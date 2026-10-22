"""Deterministic fallbacks when the LLM is disabled, slow or wrong (T-503).

- review: APPROVE × ``llm.fallback_size_multiplier`` if ``p_win ≥ θ + llm.fallback_ml_edge`` (or ML is
  off / skipped), otherwise REJECT;
- regime: the rule-based ``RuleRegimeProvider``.
"""

from __future__ import annotations

from typing import Any

from siavash.core.models import MLScore, TradeReview
from siavash.core.types import ComponentStatus
from siavash.features.regime import RuleRegimeProvider


def fallback_review(
    ml: MLScore, cfg: Any, status: ComponentStatus = ComponentStatus.FALLBACK, why: str = "LLM unavailable"
) -> TradeReview:
    """Conservative deterministic verdict."""
    size = float(cfg.llm.fallback_size_multiplier)
    edge = float(cfg.llm.fallback_ml_edge)
    ml_off = (
        not cfg.strategy.ml.enabled
        or ml.status in (ComponentStatus.SKIPPED, ComponentStatus.ERROR)
        or ml.p_win is None
    )
    if ml_off:
        return TradeReview(
            status=status,
            verdict="APPROVE",
            size_multiplier=size,
            reasons=[f"{why}; ML off -> approve x{size}"],
        )
    threshold = ml.threshold if ml.threshold is not None else cfg.strategy.ml.threshold
    assert ml.p_win is not None
    need = threshold + edge
    verdict = "APPROVE" if ml.p_win >= need else "REJECT"
    op = ">=" if verdict == "APPROVE" else "<"
    return TradeReview(
        status=status,
        verdict=verdict,
        size_multiplier=size,
        reasons=[f"{why}; p_win {ml.p_win:.3f} {op} {need:.3f}"],
    )


def fallback_regime(cfg: Any) -> RuleRegimeProvider:
    return RuleRegimeProvider(cfg)
