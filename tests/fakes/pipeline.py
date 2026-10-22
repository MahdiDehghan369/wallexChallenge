"""StubPipeline (T-601 tests): an entry on BTC/TMN every 8th closed 15m candle with fixed SL/TP."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from siavash.core.ids import new_ulid
from siavash.core.models import Candidate, Position, RiskDecision
from siavash.core.types import DecisionType, StrategyId
from siavash.engine.decision_log import build_record
from siavash.engine.exits import ExitDecision


class StubPipeline:
    def __init__(
        self,
        orch: Any,
        symbol: str = "BTC/TMN",
        every: int = 8,
        size_tmn: Decimal = Decimal("500000"),
        sl_pct: float = 1.0,
        tp_pct: float = 1.0,
        time_stop_min: int = 240,
    ) -> None:
        self.orch = orch
        self.symbol = symbol
        self.every = every
        self.size_tmn = size_tmn
        self.sl_pct, self.tp_pct, self.time_stop_min = sl_pct, tp_pct, time_stop_min
        self.n = 0
        self.entries = 0
        self.exits = 0

    async def process_symbol(self, symbol: str, ts: datetime) -> None:
        if symbol != self.symbol:
            return
        self.n += 1
        app = self.orch.app
        if self.n % self.every or not self.orch.entries_allowed() or app.positions.by_symbol(symbol):
            return
        book = await self.orch.book(symbol)
        if book.best_ask is None:
            return
        price = float(book.best_ask)
        c = Candidate(
            candidate_id=new_ulid(),
            ts=ts,
            symbol=symbol,
            strategy=StrategyId.S3_IMPLIED_DEV,
            entry_ref_price=price,
            stop_loss=price * (1 - self.sl_pct / 100),
            take_profit=price * (1 + self.tp_pct / 100),
            time_stop_minutes=self.time_stop_min,
            expected_move_pct=self.tp_pct,
            conditions={"stub": True},
        )
        qty = app.registry.max_qty_for_notional(symbol, self.size_tmn, book.best_ask)
        risk = RiskDecision(
            approved=True, size_tmn=qty * book.best_ask, qty=qty, equity_tmn=Decimal("5000000")
        )
        rec = build_record(
            DecisionType.ENTRY_CANDIDATE,
            clock=app.clock,
            final_reason="stub entry",
            outcome_action="ORDER_PLACED",
            symbol=symbol,
            strategy=c.strategy,
            signal=c,
            risk=risk,
        )
        p = await app.positions.create(c, risk, rec.decision_id)
        app.decision_logger.write(rec.model_copy(update={"position_id": p.position_id}))
        self.entries += 1

        async def valid() -> bool:
            return True

        self.orch.execute_entry(p, qty, book, valid)

    async def process_exit(self, position: Position, decision: ExitDecision) -> None:
        app = self.orch.app
        rec = build_record(
            DecisionType.EXIT,
            clock=app.clock,
            final_reason=f"exit {decision.reason.value}",
            outcome_action="EXITED",
            symbol=position.symbol,
            strategy=position.strategy,
            position_id=position.position_id,
            exit_reason=decision.reason,
        )
        app.decision_logger.write(rec)
        self.exits += 1
        self.orch.execute_exit(position, decision, rec.decision_id)
