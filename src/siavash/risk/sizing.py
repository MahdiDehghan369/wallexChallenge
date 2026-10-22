"""Position sizing (T-701, section 5).

``size = min(risk_per_trade% × equity / SL%, max_position% × equity, free_exposure, cash × buffer)
× regime_mult × llm_mult × (initial_live_risk_scale if live)``, then converted to a step-aligned qty;
below the market minimum the size is zero. Multipliers are clamped so they can only *reduce* size.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from siavash.exchange.markets import MarketRegistry

ZERO = Decimal(0)
HUNDRED = Decimal(100)


def _clamp(x: float, lo: float, hi: float) -> Decimal:
    return Decimal(str(min(max(x, lo), hi)))


def position_size_tmn(
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    free_exposure_tmn: Decimal,
    cash_tmn: Decimal,
    regime_mult: float,
    llm_mult: float,
    cfg: Any,
    mode: str = "paper",
) -> Decimal:
    """Order size in TMN (never negative, never above caps)."""
    r = cfg.risk
    if equity <= 0 or entry <= 0 or stop <= 0 or stop >= entry:
        return ZERO
    sl_frac = (entry - stop) / entry
    by_risk = Decimal(str(r.risk_per_trade_pct)) / HUNDRED * equity / sl_frac
    cap = Decimal(str(r.max_position_pct)) / HUNDRED * equity
    cash_cap = max(cash_tmn, ZERO) * Decimal(str(r.cash_buffer_fraction))
    base = min(by_risk, cap, max(free_exposure_tmn, ZERO), cash_cap)
    lo, hi = r.llm_size_multiplier_range
    size = base * _clamp(regime_mult, 0.0, 1.0) * _clamp(llm_mult, lo, hi)
    if mode == "live":
        size *= _clamp(r.initial_live_risk_scale, 0.0, 1.0)
    return max(size, ZERO)


def size_to_qty(size_tmn: Decimal, price: Decimal, symbol: str, registry: MarketRegistry) -> Decimal:
    """Step-aligned quantity for ``size_tmn`` at ``price``; 0 if below the market minimums."""
    if size_tmn <= 0 or price <= 0:
        return ZERO
    qty = registry.max_qty_for_notional(symbol, size_tmn, price)
    return qty if registry.check_min(symbol, qty, price) else ZERO
