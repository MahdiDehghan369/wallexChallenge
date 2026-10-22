"""T-503: schemas, clamping, repair, fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from siavash.agents.fallback import fallback_regime, fallback_review
from siavash.agents.llm import LLMInvalidOutput
from siavash.agents.schemas import (
    LLMRegimeOut,
    LLMTradeReviewOut,
    complete_with_repair,
    to_regime,
    to_trade_review,
)
from siavash.config import MLConfig, load_config
from siavash.core.models import MLScore, RegimeAssessment
from siavash.core.types import ComponentStatus, Regime, StrategyId

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
TS = datetime(2026, 1, 1, tzinfo=UTC)


def _ml_on() -> Any:
    return CFG.model_copy(
        update={"strategy": CFG.strategy.model_copy(update={"ml": MLConfig(enabled=True, threshold=0.55)})}
    )


def test_review_clamp() -> None:
    out = LLMTradeReviewOut.model_validate(
        {"verdict": "approve", "size_multiplier": 1.7, "reasons": ["ok"], "confidence": 3}
    )
    r = to_trade_review(out, {"model": "m", "prompt_version": "reviewer_v1", "cache_key": "k"}, CFG)
    assert r.verdict == "APPROVE" and r.size_multiplier == 1.0 and r.confidence == 1.0 and r.cache_key == "k"
    low = to_trade_review(LLMTradeReviewOut(verdict="APPROVE", size_multiplier=0.01), {}, CFG)
    assert low.size_multiplier == 0.25
    with pytest.raises(ValidationError):
        LLMTradeReviewOut.model_validate({"verdict": "MAYBE"})


def test_regime_clamp_and_subset() -> None:
    rule = RegimeAssessment(
        ts=TS,
        regime=Regime.RANGE,
        risk_multiplier=0.7,
        enabled_strategies=[],
        reasons=[],
        source="rule",
        rule_regime=Regime.RANGE,
    )
    out = LLMRegimeOut.model_validate(
        {
            "regime": "range",
            "risk_multiplier": 0.95,
            "enabled_strategies": ["S2_MEANREV", "S3_IMPLIED_DEV", "S1_TREND_BREAKOUT"],
            "reasons": ["adx low"],
        }
    )
    r = to_regime(out, rule, CFG, TS)
    assert r.risk_multiplier == 0.7  # capped at RANGE multiplier
    assert r.enabled_strategies == [
        StrategyId.S2_MEAN_REVERSION,
        StrategyId.S3_IMPLIED_DEV,
    ]  # S1 not allowed in RANGE
    assert r.source == "llm" and r.rule_regime is Regime.RANGE
    panic = to_regime(LLMRegimeOut(regime=Regime.PANIC, risk_multiplier=0.5), rule, CFG, TS)
    assert panic.risk_multiplier == 0.0 and panic.enabled_strategies == []


class FakeLLM:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.users: list[dict[str, Any]] = []

    async def complete_json(self, system: str, user: dict[str, Any], schema: Any, **kw: Any) -> Any:
        self.users.append(user)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return schema.model_validate(o), {"model": "m"}


async def test_repair_once() -> None:
    llm = FakeLLM([LLMInvalidOutput("bad json"), {"verdict": "REJECT"}])
    out, _ = await complete_with_repair(llm, "s", {"a": 1}, LLMTradeReviewOut, "v1")
    assert out.verdict == "REJECT" and "previous_answer_error" in llm.users[1]
    llm2 = FakeLLM([LLMInvalidOutput("bad"), LLMInvalidOutput("still bad")])
    with pytest.raises(LLMInvalidOutput):
        await complete_with_repair(llm2, "s", {"a": 1}, LLMTradeReviewOut, "v1")


def test_fallback_review() -> None:
    skipped = MLScore(status=ComponentStatus.SKIPPED)
    r = fallback_review(skipped, CFG)
    assert r.verdict == "APPROVE" and r.size_multiplier == 0.5 and r.status is ComponentStatus.FALLBACK
    cfg = _ml_on()
    assert (
        fallback_review(MLScore(status=ComponentStatus.OK, p_win=0.61, threshold=0.55), cfg).verdict
        == "APPROVE"
    )
    assert (
        fallback_review(MLScore(status=ComponentStatus.OK, p_win=0.58, threshold=0.55), cfg).verdict
        == "REJECT"
    )
    assert fallback_review(skipped, CFG, status=ComponentStatus.SKIPPED).status is ComponentStatus.SKIPPED


async def test_fallback_regime_is_rule_provider() -> None:
    a = await fallback_regime(CFG).assess({"x_btc_ret_1h": -4.0}, TS)
    assert a.regime is Regime.PANIC and a.source == "rule"
