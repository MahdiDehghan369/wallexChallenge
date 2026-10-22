"""S1 Trend Breakout (section 4.3)."""

from __future__ import annotations

from datetime import datetime

from siavash.core.interfaces import FeatureRow
from siavash.core.models import Candidate, RegimeAssessment
from siavash.core.types import StrategyId
from siavash.signals.base import BaseSignal, feats

KEYS = ("15m_close", "15m_atr14", "15m_donchian_high_dist", "1h_ema20", "1h_ema50", "1h_adx14", "15m_vol_z20")


class TrendBreakout(BaseSignal):
    """Close above the previous Donchian high, 1h EMA20 > EMA50, 1h ADX and 15m volume confirmation."""

    strategy_id = StrategyId.S1_TREND_BREAKOUT

    def generate(
        self, symbol: str, ts: datetime, features: FeatureRow, regime: RegimeAssessment
    ) -> list[Candidate]:
        if not self.is_active(regime):
            return []
        v = feats(features, KEYS)
        if v is None:
            return []
        p = self.cfg.strategy.s1
        close, atr = v["15m_close"], v["15m_atr14"]
        ok = (
            v["15m_donchian_high_dist"] > 0
            and v["1h_ema20"] > v["1h_ema50"]
            and v["1h_adx14"] > p.adx_min
            and v["15m_vol_z20"] > p.vol_z_min
            and atr > 0
        )
        if not ok:
            return []
        conditions: dict[str, float | str | bool] = {
            "donchian_high_dist_pct": v["15m_donchian_high_dist"],
            "ema20_1h": v["1h_ema20"],
            "ema50_1h": v["1h_ema50"],
            "adx_1h": v["1h_adx14"],
            "adx_min": float(p.adx_min),
            "vol_z_15m": v["15m_vol_z20"],
            "vol_z_min": float(p.vol_z_min),
            "atr_15m": atr,
        }
        c = self.make_candidate(
            symbol,
            ts,
            entry=close,
            stop_loss=close - p.sl_atr * atr,
            take_profit=close + p.tp_atr * atr,
            tp1_price=close + p.tp1_atr * atr,
            time_stop_minutes=p.time_stop_min,
            conditions=conditions,
        )
        return [c] if c else []
