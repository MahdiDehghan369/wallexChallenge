"""T-1201: judge report from a fake logs directory."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from siavash.cli import app
from siavash.reporting.judge_report import build_judge_report

T0 = datetime(2026, 11, 5, 18, 0, tzinfo=UTC)  # 21:30 Tehran -> the sell lands on the next Tehran day


def cand(i: int, outcome: str, stage: str | None, llm: dict | None, ml_status: str = "skipped") -> dict:
    return {
        "decision_id": f"D{i}",
        "ts": (T0 + timedelta(minutes=i)).isoformat(),
        "type": "ENTRY_CANDIDATE",
        "symbol": "BTC/TMN",
        "strategy": "S1_TREND_BREAKOUT",
        "outcome_action": outcome,
        "rejected_at": stage,
        "llm_review": llm,
        "ml": {"status": ml_status},
        "position_id": "P1" if outcome == "ORDER_PLACED" else None,
    }


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    pd.DataFrame(
        {
            "ts": [T0 + timedelta(hours=h) for h in range(5)],
            "equity_tmn": [5e6, 5.01e6, 4.98e6, 5.03e6, 5.04e6],
        }
    ).to_csv(d / "equity.csv", index=False)
    pd.DataFrame(
        [
            {
                "ts": T0,
                "symbol": "BTC/TMN",
                "side": "buy",
                "price": 1e9,
                "qty": 0.002,
                "notional_tmn": 2e6,
                "fee": 4000,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1_TREND_BREAKOUT",
            },
            {
                "ts": T0 + timedelta(hours=4),
                "symbol": "BTC/TMN",
                "side": "sell",
                "price": 1.03e9,
                "qty": 0.002,
                "notional_tmn": 2.06e6,
                "fee": 4120,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1_TREND_BREAKOUT",
            },
        ]
    ).to_csv(d / "fills.csv", index=False)
    decisions = [
        cand(1, "ORDER_PLACED", None, {"status": "ok", "verdict": "APPROVE"}, "ok"),
        cand(2, "REJECTED", "LLM", {"status": "ok", "verdict": "REJECT"}),
        cand(3, "REJECTED", "RISK", {"status": "fallback", "verdict": "APPROVE"}),
        cand(4, "REJECTED", "FEE_FILTER", None),
        cand(5, "REJECTED", "ML", None, "ok"),
    ]
    (d / "decisions.jsonl").write_text("\n".join(json.dumps(x) for x in decisions) + "\n")
    http = [
        {"event": "http_request", "status": s, "ms": ms}
        for s, ms in [(200, 50), (200, 70), (500, 900), (None, 10000)]
    ]
    (d / "system.log").write_text("\n".join(json.dumps(x) for x in http) + '\n{"event": "other"}\nnot json\n')
    (d / "latency.jsonl").write_text(
        "\n".join(
            json.dumps({"ts": T0.isoformat(), "kind": "candle_close_to_decision", "ms": m})
            for m in (100, 200, 300)
        )
    )
    return d


def test_judge_report(logs: Path, tmp_path: Path) -> None:
    out = tmp_path / "reports" / "live"
    p = build_judge_report(logs, out)
    j = p["judge"]
    assert j["Number of Trades"] == 1 and j["Trading Volume (TMN)"] == pytest.approx(4.06e6)
    assert j["Net Return %"] == pytest.approx(0.8)  # (5.04M − 5M) / 5M
    assert j["API error rate %"] == 50.0
    assert p["api"]["requests"] == 4
    assert p["ai"] == {
        "candidates": 5,
        "orders_placed": 1,
        "rejected_by_fee_filter": 1,
        "rejected_by_ml": 1,
        "rejected_by_llm": 1,
        "rejected_by_risk": 1,
        "llm_reviewed": 2,
        "llm_approve_pct": 50.0,
        "llm_fallbacks": 1,
        "ml_scored": 2,
    }
    assert [d["fills"] for d in p["per_day"]] == [1, 1]  # buy and sell fall on two Tehran days
    text = (out / "REPORT.md").read_text(encoding="utf-8")
    for title in (
        "Number of Trades",
        "Trading Volume (TMN)",
        "Average Trade Volume",
        "Net Return %",
        "Max Drawdown %",
        "API error rate",
        "Trades per challenge day",
        "Role of AI",
        "p50",
    ):
        assert title in text
    for f in ("equity.png", "drawdown.png", "metrics.json", "trades.csv", "trades_per_day.csv"):
        assert (out / f).exists()


def test_cli_report(logs: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli_out"
    r = CliRunner().invoke(app, ["report", "--logs", str(logs), "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert "Net Return %" in r.output and (out / "REPORT.md").exists()


def test_empty_logs(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    p = build_judge_report(tmp_path / "logs", tmp_path / "out")
    assert p["judge"]["Number of Trades"] == 0 and p["api"]["requests"] == 0
