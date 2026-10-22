"""Position lifecycle (T-603).

States: PENDING_ENTRY → OPEN (first entry fill) → PENDING_EXIT → CLOSED (qty ≤ min_qty) and
PENDING_ENTRY → CANCELLED (entry ended without any fill). A partial exit (TP1) returns to OPEN.
Any other transition raises ``InvalidTransition``. ``realized_pnl_tmn`` is net of *all* fees
(entry fees are charged when they occur). Every change is saved to the store.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from siavash.core.ids import new_ulid
from siavash.core.interfaces import Clock
from siavash.core.models import Candidate, Fill, Position, RiskDecision
from siavash.core.types import ExitReason, PositionStatus, Side
from siavash.logging import get_logger

log = get_logger(__name__)
ZERO = Decimal(0)
ACTIVE = frozenset({PositionStatus.PENDING_ENTRY, PositionStatus.OPEN, PositionStatus.PENDING_EXIT})


class InvalidTransition(Exception):
    pass


class PositionStore(Protocol):
    async def save_position(self, p: Position) -> None: ...
    async def load_positions(self) -> list[Position]: ...


def _d(x: float) -> Decimal:
    return Decimal(str(x))


class PositionManager:
    def __init__(self, store: PositionStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock
        self.positions: dict[str, Position] = {}
        self.planned_tmn: dict[str, Decimal] = {}
        self.pending_exit_reason: dict[str, ExitReason] = {}

    async def load(self) -> None:
        """Restore positions from the store."""
        for p in await self.store.load_positions():
            self.positions[p.position_id] = p

    async def _save(self, p: Position) -> Position:
        self.positions[p.position_id] = p
        await self.store.save_position(p)
        return p

    def get(self, position_id: str) -> Position:
        return self.positions[position_id]

    def _require(self, p: Position, *allowed: PositionStatus) -> None:
        if p.status not in allowed:
            raise InvalidTransition(f"{p.position_id}: {p.status.value} not in {[a.value for a in allowed]}")

    # ------------------------------------------------------------------ creation / entry
    async def create(self, c: Candidate, risk: RiskDecision, decision_id: str) -> Position:
        """New ``PENDING_ENTRY`` position from an approved candidate."""
        p = Position(
            position_id=new_ulid(),
            symbol=c.symbol,
            strategy=c.strategy,
            status=PositionStatus.PENDING_ENTRY,
            entry_decision_id=decision_id,
            stop_loss=_d(c.stop_loss),
            take_profit=_d(c.take_profit),
            tp1_price=_d(c.tp1_price) if c.tp1_price is not None else None,
            time_stop_at=self.clock.now() + timedelta(minutes=c.time_stop_minutes),
        )
        self.planned_tmn[p.position_id] = risk.size_tmn
        log.info(
            "position_created",
            position_id=p.position_id,
            symbol=p.symbol,
            strategy=p.strategy.value,
            size_tmn=str(risk.size_tmn),
        )
        return await self._save(p)

    async def apply_entry_fill(self, position_id: str, fill: Fill, fee_tmn: Decimal) -> Position:
        p = self.get(position_id)
        self._require(p, PositionStatus.PENDING_ENTRY, PositionStatus.OPEN)
        if fill.side is not Side.BUY:
            raise InvalidTransition("entry fill must be a BUY")
        new_qty = p.qty + fill.qty
        avg = ((p.avg_entry or ZERO) * p.qty + fill.price * fill.qty) / new_qty
        p.qty = new_qty
        p.avg_entry = avg
        p.fees_tmn += fee_tmn
        p.realized_pnl_tmn -= fee_tmn
        p.highest_price = max(p.highest_price or fill.price, fill.price)
        if fill.client_order_id not in p.order_client_ids:
            p.order_client_ids.append(fill.client_order_id)
        if p.status is PositionStatus.PENDING_ENTRY:
            p.status = PositionStatus.OPEN
            p.opened_at = fill.ts
        return await self._save(p)

    async def entry_finished(self, position_id: str) -> Position:
        """All entry orders are terminal: no fill → CANCELLED; otherwise stays OPEN."""
        p = self.get(position_id)
        self.planned_tmn.pop(position_id, None)
        if p.status is PositionStatus.PENDING_ENTRY:
            p.status = PositionStatus.CANCELLED
            p.closed_at = self.clock.now()
            return await self._save(p)
        return p

    # ------------------------------------------------------------------ exit
    async def start_exit(self, position_id: str, reason: ExitReason) -> Position:
        p = self.get(position_id)
        self._require(p, PositionStatus.OPEN)
        p.status = PositionStatus.PENDING_EXIT
        self.pending_exit_reason[position_id] = reason
        return await self._save(p)

    async def apply_exit_fill(
        self, position_id: str, fill: Fill, fee_tmn: Decimal, min_qty: Decimal = ZERO
    ) -> Position:
        p = self.get(position_id)
        self._require(p, PositionStatus.PENDING_EXIT, PositionStatus.OPEN)
        if fill.side is not Side.SELL:
            raise InvalidTransition("exit fill must be a SELL")
        if fill.qty > p.qty:
            raise InvalidTransition(f"exit qty {fill.qty} > position qty {p.qty}")
        assert p.avg_entry is not None
        p.qty -= fill.qty
        p.realized_pnl_tmn += (fill.price - p.avg_entry) * fill.qty - fee_tmn
        p.fees_tmn += fee_tmn
        if fill.client_order_id not in p.order_client_ids:
            p.order_client_ids.append(fill.client_order_id)
        reason = self.pending_exit_reason.get(position_id)
        if p.qty <= min_qty:
            p.status = PositionStatus.CLOSED
            p.closed_at = fill.ts
            p.exit_reason = reason
            self.pending_exit_reason.pop(position_id, None)
        elif reason is ExitReason.TP1_PARTIAL:
            p.tp1_done = True
        return await self._save(p)

    async def exit_finished(self, position_id: str, min_qty: Decimal = ZERO) -> Position:
        """Exit orders are terminal; leftover quantity → back to OPEN (exit can be retried)."""
        p = self.get(position_id)
        if p.status is PositionStatus.PENDING_EXIT:
            if p.qty <= min_qty:
                p.status = PositionStatus.CLOSED
                p.closed_at = self.clock.now()
                p.exit_reason = self.pending_exit_reason.pop(position_id, None)
            else:
                if self.pending_exit_reason.get(position_id) is ExitReason.TP1_PARTIAL:
                    p.tp1_done = True
                p.status = PositionStatus.OPEN
                self.pending_exit_reason.pop(position_id, None)
            return await self._save(p)
        return p

    async def update(self, p: Position) -> Position:
        """Persist an externally modified position (e.g. trailing stop / SL moves)."""
        self._require(p, *ACTIVE)
        return await self._save(p)

    # ------------------------------------------------------------------ queries
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.status in ACTIVE]

    def by_symbol(self, symbol: str) -> Position | None:
        return next((p for p in self.open_positions() if p.symbol == symbol), None)

    def exposure_tmn(self, prices: Mapping[str, Decimal]) -> Decimal:
        """Mark-to-market value of open quantities + planned size of entries not filled yet."""
        total = ZERO
        for p in self.open_positions():
            px = prices.get(p.symbol) or p.avg_entry or ZERO
            held = p.qty * px
            if p.status is PositionStatus.PENDING_ENTRY or (
                p.position_id in self.planned_tmn and p.status is PositionStatus.OPEN
            ):
                held = max(held, self.planned_tmn.get(p.position_id, ZERO))
            total += held
        return total

    def closed_since(self, since: datetime) -> list[Position]:
        return [
            p
            for p in self.positions.values()
            if p.status is PositionStatus.CLOSED and p.closed_at and p.closed_at >= since
        ]
