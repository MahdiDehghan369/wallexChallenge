"""Orchestrator (T-601): the single engine used by backtest, replay, paper and live.

Concurrent jobs, all waiting only through ``clock.sleep`` (so ``SimClock`` can drive backtests):
- ``price_guard`` every ``loop.price_guard_s``: trailing update + ``evaluate_exit`` for open positions;
- ``main_loop`` every ``loop.main_loop_s``: equity, breakers, end-game step, operator flags,
  scheduled reconcile / regime / universe refresh;
- live-data polling (candle closes → ``pipeline.process_symbol``) and the order tracker;
- entries and exits run as separate non-blocking tasks.
Fills reach positions through tracker callbacks (``TrackMeta.position_id``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from siavash.core.interfaces import Broker, Clock, DecisionLogger, MarketData
from siavash.core.models import Candle, Fill, Order, OrderBook, PortfolioSnapshot, Position, RegimeAssessment
from siavash.core.types import DecisionType, ExitReason, PositionStatus, Side, Timeframe
from siavash.engine.exits import ExitDecision, after_tp1, evaluate_exit, update_trailing
from siavash.engine.positions import PositionManager
from siavash.exchange.markets import MarketRegistry
from siavash.exchange.rate_limit import ErrorCounter
from siavash.execution.reconcile import reconcile
from siavash.execution.router import OrderRouter
from siavash.execution.tracker import OrderTracker, TrackMeta
from siavash.features import indicators as ind
from siavash.logging import get_logger
from siavash.reporting.equity import EquityTracker
from siavash.risk.breakers import BreakerController
from siavash.risk.endgame import EndgameLiquidator
from siavash.risk.limits import CooldownTracker, EntryCounter
from siavash.runtime import get_runtime

log = get_logger(__name__)
ZERO = Decimal(0)


class PipelineLike(Protocol):
    """What the orchestrator needs from a decision pipeline (``DecisionPipeline`` or ``StubPipeline``)."""

    async def process_symbol(self, symbol: str, ts: datetime) -> Any: ...
    async def process_exit(self, position: Position, decision: ExitDecision) -> None: ...


@dataclass
class App:
    """All components for one run; built by ``engine.factory.build_app``."""

    cfg: Any
    mode: str
    clock: Clock
    market_data: MarketData
    broker: Broker
    registry: MarketRegistry
    guard: Any
    live_data: Any
    store: Any
    positions: PositionManager
    tracker: OrderTracker
    router: OrderRouter
    equity: EquityTracker
    breakers: BreakerController
    endgame: EndgameLiquidator
    cooldowns: CooldownTracker
    entries: EntryCounter
    decision_logger: DecisionLogger
    error_counter: ErrorCounter
    universe: list[str] = field(default_factory=list)
    heartbeat_path: Path | None = None
    regime: RegimeAssessment | None = None
    latency: Any = None  # reporting.latency.LatencyRecorder
    alerts: Any = None  # ops.alerts.Alerts (callable ``alerts(kind, data)``)

    def alert(self, kind: str, data: dict[str, Any]) -> None:
        if self.alerts is not None:
            self.alerts(kind, data)


class Orchestrator:
    def __init__(self, app: App, pipeline: PipelineLike | None = None) -> None:
        self.app = app
        self.pipeline = pipeline
        self.cfg = app.cfg
        self.clock = app.clock
        self.stop_event = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._jobs: list[asyncio.Task[Any]] = []
        self.decision_positions: dict[str, str] = {}
        self._atr_cache: dict[str, tuple[datetime, float | None]] = {}
        self._last_reconcile: datetime | None = None
        self._last_regime: datetime | None = None
        self._last_universe: datetime | None = None
        self._flattened = False
        self._paused = False
        self.started = False
        self._decision_ts: dict[str, datetime] = {}
        self._exit_trigger: dict[str, datetime] = {}
        self._first_fill_seen: set[str] = set()
        app.router.latency = app.latency
        app.router.on_order = self._on_order
        app.tracker.on_fill.append(self._on_fill)
        app.tracker.on_order_terminal.append(self._on_terminal)
        app.live_data.subscribe(self._on_candle)

    # ------------------------------------------------------------------ helpers
    def spawn(self, coro: Awaitable[Any], name: str = "task") -> asyncio.Task[Any]:
        """Run ``coro`` as a tracked background task (errors are logged, never crash the engine)."""

        async def guarded() -> Any:
            try:
                return await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("task_failed", task=name, error=str(e))
                return None

        t = asyncio.ensure_future(guarded())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def entries_allowed(self) -> bool:
        return self.app.breakers.allows_entries and not self._paused and self.app.endgame.phase() == "NONE"

    async def book(self, symbol: str) -> OrderBook:
        b = self.app.live_data.get_book(symbol)
        if (
            b is not None
            and (self.clock.now() - b.ts).total_seconds() <= self.cfg.base.loop.book_poll_seconds * 3
        ):
            return b
        return await self.app.market_data.get_orderbook(symbol)

    async def prices(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for p in self.app.positions.open_positions():
            b = self.app.live_data.get_book(p.symbol)
            if b is not None and b.best_bid is not None:
                out[p.symbol] = b.best_bid
        return out

    async def portfolio_snapshot(self) -> PortfolioSnapshot:
        """Latest snapshot for the risk layer (exposure includes pending entries)."""
        a = self.app
        if a.equity.last is None:
            await a.equity.update()
        now = self.clock.now()
        exposure = a.positions.exposure_tmn(await self.prices())
        return a.equity.snapshot(
            a.positions.open_positions(),
            a.entries.last_hour(now),
            a.entries.since(a.equity.day_start()),
            a.breakers.state,
            exposure,
        )

    def atr15(self, symbol: str) -> float | None:
        df = self.app.live_data.candles_df(symbol, Timeframe.M15)
        if len(df) < 20:
            return None
        last_ts = df["ts_open"].iloc[-1].to_pydatetime()
        cached = self._atr_cache.get(symbol)
        if cached and cached[0] == last_ts:
            return cached[1]
        v = ind.atr(df, 14).iloc[-1]
        val = None if v != v else float(v)
        self._atr_cache[symbol] = (last_ts, val)
        return val

    # ------------------------------------------------------------------ tracker / router wiring
    async def _on_order(self, order: Order, decision_id: str, leg: str) -> None:
        pid = self.decision_positions.get(decision_id)
        strategy = None
        if pid and pid in self.app.positions.positions:
            strategy = self.app.positions.get(pid).strategy.value
        meta = TrackMeta(decision_id, pid, strategy)
        self.app.tracker.track(order, meta)
        t0 = self._decision_ts.pop(decision_id, None)
        if t0 is not None and self.app.latency is not None:
            self.app.latency.record(
                "decision_to_order_sent", (self.clock.now() - t0).total_seconds() * 1000, symbol=order.symbol
            )
        await self.app.store.save_order(order, {"decision_id": decision_id, "position_id": pid, "leg": leg})

    async def _on_fill(self, fill: Fill, fee_tmn: Decimal, meta: TrackMeta | None) -> None:
        pm = self.app.positions
        if meta is None or meta.position_id is None or meta.position_id not in pm.positions:
            log.info("fill_without_position", client_order_id=fill.client_order_id)
            return
        p = pm.get(meta.position_id)
        lat = self.app.latency
        if lat is not None and fill.client_order_id not in self._first_fill_seen:
            self._first_fill_seen.add(fill.client_order_id)
            order = self.app.tracker.tracked.get(fill.client_order_id)
            if order is not None:
                lat.record(
                    "order_sent_to_first_fill",
                    (fill.ts - order.created_at).total_seconds() * 1000,
                    symbol=fill.symbol,
                )
        if lat is not None and fill.side is Side.SELL and p.position_id in self._exit_trigger:
            t0 = self._exit_trigger.pop(p.position_id)
            lat.record("exit_trigger_to_exit_fill", (fill.ts - t0).total_seconds() * 1000, symbol=fill.symbol)
        min_qty = self.app.registry.get(p.symbol).min_qty if p.symbol in self.app.registry else ZERO
        if fill.side is Side.BUY:
            await pm.apply_entry_fill(p.position_id, fill, fee_tmn)
        else:
            p = await pm.apply_exit_fill(p.position_id, fill, fee_tmn, min_qty)
            if p.status is PositionStatus.CLOSED:
                self.app.cooldowns.record_exit(p.symbol, fill.ts, p.realized_pnl_tmn)
                await self.app.store.kv_set("cooldowns", self.app.cooldowns.to_dict())
                log.info(
                    "position_closed",
                    position_id=p.position_id,
                    symbol=p.symbol,
                    pnl_tmn=str(p.realized_pnl_tmn),
                    reason=p.exit_reason.value if p.exit_reason else None,
                )
                self.app.alert(
                    "exit",
                    {
                        "symbol": p.symbol,
                        "pnl_tmn": str(round(p.realized_pnl_tmn)),
                        "reason": p.exit_reason.value if p.exit_reason else None,
                    },
                )

    async def _on_terminal(self, order: Order, meta: TrackMeta | None) -> None:
        await self.app.store.save_order(
            order,
            {
                "decision_id": meta.decision_id if meta else None,
                "position_id": meta.position_id if meta else None,
            },
        )

    # ------------------------------------------------------------------ execution helpers (used by pipelines)
    def execute_entry(
        self, position: Position, qty: Decimal, book: OrderBook, still_valid: Callable[[], Awaitable[bool]]
    ) -> asyncio.Task[Any]:
        """Start the entry for a ``PENDING_ENTRY`` position in a background task."""
        a = self.app
        self.decision_positions[position.entry_decision_id] = position.position_id
        self._decision_ts[position.entry_decision_id] = self.clock.now()
        a.entries.record(self.clock.now())
        a.alert("entry", {"symbol": position.symbol, "strategy": position.strategy.value, "qty": str(qty)})
        self.spawn(a.store.kv_set("entries", a.entries.to_list()), "persist_entries")

        async def run() -> None:
            try:
                await a.router.enter(position, qty, book, still_valid)
            finally:
                await a.tracker.poll_once()
                await a.positions.entry_finished(position.position_id)

        return self.spawn(run(), f"entry:{position.symbol}")

    def execute_exit(
        self, position: Position, decision: ExitDecision, decision_id: str
    ) -> asyncio.Task[Any] | None:
        """Start an exit (full or partial) for an OPEN position in a background task."""
        a = self.app
        p = a.positions.get(position.position_id)
        if p.status is not PositionStatus.OPEN or p.qty <= 0:
            return None
        min_qty = a.registry.get(p.symbol).min_qty if p.symbol in a.registry else ZERO
        qty = (
            a.registry.round_qty(p.symbol, p.qty * decision.qty_fraction) if p.symbol in a.registry else p.qty
        )
        reason = decision.reason
        if decision.qty_fraction < 1 and (qty <= 0 or p.qty - qty < min_qty):
            qty = p.qty
            if reason is ExitReason.TP1_PARTIAL:
                reason = ExitReason.TAKE_PROFIT
        self.decision_positions[decision_id] = p.position_id
        self._exit_trigger[p.position_id] = self.clock.now()

        async def run() -> None:
            await a.positions.start_exit(p.position_id, reason)
            try:
                book = await self.book(p.symbol)
                target = p.tp1_price if reason is ExitReason.TP1_PARTIAL else None
                await a.router.exit(p, qty, reason, book, decision_id=decision_id, target_price=target)
            finally:
                await a.tracker.poll_once()
                done = await a.positions.exit_finished(p.position_id, min_qty)
                if reason is ExitReason.TP1_PARTIAL and done.status is PositionStatus.OPEN:
                    await a.positions.update(after_tp1(done, self.cfg))

        return self.spawn(run(), f"exit:{p.symbol}:{reason.value}")

    # ------------------------------------------------------------------ jobs
    async def _on_candle(self, symbol: str, tf: Timeframe, candle: Candle) -> None:
        if tf is not Timeframe.M15 or self.pipeline is None or symbol not in self.app.universe:
            return
        ts = candle.ts_open + timedelta(seconds=tf.seconds)
        self.spawn(self.pipeline.process_symbol(symbol, ts), f"pipeline:{symbol}")

    async def price_guard_tick(self) -> None:
        a = self.app
        now = self.clock.now()
        phase = a.endgame.phase()
        for p in list(a.positions.open_positions()):
            if p.status is not PositionStatus.OPEN or p.qty <= 0:
                continue
            try:
                book = await self.book(p.symbol)
            except Exception as e:
                log.warning("price_guard_book_failed", symbol=p.symbol, error=str(e))
                continue
            if book.best_bid is None:
                continue
            updated = update_trailing(p, book.best_bid, self.atr15(p.symbol), self.cfg)
            if updated.trailing_stop != p.trailing_stop or updated.highest_price != p.highest_price:
                p = await a.positions.update(updated)
            decision = evaluate_exit(p, book.best_bid, now, a.breakers.state, phase, a.regime, self.cfg)
            if decision is None:
                continue
            if self.pipeline is not None:
                await self.pipeline.process_exit(p, decision)
            else:
                from siavash.core.ids import new_ulid

                self.execute_exit(p, decision, new_ulid())

    async def main_tick(self) -> None:
        a = self.app
        now = self.clock.now()
        await self._operator_flags()
        try:
            await a.equity.update()
        except Exception as e:
            log.warning("equity_failed", error=str(e))
        if a.equity.last is not None:
            snap = await self.portfolio_snapshot()
            stale = any(a.live_data.is_stale(s) for s in a.universe)
            await a.breakers.evaluate(snap, a.error_counter, stale)
        await a.endgame.run_step()
        loop = self.cfg.base.loop
        if self._last_reconcile is None or now - self._last_reconcile >= timedelta(
            minutes=loop.reconcile_interval_min
        ):
            self._last_reconcile = now
            await reconcile(
                a.broker, a.store, a.positions, a.registry, self.clock, a.decision_logger, alert=a.alerts
            )
        if self.pipeline is not None and hasattr(self.pipeline, "update_regime"):
            if self._last_regime is None or now - self._last_regime >= timedelta(
                minutes=loop.analyst_interval_min
            ):
                self._last_regime = now
                reg = await self.pipeline.update_regime(now)
                if reg is not None:
                    a.regime = reg
        if self.pipeline is not None and hasattr(self.pipeline, "update_universe"):
            if self._last_universe is None or now - self._last_universe >= timedelta(
                hours=self.cfg.strategy.universe.refresh_hours
            ):
                self._last_universe = now
                await self.pipeline.update_universe(now)
        a.live_data.set_book_symbols(p.symbol for p in a.positions.open_positions())
        a.entries.prune(now)

    async def _operator_flags(self) -> None:
        a = self.app
        self._paused = bool(await a.store.kv_get("operator_pause", False))
        if await a.store.kv_get("operator_flatten", False) and not self._flattened:
            self._flattened = True
            await a.store.kv_set("operator_flatten", False)
            from siavash.core.ids import new_ulid

            for p in a.positions.open_positions():
                if p.status is PositionStatus.OPEN:
                    d = ExitDecision(ExitReason.OPERATOR, Decimal(1), ZERO, "operator flatten")
                    self.execute_exit(p, d, new_ulid())
        elif not await a.store.kv_get("operator_flatten", False):
            self._flattened = False

    async def _write_heartbeat(self) -> None:
        if self.app.heartbeat_path is None:
            return
        from siavash.engine.watchdog import write_heartbeat

        write_heartbeat(self.app.heartbeat_path, self.clock.now().isoformat())

    async def _loop(self, interval: float, fn: Callable[[], Awaitable[None]], name: str) -> None:
        while not self.stop_event.is_set():
            try:
                await fn()
            except Exception as e:
                log.exception("job_failed", job=name, error=str(e))
            await self.clock.sleep(interval)

    # ------------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        a = self.app
        await a.store.open()
        await a.positions.load()
        await a.breakers.load()
        a.cooldowns.load_dict(await a.store.kv_get("cooldowns"))
        a.entries.load_list(await a.store.kv_get("entries"))
        await a.registry.refresh_if_stale()
        for o, meta in await a.store.load_orders():
            if not o.status.is_terminal:
                a.tracker.track(o, TrackMeta(meta.get("decision_id") or "", meta.get("position_id"), None))
        if a.alerts is not None and hasattr(a.alerts, "start"):
            a.alerts.start()
        await reconcile(
            a.broker, a.store, a.positions, a.registry, self.clock, a.decision_logger, alert=a.alerts
        )
        self._last_reconcile = self.clock.now()
        rt = get_runtime()
        from siavash.engine.decision_log import build_record

        a.decision_logger.write(
            build_record(
                DecisionType.OPERATOR_COMMAND,
                clock=self.clock,
                final_reason=f"startup mode={a.mode}",
                extra={
                    "kind": "startup",
                    "git_sha": rt.git_sha,
                    "version_tag": rt.version_tag,
                    "config_hash": rt.config_hash,
                    "universe": a.universe,
                },
            )
        )
        log.info(
            "startup",
            mode=a.mode,
            git_sha=rt.git_sha,
            version_tag=rt.version_tag,
            config_hash=rt.config_hash,
            universe=a.universe,
        )
        a.alert("startup", {"mode": a.mode, "git_sha": rt.git_sha, "version_tag": rt.version_tag})
        a.live_data.set_symbols(self._data_symbols())
        await a.live_data.bootstrap()
        await a.equity.update()
        self.started = True

    def _data_symbols(self) -> list[str]:
        from siavash.data.universe import data_symbols

        avail = [m.symbol for m in self.app.registry.all()] or None
        return data_symbols(self.app.universe, self.cfg, avail)

    def set_universe(self, symbols: list[str]) -> None:
        self.app.universe = list(symbols)
        self.app.live_data.set_symbols(self._data_symbols())

    async def run(self) -> None:
        """Start every job and wait until :meth:`shutdown` (or ``stop_event``)."""
        if not self.started:
            await self.startup()
        loop = self.cfg.base.loop
        a = self.app
        self._jobs = [
            asyncio.ensure_future(self._loop(loop.price_guard_s, self.price_guard_tick, "price_guard")),
            asyncio.ensure_future(self._loop(loop.main_loop_s, self.main_tick, "main_loop")),
            asyncio.ensure_future(self._loop(loop.heartbeat_s, self._write_heartbeat, "heartbeat")),
            asyncio.ensure_future(a.live_data.run(self.stop_event)),
            asyncio.ensure_future(a.tracker.run(self.stop_event)),
        ]
        if hasattr(a.broker, "run"):
            self._jobs.append(asyncio.ensure_future(a.broker.run(self.stop_event)))
        watchdog = None
        if a.heartbeat_path is not None and a.mode in ("paper", "live"):
            from siavash.engine.watchdog import Watchdog

            await self._write_heartbeat()
            watchdog = Watchdog(a.heartbeat_path, loop.watchdog_timeout_s)
            watchdog.start()
        await self.stop_event.wait()
        if watchdog is not None:
            watchdog.stop()
        await self._teardown()

    async def shutdown(self) -> None:
        """Request a clean stop (no new entries, cancel resting entry orders, persist state)."""
        self.stop_event.set()

    async def _teardown(self) -> None:
        a = self.app
        for o in await a.broker.get_open_orders():
            if o.side is Side.BUY:
                try:
                    await a.broker.cancel_order(o.symbol, o.client_order_id)
                except Exception as e:
                    log.warning("shutdown_cancel_failed", client_order_id=o.client_order_id, error=str(e))
        for t in list(self._tasks):
            t.cancel()
        for j in self._jobs:
            j.cancel()
        await asyncio.gather(*self._jobs, *self._tasks, return_exceptions=True)
        await a.store.kv_set("cooldowns", a.cooldowns.to_dict())
        await a.store.kv_set("entries", a.entries.to_list())
        log.info("shutdown", open_positions=len(a.positions.open_positions()))
        if a.alerts is not None and hasattr(a.alerts, "stop"):
            await a.alerts.stop()
        await a.store.close()


def install_signal_handlers(orch: Orchestrator) -> None:
    """SIGTERM / SIGINT → clean shutdown (POSIX only)."""
    import signal

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(orch.shutdown()))
        except (NotImplementedError, RuntimeError):
            pass
