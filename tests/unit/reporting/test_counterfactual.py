"""T-1203: counterfactual outcome of AI rejections."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.reporting.counterfactual import (
    counterfactual_markdown,
    evaluate_rejections,
    one_liner,
    summarize,
)

T0 = datetime(2026, 11, 5, 12, tzinfo=UTC)


def signal(i: int, symbol: str = "BTC/TMN") -> dict:
    return {
        "candidate_id": f"C{i}",
        "ts": (T0 + timedelta(hours=i)).isoformat(),
        "symbol": symbol,
        "strategy": "S2_MEAN_REVERSION",
        "entry_ref_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 103.0,
        "time_stop_minutes": 60,
        "expected_move_pct": 3.0,
        "conditions": {},
    }


def rec(i: int, stage: str, symbol: str = "BTC/TMN") -> dict:
    return {
        "type": "ENTRY_CANDIDATE",
        "decision_id": f"D{i}",
        "rejected_at": stage,
        "outcome_action": "REJECTED",
        "ts": (T0 + timedelta(hours=i)).isoformat(),
        "symbol": symbol,
        "signal": signal(i, symbol),
    }


def bars() -> pd.DataFrame:
    """Hour 0: rises to TP (winner); hour 1: falls to SL (loser); hour 2: TP; hour 3: SL."""
    rows = []
    for h in range(6):
        for q in range(4):
            ts = T0 + timedelta(hours=h, minutes=15 * q)
            up = h % 2 == 0
            hi, lo = (104.0, 99.5) if (up and q == 1) else (100.5, 99.5)
            if not up and q == 1:
                hi, lo = 100.5, 97.0
            rows.append({"ts_open": ts, "open": 100.0, "high": hi, "low": lo, "close": 100.0, "volume": 1.0})
    return pd.DataFrame(rows)


def test_counterfactual(tmp_path: Path) -> None:
    decisions = [
        rec(0, "LLM"),
        rec(1, "LLM"),
        rec(2, "ML"),
        rec(3, "EV"),
        rec(4, "RISK"),
        rec(5, "LLM", "NODATA/TMN"),
        {"type": "EXIT", "decision_id": "X"},
    ]
    p = tmp_path / "decisions.jsonl"
    p.write_text("\n".join(json.dumps(d) for d in decisions))
    cfg = load_config("config")
    df = evaluate_rejections(p, {"BTC/TMN": {Timeframe.M15: bars()}}, cfg)
    assert len(df) == 4  # RISK ignored, no data for NODATA, EV counted as ML
    assert list(df["gate"]) == ["LLM", "LLM", "ML", "ML"]
    assert list(df["exit_reason"]) == ["TP", "SL", "TP", "SL"]
    s = summarize(df)
    assert (
        s["LLM"]["rejected"] == 2
        and s["LLM"]["correct_rejections"] == 1
        and s["LLM"]["missed_opportunities"] == 1
    )
    assert s["ML"]["avoided_loss_pct"] > 0 and s["ML"]["missed_profit_pct"] > 0
    winner, loser = df.iloc[0]["ret_pct"], df.iloc[1]["ret_pct"]
    assert s["LLM"]["net_value_pct"] == pytest.approx(-(winner + loser), abs=1e-3)
    text = counterfactual_markdown(s)
    assert "| LLM | 2 | 1 | 1 |" in text and "**Summary:**" in text
    assert "LLM rejected 2 candidates" in one_liner(s)


def test_section_in_judge_report(tmp_path: Path) -> None:
    from siavash.reporting.judge_report import build_judge_report

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in [rec(0, "LLM"), rec(1, "ML")]))
    out = tmp_path / "out"
    p = build_judge_report(logs, out, frames={"BTC/TMN": {Timeframe.M15: bars()}}, cfg=load_config("config"))
    assert p["counterfactual"]["LLM"]["rejected"] == 1
    assert "Counterfactual: role of AI" in (out / "REPORT.md").read_text(encoding="utf-8")


def test_empty(tmp_path: Path) -> None:
    df = evaluate_rejections(tmp_path / "missing.jsonl", {}, load_config("config"))
    s = summarize(df)
    assert s["ML"]["rejected"] == 0 and "no evaluable" in one_liner(s)
