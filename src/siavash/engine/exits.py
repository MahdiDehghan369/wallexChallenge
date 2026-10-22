"""Exit rules (T-604, section 4.5) as pure functions.

Priority: (1) end-game / breaker flatten → (2) stop-loss or trailing stop → (3) TP1 (S1 only,
partial) → (4) take-profit → (5) time-stop → (6) signal exit (PANIC regime and profit below
``panic_exit_min_profit_pct``; only to exit earlier, never to hold longer).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from siavash.core.models import Position, RegimeAssessment
from siavash.core.types import BreakerState, ExitReason, PositionStatus, Regime, StrategyId
from siavash.signals.fee_filter import round_trip_fee_pct


@dataclass(frozen=True)
class ExitDecision:
    reason: ExitReason
    qty_fraction: Decimal
    ref_price: Decimal
    detail: str = ""


ONE = Decimal(1)


def evaluate_exit(
    position: Position,
    price_bid: Decimal,
    now: datetime,
    breaker_state: BreakerState,
    endgame_phase: str,
    regime: RegimeAssessment | None,
    cfg: Any,
) -> ExitDecision | None:
    """Exit decision for an OPEN position, or ``None``."""
    if position.status is not PositionStatus.OPEN or position.qty <= 0:
        return None
    bid = Decimal(price_bid)
    if endgame_phase in ("UNWIND", "FORCE", "DONE"):
        return ExitDecision(ExitReason.ENDGAME, ONE, bid, f"endgame phase {endgame_phase}")
    if breaker_state is BreakerState.FLATTEN_AND_HALT:
        return ExitDecision(ExitReason.BREAKER, ONE, bid, "kill-switch: flatten and halt")
    if bid <= position.stop_loss:
        return ExitDecision(ExitReason.STOP_LOSS, ONE, bid, f"bid {bid} <= SL {position.stop_loss}")
    if position.trailing_stop is not None and bid <= position.trailing_stop:
        return ExitDecision(
            ExitReason.TRAILING_STOP, ONE, bid, f"bid {bid} <= trailing {position.trailing_stop}"
        )
    if (
        position.strategy is StrategyId.S1_TREND_BREAKOUT
        and position.tp1_price is not None
        and not position.tp1_done
        and bid >= position.tp1_price
    ):
        frac = Decimal(str(cfg.strategy.s1.tp1_fraction))
        return ExitDecision(ExitReason.TP1_PARTIAL, frac, bid, f"bid {bid} >= TP1 {position.tp1_price}")
    if bid >= position.take_profit:
        return ExitDecision(ExitReason.TAKE_PROFIT, ONE, bid, f"bid {bid} >= TP {position.take_profit}")
    if now >= position.time_stop_at:
        return ExitDecision(
            ExitReason.TIME_STOP, ONE, bid, f"time stop at {position.time_stop_at.isoformat()}"
        )
    if regime is not None and regime.regime is Regime.PANIC and position.avg_entry:
        profit_pct = (bid / position.avg_entry - 1) * 100
        threshold = Decimal(str(cfg.strategy.regime.panic_exit_min_profit_pct))
        if profit_pct < threshold:
            return ExitDecision(
                ExitReason.SIGNAL, ONE, bid, f"PANIC regime, profit {profit_pct:.2f}% < {threshold}%"
            )
    return None


def breakeven_stop(position: Position, cfg: Any) -> Decimal:
    """Entry price plus round-trip fees (used as the new stop after TP1)."""
    assert position.avg_entry is not None
    return position.avg_entry * (1 + Decimal(str(round_trip_fee_pct(cfg))) / 100)


def after_tp1(position: Position, cfg: Any) -> Position:
    """After the TP1 partial exit: mark ``tp1_done`` and move the stop to break-even + fees (never down)."""
    p = position.model_copy(deep=True)
    p.tp1_done = True
    p.stop_loss = max(p.stop_loss, breakeven_stop(p, cfg))
    return p


def update_trailing(position: Position, price_bid: Decimal, atr: float | None, cfg: Any) -> Position:
    """S1 trailing stop after TP1: ``highest − trail_atr × ATR``, only ever moving up."""
    p = position.model_copy(deep=True)
    bid = Decimal(price_bid)
    p.highest_price = max(p.highest_price or bid, bid)
    if p.strategy is not StrategyId.S1_TREND_BREAKOUT or not p.tp1_done or not atr or atr <= 0:
        return p
    candidate = p.highest_price - Decimal(str(cfg.strategy.s1.trail_atr)) * Decimal(str(atr))
    if p.trailing_stop is None or candidate > p.trailing_stop:
        p.trailing_stop = candidate
    return p
