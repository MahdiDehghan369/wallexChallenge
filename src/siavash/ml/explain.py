"""SHAP explanations (T-405): top-k feature contributions for one candidate (TreeExplainer)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from siavash.ml.registry import LoadedModel


def explain(model: LoadedModel, row: Mapping[str, Any], k: int = 5) -> list[tuple[str, float]]:
    """``[(feature, shap_value)]`` sorted by |value|, largest first (log-odds contributions)."""
    values = model.explainer.shap_values(model.frame(row))
    arr = np.asarray(values[1] if isinstance(values, list) else values, dtype=float)
    contrib = arr.reshape(-1, len(model.features))[0]
    order = np.argsort(-np.abs(contrib), kind="stable")[:k]
    return [(model.features[i], round(float(contrib[i]), 6)) for i in order]
