"""T-505: LLMReviewer with a mocked LLM."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from siavash.agents.llm import LLMError, LLMInvalidOutput
from siavash.agents.reviewer import LLMReviewer
from siavash.config import LLMConfig, ReviewerConfig, load_config
from siavash.core.models import Candidate, MLScore, PortfolioSnapshot, RegimeAssessment
from siavash.core.types import BreakerState, ComponentStatus, Regime, StrategyId

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
TS = datetime(2026, 1, 1, tzinfo=UTC)
C = Candidate(
    candidate_id="c",
    ts=TS,
    symbol="BTC/TMN",
    strategy=StrategyId.S3_IMPLIED_DEV,
    entry_ref_price=100.0,
    stop_loss=98.8,
    take_profit=101.9,
    time_stop_minutes=180,
    expected_move_pct=1.9,
    conditions={"z_dev": -2.4, "tp_capped": True},
)
ML = MLScore(status=ComponentStatus.SKIPPED)
REG = RegimeAssessment(
    ts=TS,
    regime=Regime.RANGE,
    risk_multiplier=0.7,
    enabled_strategies=list(StrategyId),
    reasons=["adx 14"],
    source="rule",
    rule_regime=Regime.RANGE,
)
PF = PortfolioSnapshot(
    ts=TS,
    equity_tmn=Decimal("5000000"),
    cash_tmn=Decimal("4000000"),
    peak_equity_tmn=Decimal("5000000"),
    exposure_tmn=Decimal("1000000"),
    open_positions=[],
    trades_last_hour=0,
    trades_today=1,
    daily_pnl_pct=0.2,
    drawdown_pct=0.1,
    breaker_state=BreakerState.NORMAL,
)
FEATS = {"15m_rsi14": 31.234567, "x_implied_z": -2.4, "unrelated": 1.0, "1h_adx14": float("nan")}


class MockLLM:
    enabled = True

    def __init__(self, outcomes: list[Any], delay: float = 0.0) -> None:
        self.outcomes = outcomes
        self.delay = delay
        self.inputs: list[dict[str, Any]] = []

    async def complete_json(self, system: str, user: dict[str, Any], schema: Any, **kw: Any) -> Any:
        self.inputs.append(user)
        if self.delay:
            await asyncio.sleep(self.delay)
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return schema.model_validate(o), {
            "model": "m",
            "prompt_version": kw.get("prompt_version"),
            "cache_key": "k",
        }


def _cfg(**reviewer: Any) -> Any:
    llm = CFG.llm.model_copy(
        update={"reviewer": ReviewerConfig(**{"enabled": True, "timeout_s": 20, **reviewer})}
    )
    return CFG.model_copy(update={"llm": llm})


async def test_approve_with_structured_input() -> None:
    llm = MockLLM(
        [
            {
                "verdict": "APPROVE",
                "size_multiplier": 0.75,
                "reasons": ["4h supportive"],
                "risk_flags": [],
                "confidence": 0.7,
            }
        ]
    )
    recent = [{"pnl_pct": 1.2}] * 12
    r = await LLMReviewer(_cfg(), llm, recent_fn=lambda s, n: recent).review(C, FEATS, ML, REG, PF)
    assert r.verdict == "APPROVE" and r.size_multiplier == 0.75 and r.status is ComponentStatus.OK
    assert r.prompt_version == "reviewer_v1" and r.cache_key == "k"
    sent = llm.inputs[0]
    assert sent["candidate"]["strategy"] == "S3_IMPLIED_DEV" and sent["features"]["15m_rsi14"] == 31.2346
    assert "unrelated" not in sent["features"] and sent["features"]["1h_adx14"] is None
    assert sent["portfolio"]["exposure_pct"] == 20.0 and len(sent["recent_performance"]) == 10


async def test_reject() -> None:
    r = await LLMReviewer(
        _cfg(), MockLLM([{"verdict": "REJECT", "size_multiplier": 1, "reasons": ["near resistance"]}])
    ).review(C, FEATS, ML, REG, PF)
    assert r.verdict == "REJECT" and r.reasons == ["near resistance"]


async def test_timeout_falls_back() -> None:
    r = await LLMReviewer(_cfg(timeout_s=0.05), MockLLM([{"verdict": "REJECT"}], delay=1.0)).review(
        C, FEATS, ML, REG, PF
    )
    assert r.status is ComponentStatus.FALLBACK and r.verdict == "APPROVE" and r.size_multiplier == 0.5
    assert "timeout" in r.reasons[0]


async def test_broken_output_falls_back_after_repair() -> None:
    llm = MockLLM([LLMInvalidOutput("bad"), LLMInvalidOutput("bad again")])
    r = await LLMReviewer(_cfg(), llm).review(C, FEATS, ML, REG, PF)
    assert r.status is ComponentStatus.FALLBACK and len(llm.inputs) == 2
    r2 = await LLMReviewer(_cfg(), MockLLM([LLMError("down")])).review(C, FEATS, ML, REG, PF)
    assert r2.status is ComponentStatus.FALLBACK


async def test_budget_exceeded_falls_back() -> None:
    from siavash.agents.budget import BudgetExceeded

    llm = MockLLM([BudgetExceeded("max_calls_per_hour=20")])
    r = await LLMReviewer(_cfg(), llm).review(C, FEATS, ML, REG, PF)
    assert r.status is ComponentStatus.FALLBACK and "BudgetExceeded" in r.reasons[0]
    assert len(llm.inputs) == 1  # no repair attempt on a budget stop


async def test_clamp_and_disabled() -> None:
    r = await LLMReviewer(_cfg(), MockLLM([{"verdict": "APPROVE", "size_multiplier": 2.5}])).review(
        C, FEATS, ML, REG, PF
    )
    assert r.size_multiplier == 1.0
    off = await LLMReviewer(_cfg(enabled=False), MockLLM([])).review(C, FEATS, ML, REG, PF)
    assert off.status is ComponentStatus.SKIPPED
    none = await LLMReviewer(_cfg(), None).review(C, FEATS, ML, REG, PF)
    assert none.status is ComponentStatus.SKIPPED
    assert isinstance(CFG.llm, LLMConfig)
