"""S3 Implied Deviation (section 4.3)."""

from __future__ import annotations

from datetime import datetime

from siavash.core.interfaces import FeatureRow
from siavash.core.models import Candidate, RegimeAssessment
from siavash.core.types import StrategyId
from siavash.signals.base import BaseSignal, feat


class ImpliedDeviation(BaseSignal):
    """TMN price far below ``X/USDT × USDT/TMN`` (rolling z of the deviation) with a normal spread."""

    strategy_id = StrategyId.S3_IMPLIED_DEV

    def generate(
        self, symbol: str, ts: datetime, features: FeatureRow, regime: RegimeAssessment
    ) -> list[Candidate]:
        if not self.is_active(regime) or not symbol.endswith("/TMN") or symbol == "USDT/TMN":
            return []
        p = self.cfg.strategy.s3
        close = feat(features, "15m_close")
        z = feat(features, "x_implied_z")
        dev = feat(features, "x_implied_dev_pct")
        dev_mean = feat(features, "x_implied_dev_mean_pct")
        usdt_ret = feat(features, "x_btc_ret_1h")
        spread = feat(features, "ob_spread_pct")
        if close is None or z is None or dev is None or dev_mean is None or usdt_ret is None:
            return []
        max_spread = float(self.cfg.risk.max_spread_pct)
        spread_ok = spread is None or spread <= max_spread
        if not (z < -p.z_entry and spread_ok and usdt_ret > p.min_usdt_ret_1h_pct):
            return []
        # price at which the deviation returns to its rolling mean (z = 0), implied unchanged
        z0_price = close * (1.0 + dev_mean / 100.0) / (1.0 + dev / 100.0)
        cap_price = close * (1.0 + p.tp_pct / 100.0)
        take_profit = min(z0_price, cap_price)
        conditions: dict[str, float | str | bool] = {
            "z_dev": z,
            "z_entry": -float(p.z_entry),
            "dev_pct": dev,
            "dev_mean_pct": dev_mean,
            "usdt_pair_ret_1h": usdt_ret,
            "min_usdt_ret_1h_pct": float(p.min_usdt_ret_1h_pct),
            "z0_price": z0_price,
            "tp_capped": bool(cap_price < z0_price),
        }
        if spread is not None:
            conditions["spread_pct"] = spread
        c = self.make_candidate(
            symbol,
            ts,
            entry=close,
            stop_loss=close * (1.0 - p.sl_pct / 100.0),
            take_profit=take_profit,
            time_stop_minutes=p.time_stop_min,
            conditions=conditions,
        )
        return [c] if c else []
