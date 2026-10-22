"""T-1204: dashboard HTML from fake logs / state (and from nothing, without crashing)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from siavash.reporting.dashboard import build_dashboard, main

T0 = datetime(2026, 11, 5, 9, tzinfo=UTC)


def test_dashboard_from_fake_logs(tmp_path: Path) -> None:
    logs, state = tmp_path / "logs", tmp_path / "state"
    logs.mkdir()
    state.mkdir()
    pd.DataFrame(
        {"ts": [T0 + timedelta(minutes=5 * i) for i in range(4)], "equity_tmn": [5e6, 5.01e6, 4.95e6, 5.02e6]}
    ).to_csv(logs / "equity.csv", index=False)
    (logs / "regime.jsonl").write_text(json.dumps({"regime": "TREND_UP"}) + "\n")
    decisions = [
        {
            "ts": T0.isoformat(),
            "type": "ENTRY_CANDIDATE",
            "symbol": "BTC/TMN",
            "outcome_action": "REJECTED",
            "final_reason": "S1 breakout <script>; rejected: LLM",
        }
    ]
    (logs / "decisions.jsonl").write_text("\n".join(json.dumps(d) for d in decisions))
    with sqlite3.connect(state / "state.sqlite") as con:
        con.execute("CREATE TABLE positions(position_id TEXT, symbol TEXT, status TEXT, data TEXT)")
        con.execute("CREATE TABLE breaker(id INTEGER, state TEXT, reason TEXT, since TEXT)")
        con.execute(
            "INSERT INTO positions VALUES ('P1','ETH/TMN','OPEN',?)",
            (
                json.dumps(
                    {"symbol": "ETH/TMN", "strategy": "S2_MEAN_REVERSION", "qty": "0.01", "status": "OPEN"}
                ),
            ),
        )
        con.execute("INSERT INTO breaker VALUES (1, 'NO_NEW_ENTRIES', 'daily loss', '')")
    out = build_dashboard(logs, state, tmp_path / "dash.html")
    page = out.read_text(encoding="utf-8")
    assert "<polyline" in page and "TREND_UP" in page and "NO_NEW_ENTRIES" in page and "ETH/TMN" in page
    assert "&lt;script&gt;" in page and "<script>" not in page  # reasons are escaped


def test_dashboard_without_data(tmp_path: Path) -> None:
    assert (
        main(
            [
                "--logs",
                str(tmp_path / "none"),
                "--state",
                str(tmp_path / "none"),
                "--out",
                str(tmp_path / "d.html"),
            ]
        )
        == 0
    )
    assert "no data yet" in (tmp_path / "d.html").read_text(encoding="utf-8")
