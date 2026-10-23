"""End-game liquidation (T-704, rule R7: profit must be realised in TMN).

Phases relative to ``risk.endgame.competition_end_utc``:
NONE (> 6 h left) → NO_NEW (≤ 6 h: no new entries) → UNWIND (≤ 3 h: exit positions, short passive
attempt then aggressive) → FORCE (≤ 1 h: aggressively sell every coin balance ≥ min qty, incl.
USDT → TMN) → DONE (after the end). Without ``competition_end_utc`` live mode refuses to start.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from siavash.core.interfaces import Broker, Clock, DecisionLogger
from siavash.core.models import Order, OrderBook
from siavash.core.types import DecisionType, ExitReason, PositionStatus
from siavash.exchange.markets import MarketRegistry, UnknownMarket
from siavash.execution.router import OrderRouter
from siavash.logging import get_logger

log = get_logger(__name__)
ZERO = Decimal(0)


class EndgameConfigError(RuntimeError):
    """``competition_end_utc`` missing while running live."""


def endgame_phase(now: datetime, cfg: Any) -> str:
    """``NONE | NO_NEW | UNWIND | FORCE | DONE`` (``NONE`` if no end time is configured)."""
    return phase_at(now, cfg.risk.endgame)


def phase_starts(eg: Any) -> dict[str, datetime]:
    """Start time (UTC) of every phase after ``NONE``; empty without ``competition_end_utc``."""
    end = eg.competition_end_utc
    if end is None:
        return {}
    return {
        "NO_NEW": end - timedelta(hours=eg.no_new_entries_hours_before),
        "UNWIND": end - timedelta(hours=eg.start_unwind_hours_before),
        "FORCE": end - timedelta(hours=eg.force_liquidate_hours_before),
        "DONE": end,
    }


def phase_at(now: datetime, eg: Any) -> str:
    """:func:`endgame_phase` for a bare ``EndgameConfig``."""
    end = eg.competition_end_utc
    if end is None:
        return "NONE"
    left = end - now
    if left <= timedelta(0):
        return "DONE"
    if left <= timedelta(hours=eg.force_liquidate_hours_before):
        return "FORCE"
    if left <= timedelta(hours=eg.start_unwind_hours_before):
        return "UNWIND"
    if left <= timedelta(hours=eg.no_new_entries_hours_before):
        return "NO_NEW"
    return "NONE"


def assert_endgame_configured(cfg: Any) -> None:
    """Live mode must know when the challenge ends."""
    if cfg.mode == "live" and cfg.risk.endgame.competition_end_utc is None:
        raise EndgameConfigError("risk.endgame.competition_end_utc is empty: live mode will not start")


@dataclass
class EndgameReport:
    ts: datetime
    phase: str
    balances: dict[str, Decimal]
    final_value_tmn: Decimal
    dust: dict[str, Decimal] = field(default_factory=dict)
    capital_base_tmn: Decimal = Decimal("5000000")

    @property
    def return_pct(self) -> float:
        return float((self.final_value_tmn - self.capital_base_tmn) / self.capital_base_tmn * 100)


BookFn = Callable[[str], Awaitable[OrderBook]]


class EndgameLiquidator:
    def __init__(
        self,
        broker: Broker,
        router: OrderRouter,
        registry: MarketRegistry,
        clock: Clock,
        cfg: Any,
        book_fn: BookFn,
        positions: Any = None,
        decision_logger: DecisionLogger | None = None,
        report_path: str | Path | None = "reports/endgame.md",
        value_fn: Callable[[str, Decimal], Awaitable[Decimal | None]] | None = None,
    ) -> None:
        self.broker = broker
        self.router = router
        self.registry = registry
        self.clock = clock
        self.cfg = cfg
        self.book_fn = book_fn
        self.positions = positions
        self.decision_logger = decision_logger
        self.report_path = Path(report_path) if report_path else None
        self.value_fn = value_fn
        self.last_phase = "NONE"

    def phase(self) -> str:
        return endgame_phase(self.clock.now(), self.cfg)

    def _record(self, phase: str, reason: str, extra: dict[str, Any]) -> str:
        from siavash.core.ids import new_ulid
        from siavash.engine.decision_log import build_record

        did = new_ulid()
        if self.decision_logger is not None:
            self.decision_logger.write(
                build_record(
                    DecisionType.ENDGAME,
                    clock=self.clock,
                    decision_id=did,
                    final_reason=reason,
                    extra={"phase": phase, **extra},
                )
            )
        return did

    async def _cancel_open(self) -> None:
        for o in await self.broker.get_open_orders():
            try:
                await self.broker.cancel_order(o.symbol, o.client_order_id)
            except Exception as e:
                log.warning("endgame_cancel_failed", client_order_id=o.client_order_id, error=str(e))

    async def run_step(self) -> list[Order]:
        """One end-game step for the current phase; returns orders sent."""
        phase = self.phase()
        if phase != self.last_phase:
            self._record(phase, f"end-game phase {self.last_phase} -> {phase}", {})
            log.warning("endgame_phase", old=self.last_phase, new=phase)
            self.last_phase = phase
        if phase == "UNWIND":
            return await self._unwind_positions()
        if phase in ("FORCE", "DONE"):
            orders = await self._force_liquidate()
            await self.write_report(phase)
            return orders
        return []

    async def _unwind_positions(self) -> list[Order]:
        if self.positions is None:
            return []
        out: list[Order] = []
        for p in list(self.positions.open_positions()):
            if p.status is not PositionStatus.OPEN or p.qty <= 0:
                continue
            did = self._record("UNWIND", f"unwind {p.symbol}", {"position_id": p.position_id})
            await self.positions.start_exit(p.position_id, ExitReason.ENDGAME)
            book = await self.book_fn(p.symbol)
            out += await self.router.sell(
                p.symbol,
                p.qty,
                did,
                ExitReason.ENDGAME,
                book,
                urgent=False,
                passive_timeout_s=self.cfg.risk.endgame.unwind_passive_timeout_s,
            )
        return out

    async def _force_liquidate(self) -> list[Order]:
        await self._cancel_open()
        balances = await self.broker.get_balances()
        out: list[Order] = []
        # coins first, USDT last (USDT/TMN is the conversion market)
        assets = sorted((a for a in balances if a != "TMN"), key=lambda a: a == "USDT")
        for asset in assets:
            qty = balances[asset].free
            sym = f"{asset}/TMN"
            try:
                m = self.registry.get(sym)
            except UnknownMarket:
                log.warning("endgame_no_tmn_market", asset=asset)
                continue
            if qty < m.min_qty or qty <= 0:
                continue
            did = self._record("FORCE", f"force-sell {qty} {asset}", {"asset": asset})
            book = await self.book_fn(sym)
            out += await self.router.sell(sym, qty, did, ExitReason.ENDGAME, book, urgent=True)
        return out

    async def report(self, phase: str | None = None) -> EndgameReport:
        balances = {a: b.total for a, b in (await self.broker.get_balances()).items()}
        value = balances.get("TMN", ZERO)
        dust: dict[str, Decimal] = {}
        for a, q in balances.items():
            if a == "TMN" or q == 0:
                continue
            dust[a] = q
            if self.value_fn is not None:
                v = await self.value_fn(a, q)
                value += v or ZERO
        return EndgameReport(
            self.clock.now(),
            phase or self.phase(),
            balances,
            value,
            dust,
            Decimal(self.cfg.risk.capital_base_tmn),
        )

    async def write_report(self, phase: str | None = None) -> EndgameReport:
        rep = await self.report(phase)
        if self.report_path is not None:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            lines = [
                "# End-game report",
                "",
                f"- time (UTC): {rep.ts.isoformat()}",
                f"- phase: {rep.phase}",
                f"- final value: {rep.final_value_tmn:,.0f} TMN",
                f"- return (official formula, base {rep.capital_base_tmn:,.0f} TMN): {rep.return_pct:.3f}%",
                "",
                "| asset | balance |",
                "|---|---|",
                *[f"| {a} | {q} |" for a, q in sorted(rep.balances.items())],
                "",
                "## Unsellable / remaining non-TMN balances (dust)",
                "",
                *([f"- {a}: {q}" for a, q in sorted(rep.dust.items())] or ["None."]),
            ]
            self.report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return rep
