"""S2 Mean Reversion (section 4.3)."""

from __future__ import annotations

from datetime import datetime

from siavash.core.interfaces import FeatureRow
from siavash.core.models import Candidate, RegimeAssessment
from siavash.core.types import StrategyId
from siavash.signals.base import BaseSignal, feats

KEYS = ("15m_close", "15m_atr14", "15m_bb_pos", "15m_bb_mid", "15m_rsi14", "1h_adx14", "4h_ema200_dist")


class MeanReversion(BaseSignal):
    """Close below the lower Bollinger band, RSI oversold, weak 1h trend, 4h not far below EMA200."""

    strategy_id = StrategyId.S2_MEAN_REVERSION

    def generate(
        self, symbol: str, ts: datetime, features: FeatureRow, regime: RegimeAssessment
    ) -> list[Candidate]:
        if not self.is_active(regime):
            return []
        v = feats(features, KEYS)
        if v is None:
            return []
        p = self.cfg.strategy.s2
        close, atr, bb_mid = v["15m_close"], v["15m_atr14"], v["15m_bb_mid"]
        ok = (
            v["15m_bb_pos"] < 0
            and v["15m_rsi14"] < p.rsi_max
            and v["1h_adx14"] < p.adx_max
            and v["4h_ema200_dist"] > p.min_4h_ema200_dist_pct
            and atr > 0
        )
        if not ok:
            return []
        conditions: dict[str, float | str | bool] = {
            "bb_pos_15m": v["15m_bb_pos"],
            "rsi_15m": v["15m_rsi14"],
            "rsi_max": float(p.rsi_max),
            "adx_1h": v["1h_adx14"],
            "adx_max": float(p.adx_max),
            "ema200_dist_4h_pct": v["4h_ema200_dist"],
            "min_4h_ema200_dist_pct": float(p.min_4h_ema200_dist_pct),
            "bb_mid_15m": bb_mid,
            "tp_dist_pct": (bb_mid / close - 1.0) * 100.0,
            "atr_15m": atr,
        }
        c = self.make_candidate(
            symbol,
            ts,
            entry=close,
            stop_loss=close - p.sl_atr * atr,
            take_profit=bb_mid,
            time_stop_minutes=p.time_stop_min,
            conditions=conditions,
        )
        return [c] if c else []
