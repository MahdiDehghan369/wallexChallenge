"""T-504: conservative merge + fallback."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from siavash.agents.analyst import LLMRegimeProvider, needs_update
from siavash.agents.llm import LLMError
from siavash.config import load_config
from siavash.core.types import ComponentStatus, DecisionType, Regime, StrategyId
from siavash.engine.decision_log import MemoryDecisionLogger
from tests.unit.agents.test_reviewer import MockLLM

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
TS = datetime(2026, 1, 1, tzinfo=UTC)
RANGE_ROW = {
    "x_btc_ret_1h": 0.1,
    "x_btc_ret_4h": 0.2,
    "x_rv_percentile_7d": 40.0,
    "x_btc_adx_1h": 14.0,
    "x_btc_ema_trend_1h": -0.1,
    "x_breadth_ema50": 45.0,
}
TREND_ROW = {**RANGE_ROW, "x_btc_adx_1h": 28.0, "x_btc_ema_trend_1h": 0.4, "x_breadth_ema50": 70.0}


def _prov(llm: Any, tmp_path: Path, logger: MemoryDecisionLogger | None = None) -> LLMRegimeProvider:
    return LLMRegimeProvider(CFG, llm, decision_logger=logger, regime_log=tmp_path / "regime.jsonl")


async def test_llm_trend_rule_range_gives_range(tmp_path: Path) -> None:
    llm = MockLLM(
        [
            {
                "regime": "TREND_UP",
                "risk_multiplier": 1.0,
                "enabled_strategies": ["S1_TREND_BREAKOUT", "S3_IMPLIED_DEV"],
                "reasons": ["breakout"],
            }
        ]
    )
    logger = MemoryDecisionLogger()
    r = await _prov(llm, tmp_path, logger).assess(RANGE_ROW, TS)
    assert r.regime is Regime.RANGE and r.source == "rule" and r.rule_regime is Regime.RANGE
    assert r.enabled_strategies == [StrategyId.S3_IMPLIED_DEV]  # intersection
    assert "rule_regime" in llm.inputs[0] and "x_btc_adx_1h" in llm.inputs[0]["market_features"]
    assert logger.records[0].type is DecisionType.REGIME_UPDATE
    assert json.loads((tmp_path / "regime.jsonl").read_text().splitlines()[0])["regime"] == "RANGE"


async def test_llm_more_cautious_wins(tmp_path: Path) -> None:
    llm = MockLLM(
        [
            {
                "regime": "HIGH_VOL",
                "risk_multiplier": 0.5,
                "enabled_strategies": ["S3_IMPLIED_DEV"],
                "reasons": ["vol spike"],
            }
        ]
    )
    r = await _prov(llm, tmp_path).assess(TREND_ROW, TS)
    assert (
        r.regime is Regime.HIGH_VOL
        and r.source == "llm"
        and r.rule_regime is Regime.TREND_UP
        and r.risk_multiplier == 0.5
    )


async def test_fallback_and_disabled(tmp_path: Path) -> None:
    r = await _prov(MockLLM([LLMError("down")]), tmp_path).assess(TREND_ROW, TS)
    assert r.regime is Regime.TREND_UP and r.status is ComponentStatus.FALLBACK and r.source == "fallback"
    off = await _prov(None, tmp_path).assess(RANGE_ROW, TS)
    assert off.status is ComponentStatus.SKIPPED and off.regime is Regime.RANGE


def test_needs_update() -> None:
    assert needs_update(TS, None, 60, None, None)
    assert needs_update(TS + timedelta(minutes=61), TS, 60, 0.1, 0.5)
    assert not needs_update(TS + timedelta(minutes=10), TS, 60, 0.6, 0.5)
    assert needs_update(TS + timedelta(minutes=10), TS, 60, -1.2, 0.5)
