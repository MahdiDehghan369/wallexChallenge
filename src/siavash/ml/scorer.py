"""ML scorer (T-406): calibrated ``p_win``, ``threshold``, ``ev_pct`` and SHAP top features.

``ev_pct = p × tp% − (1 − p) × sl% − round_trip_fee%`` with ``tp%`` to the first target (``tp1`` when
set) and ``sl%`` to the stop. ``ml.enabled=false`` or no model → ``status=SKIPPED``; any error while
scoring → ``status=ERROR`` (the pipeline then continues without the ML gate).
"""

from __future__ import annotations

from typing import Any

from siavash.core.interfaces import FeatureRow
from siavash.core.models import Candidate, MLScore
from siavash.core.types import ComponentStatus, Regime
from siavash.logging import get_logger
from siavash.ml.dataset import REGIME_COLUMNS
from siavash.ml.explain import explain
from siavash.ml.registry import LoadedModel, load_model
from siavash.signals.fee_filter import round_trip_fee_pct

log = get_logger(__name__)


def regime_features(regime: Regime | str) -> dict[str, float]:
    """One-hot regime columns, as in the training dataset."""
    value = regime.value if isinstance(regime, Regime) else str(regime)
    return {col: float(col == f"regime_{value}") for col in REGIME_COLUMNS}


def ev_pct(c: Candidate, p: float, fee_pct: float) -> float:
    target = c.tp1_price if c.tp1_price is not None else c.take_profit
    tp = (target / c.entry_ref_price - 1) * 100
    sl = (1 - c.stop_loss / c.entry_ref_price) * 100
    return p * tp - (1 - p) * sl - fee_pct


class LgbmScorer:
    """``Scorer`` over a registry model."""

    def __init__(
        self, cfg: Any, model: LoadedModel | None = None, models_root: str = "models", top_k: int = 5
    ) -> None:
        self.cfg = cfg
        ml = cfg.strategy.ml
        self.threshold = float(ml.threshold)
        self.fee_pct = round_trip_fee_pct(cfg)
        self.top_k = top_k
        self.model = model
        if self.model is None and ml.enabled and ml.model_version:
            try:
                self.model = load_model(ml.model_version, models_root)
            except Exception as e:  # missing / incompatible model: run without ML, loudly
                log.error("ml_model_load_failed", version=ml.model_version, error=str(e))
        self.enabled = bool(ml.enabled and self.model is not None)

    @property
    def model_version(self) -> str | None:
        return self.model.version if self.model is not None else None

    def score(self, c: Candidate, features: FeatureRow) -> MLScore:
        if not self.enabled or self.model is None:
            return MLScore(status=ComponentStatus.SKIPPED)
        try:
            p = float(self.model.predict(features)[0])
            top = explain(self.model, features, self.top_k) if self.top_k else []
            return MLScore(
                status=ComponentStatus.OK,
                model_version=self.model.version,
                p_win=round(p, 6),
                threshold=self.threshold,
                ev_pct=round(ev_pct(c, p, self.fee_pct), 6),
                top_features=top,
            )
        except Exception as e:
            log.warning("ml_score_failed", symbol=c.symbol, error=str(e))
            return MLScore(status=ComponentStatus.ERROR, model_version=self.model.version)
