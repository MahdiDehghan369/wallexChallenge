"""Market-wide features and the deterministic rule-based regime detector (T-304).

Market features (percent units, indexed by 15m close time):
- ``x_btc_ret_1h`` / ``x_btc_ret_4h``: BTC rolling return over the last 4 / 16 15m bars,
- ``x_btc_adx_1h``, ``x_btc_rv_1h``: BTC 1h ADX(14) and 1h realised vol,
- ``x_btc_ema_trend_1h``: ``(1h EMA20 / 1h EMA50 − 1) × 100`` (> 0 means EMA20 > EMA50),
- ``x_breadth_ema50``: % of universe markets whose 1h close is above their 1h EMA50,
- ``x_avg_ret_4h``: mean 4h rolling return across the universe,
- ``x_rv_percentile_7d``: percentile (0–100) of BTC 15m realised vol within the trailing 7 days.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from siavash.core.interfaces import FeatureRow
from siavash.core.models import RegimeAssessment
from siavash.core.types import Regime, StrategyId

BARS_7D_15M = 7 * 96
MIN_BARS_PERCENTILE = 96


def _rolling_percentile(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    def pct(w: np.ndarray) -> float:
        w = w[~np.isnan(w)]
        if len(w) < min_periods:
            return np.nan
        return float((w <= w[-1]).mean() * 100.0)

    return s.rolling(window, min_periods=1).apply(pct, raw=True).where(s.notna())


def market_features(
    universe_features: Mapping[str, pd.DataFrame], btc_symbol: str = "BTC/TMN"
) -> pd.DataFrame:
    """Compute ``x_*`` market features from per-symbol outputs of ``build_features``."""
    if btc_symbol not in universe_features:
        raise KeyError(f"{btc_symbol} features are required for market features")
    btc = universe_features[btc_symbol]
    out = pd.DataFrame(index=btc.index)
    out["x_btc_ret_1h"] = btc["15m_ret_4"]
    out["x_btc_ret_4h"] = btc["15m_ret_16"]
    out["x_btc_adx_1h"] = btc["1h_adx14"]
    out["x_btc_rv_1h"] = btc["1h_rv20"]
    out["x_btc_ema_trend_1h"] = (btc["1h_ema20"] / btc["1h_ema50"] - 1.0) * 100.0

    above = pd.DataFrame(
        {s: (f["1h_ema50_dist"] > 0).where(f["1h_ema50_dist"].notna()) for s, f in universe_features.items()}
    )
    above = above.reindex(out.index)
    out["x_breadth_ema50"] = above.astype(float).mean(axis=1, skipna=True) * 100.0
    rets = pd.DataFrame({s: f["15m_ret_16"] for s, f in universe_features.items()}).reindex(out.index)
    out["x_avg_ret_4h"] = rets.mean(axis=1, skipna=True)
    out["x_rv_percentile_7d"] = _rolling_percentile(btc["15m_rv20"], BARS_7D_15M, MIN_BARS_PERCENTILE)
    return out


def _f(row: FeatureRow, key: str) -> float:
    v = row.get(key, float("nan"))
    return float("nan") if v is None else float(v)


class RuleRegimeProvider:
    """Deterministic regime rules (order matters): PANIC → HIGH_VOL → TREND_UP → RANGE.

    Thresholds: ``strategy.regime.rules``; risk multiplier: ``risk.regime_multipliers``;
    enabled strategies: ``strategy.regime.enabled_strategies``.
    """

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.rules = cfg.strategy.regime.rules

    def classify(self, row: FeatureRow) -> tuple[Regime, list[str]]:
        """Pure rule evaluation: ``(regime, reasons)``."""
        r = self.rules
        ret1h, ret4h = _f(row, "x_btc_ret_1h"), _f(row, "x_btc_ret_4h")
        rvp, adx = _f(row, "x_rv_percentile_7d"), _f(row, "x_btc_adx_1h")
        trend, breadth = _f(row, "x_btc_ema_trend_1h"), _f(row, "x_breadth_ema50")
        if ret1h <= r.panic_btc_ret_1h_pct or ret4h <= r.panic_btc_ret_4h_pct:
            return Regime.PANIC, [
                f"BTC ret 1h={ret1h:.2f}% (≤{r.panic_btc_ret_1h_pct}) or 4h={ret4h:.2f}% (≤{r.panic_btc_ret_4h_pct})"
            ]
        if rvp >= r.high_vol_rv_percentile:
            return Regime.HIGH_VOL, [f"BTC realised-vol percentile 7d={rvp:.0f} ≥ {r.high_vol_rv_percentile}"]
        if adx >= r.trend_adx_min and trend > 0 and breadth >= r.trend_breadth_min:
            return Regime.TREND_UP, [
                f"BTC ADX(1h)={adx:.1f} ≥ {r.trend_adx_min}",
                f"BTC EMA20>EMA50 (1h) by {trend:.2f}%",
                f"breadth={breadth:.0f}% ≥ {r.trend_breadth_min}",
            ]
        reasons = [f"BTC ADX(1h)={adx:.1f}", f"EMA trend 1h={trend:.2f}%", f"breadth={breadth:.0f}%"]
        if any(np.isnan(v) for v in (adx, trend, breadth)):
            reasons.append("insufficient data for trend rule")
        return Regime.RANGE, reasons

    def assessment(
        self, regime: Regime, reasons: list[str], ts: datetime, source: str = "rule"
    ) -> RegimeAssessment:
        """Wrap a regime into a ``RegimeAssessment`` with config multipliers/strategies."""
        enabled = [StrategyId(s) for s in self.cfg.strategy.regime.enabled_strategies.get(regime.value, [])]
        return RegimeAssessment(
            ts=ts,
            regime=regime,
            risk_multiplier=float(self.cfg.risk.regime_multipliers[regime.value]),
            enabled_strategies=enabled,
            reasons=reasons,
            source=source,  # type: ignore[arg-type]
            rule_regime=regime,
        )

    async def assess(self, market_features: FeatureRow, ts: datetime) -> RegimeAssessment:
        """``RegimeProvider`` implementation."""
        regime, reasons = self.classify(market_features)
        return self.assessment(regime, reasons, ts)
