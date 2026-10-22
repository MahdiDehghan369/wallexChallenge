"""T-506: DailyReflector with a mock LLM — range filtering, labels, fallback, nothing applied."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from siavash.agents.llm import LLMError
from siavash.agents.reflector import LABEL, DailyReflector, Suggestion, filter_suggestions
from siavash.config import load_config

DAY = date(2026, 11, 5)
T0 = datetime(2026, 11, 5, 8, tzinfo=UTC)  # 11:30 Tehran


class MockLLM:
    enabled = True

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.inputs: list[dict[str, Any]] = []

    async def complete_json(self, system: str, user: dict[str, Any], schema: Any, **kw: Any) -> Any:
        self.inputs.append(user)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return schema.model_validate(self.outcome), {"model": "m"}


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    pd.DataFrame(
        {"ts": [T0, T0 + timedelta(hours=2), T0 + timedelta(days=1)], "equity_tmn": [5e6, 5.02e6, 4.9e6]}
    ).to_csv(d / "equity.csv", index=False)
    pd.DataFrame(
        [
            {
                "ts": T0,
                "symbol": "BTC/TMN",
                "side": "buy",
                "price": 1e9,
                "qty": 0.001,
                "notional_tmn": 1e6,
                "fee": 2000,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1_TREND_BREAKOUT",
            },
            {
                "ts": T0 + timedelta(hours=1),
                "symbol": "BTC/TMN",
                "side": "sell",
                "price": 1.02e9,
                "qty": 0.001,
                "notional_tmn": 1.02e6,
                "fee": 2040,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1_TREND_BREAKOUT",
            },
        ]
    ).to_csv(d / "fills.csv", index=False)
    (d / "decisions.jsonl").write_text(
        json.dumps(
            {
                "decision_id": "D1",
                "ts": T0.isoformat(),
                "type": "ENTRY_CANDIDATE",
                "outcome_action": "ORDER_PLACED",
                "symbol": "BTC/TMN",
                "strategy": "S1_TREND_BREAKOUT",
                "position_id": "P1",
            }
        )
        + "\n"
    )
    return d


GOOD = {
    "summary": "One S1 trade, +2 %.",
    "worked": ["S1 breakout on BTC"],
    "did_not_work": [],
    "ai_effect": "no rejections today",
    "suggestions": [
        {"parameter": "strategy.s3.z_entry", "proposed": 2.5, "reason": "fewer weak S3 signals"},
        {"parameter": "risk.risk_per_trade_pct", "proposed": 2.0, "reason": "more profit"},  # above cap
        {"parameter": "risk.kill_switch_drawdown_pct", "proposed": 4.0, "reason": "x"},  # not tunable
    ],
}


async def test_reflect_with_llm(logs: Path, tmp_path: Path) -> None:
    cfg = load_config("config")
    before = cfg.model_dump(mode="json")
    llm = MockLLM(GOOD)
    path = await DailyReflector(cfg, llm, logs, reports_dir=tmp_path / "reports").reflect(DAY)
    assert path.name == "daily_20261105.md"
    text = path.read_text(encoding="utf-8")
    assert "LLM status: ok" in text and "One S1 trade" in text
    assert "strategy.s3.z_entry" in text and LABEL in text
    assert "risk.risk_per_trade_pct=2.0: outside allowed range" in text
    assert "risk.kill_switch_drawdown_pct: not a tunable parameter" in text
    payload = llm.inputs[0]
    assert payload["stats"]["n_trades"] == 1 and payload["date"] == "2026-11-05"
    assert payload["parameters"]["strategy.s3.z_entry"]["allowed"] == [1.5, 3.0]
    assert cfg.model_dump(mode="json") == before  # nothing applied


async def test_fallback_without_llm(logs: Path, tmp_path: Path) -> None:
    cfg = load_config("config")
    path = await DailyReflector(cfg, MockLLM(LLMError("down")), logs, reports_dir=tmp_path).reflect(DAY)
    text = path.read_text(encoding="utf-8")
    assert "LLM status: fallback" in text and "## Numbers" in text and "Parameter suggestions" not in text
    path2 = await DailyReflector(cfg, None, logs, reports_dir=tmp_path).reflect(DAY)
    assert "LLM status: skipped" in path2.read_text(encoding="utf-8")


def test_filter_suggestions_limits() -> None:
    cfg = load_config("config")
    many = [Suggestion(parameter="strategy.s3.z_entry", proposed=2.0 + i * 0.1) for i in range(5)]
    kept, dropped = filter_suggestions(cfg, many)
    assert len(kept) == 3 and len(dropped) == 2
    assert kept[0].current == cfg.strategy.s3.z_entry
    assert cfg.strategy.tunable_ranges["risk.risk_per_trade_pct"][1] <= 0.5  # section-5 cap
    assert cfg.strategy.tunable_ranges["risk.max_concurrent_positions"][1] <= 3
