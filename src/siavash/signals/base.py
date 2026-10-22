"""Shared helpers for signal generators (T-305)."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from siavash.core.ids import new_ulid
from siavash.core.interfaces import FeatureRow, SignalGenerator
from siavash.core.models import Candidate, RegimeAssessment


def feat(features: FeatureRow, key: str) -> float | None:
    """Feature value or ``None`` if missing/NaN/inf."""
    v = features.get(key)
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def feats(features: FeatureRow, keys: tuple[str, ...]) -> dict[str, float] | None:
    """All requested features as floats, or ``None`` if any is missing/NaN."""
    out: dict[str, float] = {}
    for k in keys:
        v = feat(features, k)
        if v is None:
            return None
        out[k] = v
    return out


class BaseSignal(SignalGenerator):
    """Common gating (strategy enabled + allowed by regime) and candidate construction."""

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg

    def is_active(self, regime: RegimeAssessment) -> bool:
        """Strategy must be enabled in config and allowed by the current regime."""
        enabled = self.strategy_id.value in self.cfg.strategy.strategies.enabled
        return enabled and self.strategy_id in regime.enabled_strategies

    def make_candidate(
        self,
        symbol: str,
        ts: datetime,
        entry: float,
        stop_loss: float,
        take_profit: float,
        time_stop_minutes: int,
        conditions: dict[str, float | str | bool],
        tp1_price: float | None = None,
    ) -> Candidate | None:
        """Build a ``Candidate``; ``None`` unless ``stop_loss < entry < take_profit``."""
        if not (0 < stop_loss < entry < take_profit):
            return None
        target = tp1_price if tp1_price is not None else take_profit
        return Candidate(
            candidate_id=new_ulid(),
            ts=ts,
            symbol=symbol,
            strategy=self.strategy_id,
            entry_ref_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            tp1_price=tp1_price,
            time_stop_minutes=time_stop_minutes,
            expected_move_pct=(target / entry - 1.0) * 100.0,
            conditions=conditions,
        )
