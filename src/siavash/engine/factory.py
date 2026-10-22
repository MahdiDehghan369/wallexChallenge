"""Build the engine for a mode (T-601).

LIVE → ``LiveBroker`` + ``WallexMarketData``; PAPER → ``PaperBroker`` + ``WallexMarketData``;
BACKTEST / REPLAY → ``BacktestBroker`` + ``HistoricalMarketData`` (injected by the backtest engine).
Decision components (signals, scorer, regime, reviewer, risk) are identical in every mode: the
decision pipeline is created by ``pipeline_factory(app, orchestrator)``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from siavash.core.interfaces import Broker, Clock, MarketData
from siavash.data.live import LiveDataService
from siavash.engine.clock import SystemClock
from siavash.engine.decision_log import FillsCsvLogger, JsonlDecisionLogger, OrdersLogger
from siavash.engine.orchestrator import App, Orchestrator, PipelineLike
from siavash.engine.positions import PositionManager
from siavash.engine.store import InMemoryStore, SqliteStore
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.exchange.rate_limit import ErrorCounter
from siavash.execution.router import OrderRouter
from siavash.execution.safe_submit import SafeSubmitter
from siavash.execution.tracker import OrderTracker
from siavash.reporting.equity import EquityTracker
from siavash.risk.breakers import BreakerController
from siavash.risk.endgame import EndgameLiquidator, assert_endgame_configured, endgame_phase
from siavash.risk.limits import CooldownTracker, EntryCounter

PipelineFactory = Callable[[App, Orchestrator], PipelineLike]


def assemble(
    cfg: Any,
    mode: str,
    clock: Clock,
    market_data: MarketData,
    broker: Broker,
    registry: MarketRegistry,
    guard: Any,
    store: Any,
    logs_dir: str | Path,
    reports_dir: str | Path,
    error_counter: ErrorCounter | None = None,
    live_data: Any = None,
    universe: list[str] | None = None,
    heartbeat_path: Path | None = None,
) -> App:
    """Wire the mode-independent components around a given broker / market data."""
    logs = Path(logs_dir)
    error_counter = error_counter or ErrorCounter(time_fn=lambda: clock.now().timestamp())
    decision_logger = JsonlDecisionLogger(logs / "decisions.jsonl")
    if live_data is None:
        live_data = LiveDataService.from_config(cfg, market_data, clock, universe or [])
        if mode in ("backtest", "replay"):
            live_data.store_dir = None
    positions = PositionManager(store, clock)
    tracker = OrderTracker(
        broker,
        clock,
        FillsCsvLogger(logs / "fills.csv"),
        price_fn=None,
        poll_seconds=cfg.base.execution.order_poll_seconds,
    )

    async def book_fn(symbol: str) -> Any:
        return await market_data.get_orderbook(symbol)

    router = OrderRouter(
        broker, registry, clock, cfg, book_fn=book_fn, submit=SafeSubmitter.from_config(cfg, clock)
    )
    equity = EquityTracker(
        broker, EquityTracker.bid_fn_from_live(live_data, market_data), store, clock, cfg, logs / "equity.csv"
    )
    breakers = BreakerController(store, cfg, clock, decision_logger=decision_logger)
    endgame = EndgameLiquidator(
        broker,
        router,
        registry,
        clock,
        cfg,
        book_fn,
        positions=positions,
        decision_logger=decision_logger,
        report_path=Path(reports_dir) / "endgame.md",
        value_fn=equity.value_of,
    )
    return App(
        cfg=cfg,
        mode=mode,
        clock=clock,
        market_data=market_data,
        broker=broker,
        registry=registry,
        guard=guard,
        live_data=live_data,
        store=store,
        positions=positions,
        tracker=tracker,
        router=router,
        equity=equity,
        breakers=breakers,
        endgame=endgame,
        cooldowns=CooldownTracker(cfg),
        entries=EntryCounter(),
        decision_logger=decision_logger,
        error_counter=error_counter,
        universe=list(universe or []),
        heartbeat_path=heartbeat_path,
    )


def build_app(
    cfg: Any,
    mode: str | None = None,
    *,
    config_dir: str | Path = "config",
    clock: Clock | None = None,
    market_data: MarketData | None = None,
    broker: Broker | None = None,
    store: Any = None,
    universe: list[str] | None = None,
) -> App:
    """Create the ``App`` for ``mode`` (default ``cfg.mode``)."""
    mode = mode or cfg.mode
    paths = cfg.base.paths
    if mode in ("backtest", "replay"):
        if market_data is None or broker is None or clock is None:
            raise ValueError(f"{mode}: market_data, broker and clock must be injected by the backtest engine")
        registry = MarketRegistry(market_data.list_markets, clock)
        guard = SymbolGuard.from_config(cfg, registry, config_dir)
        return assemble(
            cfg,
            mode,
            clock,
            market_data,
            broker,
            registry,
            guard,
            store or InMemoryStore(),
            paths.logs,
            paths.reports,
            universe=universe,
        )

    from siavash.exchange.client import WallexHttp
    from siavash.exchange.market_data import WallexMarketData

    clock = clock or SystemClock()
    if mode == "live":
        assert_endgame_configured(cfg)
    error_counter = ErrorCounter()
    http = WallexHttp.from_config(cfg, error_counter)
    md = market_data or WallexMarketData(http, clock, ticker_cache_s=cfg.base.exchange.ticker_cache_s)
    registry = MarketRegistry(md.list_markets, clock, cfg.strategy.universe.refresh_hours)
    guard = SymbolGuard.from_config(cfg, registry, config_dir)
    store = store or SqliteStore(Path(paths.state) / "state.sqlite")
    live_data = LiveDataService.from_config(cfg, md, clock, universe or [])
    if broker is None:
        if mode == "live":
            from siavash.exchange.account import OrderIdMap, WallexAccount
            from siavash.execution.live_broker import LiveBroker

            account = WallexAccount(
                http, registry, OrderIdMap(Path(paths.state) / "id_map.sqlite"), guard, clock
            )
            broker = LiveBroker(account, OrdersLogger(Path(paths.logs) / "orders.jsonl", clock))
        elif mode == "paper":
            from siavash.execution.paper_broker import PaperBroker

            broker = PaperBroker.from_config(cfg, md, guard, registry, clock, live_data)
        else:
            raise ValueError(f"unknown mode {mode!r}")
    return assemble(
        cfg,
        mode,
        clock,
        md,
        broker,
        registry,
        guard,
        store,
        paths.logs,
        paths.reports,
        error_counter,
        live_data,
        universe,
        Path(paths.state) / "heartbeat",
    )


__all__ = ["PipelineFactory", "assemble", "build_app", "endgame_phase"]
