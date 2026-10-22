"""DefaultRiskManager (T-702): the hard veto layer the AI cannot bypass.

Checks in order (all failures are collected; any failure → not approved): SymbolGuard → breaker →
end-game → PANIC regime → max concurrent positions → market already open → loss cooldown → market
cooldown → hourly / daily trade caps → spread → stale data → opposite order open → size (T-701) →
exposure / cash / minimum notional.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from siavash.core.interfaces import Clock
from siavash.core.models import (
    Candidate,
    MarketInfo,
    MLScore,
    OrderBook,
    PortfolioSnapshot,
    RegimeAssessment,
    RiskDecision,
    TradeReview,
)
from siavash.core.types import BreakerState, Regime, Side
from siavash.exchange.markets import MarketRegistry
from siavash.risk import codes
from siavash.risk.limits import CooldownTracker
from siavash.risk.sizing import position_size_tmn, size_to_qty

ZERO = Decimal(0)


class DefaultRiskManager:
    def __init__(
        self,
        cfg: Any,
        guard: Any,
        clock: Clock,
        cooldowns: CooldownTracker,
        endgame_phase: Callable[[datetime], str] | None = None,
        is_stale: Callable[[str], bool] | None = None,
        open_order_sides: Callable[[str], list[Side]] | None = None,
    ) -> None:
        self.cfg = cfg
        self.guard = guard
        self.clock = clock
        self.cooldowns = cooldowns
        self.endgame_phase = endgame_phase or (lambda _now: "NONE")
        self.is_stale = is_stale or (lambda _s: False)
        self.open_order_sides = open_order_sides or (lambda _s: [])

    def evaluate(
        self,
        c: Candidate,
        ml: MLScore,
        review: TradeReview | None,
        regime: RegimeAssessment,
        portfolio: PortfolioSnapshot,
        market: MarketInfo,
        book: OrderBook,
    ) -> RiskDecision:
        r = self.cfg.risk
        now = self.clock.now()
        failed: list[str] = []
        ok, _ = self.guard.is_tradable(c.symbol, Side.BUY)
        if not ok:
            failed.append(codes.RISK_SYMBOL_NOT_ALLOWED)
        if portfolio.breaker_state is not BreakerState.NORMAL:
            failed.append(codes.RISK_BREAKER_ACTIVE)
        if self.endgame_phase(now) != "NONE":
            failed.append(codes.RISK_ENDGAME)
        if regime.regime is Regime.PANIC or regime.risk_multiplier <= 0:
            failed.append(codes.RISK_REGIME_PANIC)
        if len(portfolio.open_positions) >= r.max_concurrent_positions:
            failed.append(codes.RISK_MAX_POSITIONS)
        if any(p.symbol == c.symbol for p in portfolio.open_positions):
            failed.append(codes.RISK_MARKET_ALREADY_OPEN)
        if self.cooldowns.losses_active(now):
            failed.append(codes.RISK_COOLDOWN_LOSSES)
        if self.cooldowns.market_active(c.symbol, now):
            failed.append(codes.RISK_COOLDOWN_MARKET)
        if portfolio.trades_last_hour >= r.max_trades_per_hour:
            failed.append(codes.RISK_MAX_TRADES_HOUR)
        if portfolio.trades_today >= r.max_trades_per_day:
            failed.append(codes.RISK_MAX_TRADES_DAY)
        spread = book.spread_pct
        if spread is None or spread > r.max_spread_pct:
            failed.append(codes.RISK_SPREAD_TOO_WIDE)
        if self.is_stale(c.symbol):
            failed.append(codes.RISK_STALE_DATA)
        if Side.SELL in self.open_order_sides(c.symbol):
            failed.append(codes.RISK_OPPOSITE_ORDER_OPEN)

        equity = portfolio.equity_tmn
        free_exposure = Decimal(str(r.max_total_exposure_pct)) / 100 * equity - portfolio.exposure_tmn
        entry = book.best_ask if book.best_ask is not None else Decimal(str(c.entry_ref_price))
        stop = Decimal(str(c.stop_loss))
        llm_mult = review.size_multiplier if review is not None else 1.0
        size = position_size_tmn(
            equity,
            entry,
            stop,
            free_exposure,
            portfolio.cash_tmn,
            regime.risk_multiplier,
            llm_mult,
            self.cfg,
            self.cfg.mode,
        )
        qty = size_to_qty(size, entry, c.symbol, MarketRegistry.from_markets([market])) if size > 0 else ZERO
        if free_exposure <= 0:
            failed.append(codes.RISK_MAX_EXPOSURE)
        if portfolio.cash_tmn * Decimal(str(r.cash_buffer_fraction)) < market.min_notional:
            failed.append(codes.RISK_INSUFFICIENT_CASH)
        if size <= 0:
            failed.append(codes.RISK_SIZE_ZERO)
        elif qty <= 0:
            failed.append(codes.RISK_BELOW_MIN_NOTIONAL)
        approved = not failed
        return RiskDecision(
            approved=approved,
            size_tmn=(qty * entry) if approved else ZERO,
            qty=qty if approved else ZERO,
            checks_failed=list(dict.fromkeys(failed)),
            equity_tmn=equity,
        )
