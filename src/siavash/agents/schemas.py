"""Raw LLM output schemas and their safe conversion (T-503, appendix D).

The conversion enforces section 3.2 ("AI proposes, risk vetoes"): ``size_multiplier`` is clamped to
``risk.llm_size_multiplier_range`` (never above 1), the LLM regime's ``risk_multiplier`` never exceeds
the configured multiplier of that regime, and ``enabled_strategies`` must be a subset of what the
regime allows. Invalid output gets exactly one repair attempt, then the caller falls back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

from siavash.agents.llm import LLMInvalidOutput
from siavash.core.models import RegimeAssessment, TradeReview
from siavash.core.types import ComponentStatus, Regime, StrategyId

T = TypeVar("T", bound=BaseModel)

STRATEGY_ALIASES = {
    "S1": "S1_TREND_BREAKOUT",
    "S1_TREND": "S1_TREND_BREAKOUT",
    "TREND_BREAKOUT": "S1_TREND_BREAKOUT",
    "S2": "S2_MEAN_REVERSION",
    "S2_MEANREV": "S2_MEAN_REVERSION",
    "MEAN_REVERSION": "S2_MEAN_REVERSION",
    "S3": "S3_IMPLIED_DEV",
    "IMPLIED_DEV": "S3_IMPLIED_DEV",
}


class LLMTradeReviewOut(BaseModel):
    verdict: Literal["APPROVE", "REJECT"]
    size_multiplier: float = 1.0
    reasons: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float | None = None

    @field_validator("verdict", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v


class LLMRegimeOut(BaseModel):
    regime: Regime
    risk_multiplier: float
    enabled_strategies: list[StrategyId] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    watch: list[str] = Field(default_factory=list)

    @field_validator("regime", mode="before")
    @classmethod
    def _regime(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("enabled_strategies", mode="before")
    @classmethod
    def _aliases(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [STRATEGY_ALIASES.get(str(s).strip().upper(), str(s).strip().upper()) for s in v]
        return v


def to_trade_review(out: LLMTradeReviewOut, meta: dict[str, Any], cfg: Any) -> TradeReview:
    """Clamp and wrap a raw review."""
    lo, hi = cfg.risk.llm_size_multiplier_range
    mult = min(max(float(out.size_multiplier), lo), hi, 1.0)
    conf = None if out.confidence is None else min(max(float(out.confidence), 0.0), 1.0)
    return TradeReview(
        status=ComponentStatus.OK,
        verdict=out.verdict,
        size_multiplier=mult,
        reasons=[str(r)[:300] for r in out.reasons][:10],
        risk_flags=[str(r)[:200] for r in out.risk_flags][:10],
        confidence=conf,
        model=meta.get("model"),
        prompt_version=meta.get("prompt_version"),
        cache_key=meta.get("cache_key"),
    )


def to_regime(out: LLMRegimeOut, rule: RegimeAssessment, cfg: Any, ts: datetime) -> RegimeAssessment:
    """LLM regime clamped to config limits (``source="llm"``; ``rule_regime`` kept)."""
    cap = float(cfg.risk.regime_multipliers[out.regime.value])
    allowed = {StrategyId(s) for s in cfg.strategy.regime.enabled_strategies.get(out.regime.value, [])}
    enabled = [s for s in out.enabled_strategies if s in allowed]
    return RegimeAssessment(
        ts=ts,
        regime=out.regime,
        risk_multiplier=min(max(float(out.risk_multiplier), 0.0), cap),
        enabled_strategies=enabled,
        reasons=[str(r)[:300] for r in out.reasons][:10],
        source="llm",
        rule_regime=rule.rule_regime,
        status=ComponentStatus.OK,
    )


async def complete_with_repair(
    llm: Any, system: str, user: dict[str, Any], schema: type[T], prompt_version: str, **kw: Any
) -> tuple[T, dict[str, Any]]:
    """Call the LLM; on invalid output retry once with the validation error appended, else re-raise."""
    try:
        return await llm.complete_json(system, user, schema, prompt_version=prompt_version, **kw)
    except LLMInvalidOutput as e:
        repair = dict(user)
        repair["previous_answer_error"] = str(e)[:500]
        repair["instruction"] = (
            "Your previous answer was invalid. Reply with ONLY one JSON object that matches the schema."
        )
        return await llm.complete_json(system, repair, schema, prompt_version=prompt_version, **kw)
