"""T-805: latency recorder and summary."""

from __future__ import annotations

from pathlib import Path

import pytest

from siavash.reporting.latency import LatencyRecorder, load, summarize


def test_record_and_summarize(tmp_path: Path) -> None:
    rec = LatencyRecorder(tmp_path / "latency.jsonl")
    for ms in range(1, 101):
        rec.record("order_sent_to_ack", float(ms), symbol="BTC/TMN")
    rec.record("candle_close_to_decision", 1500.0)
    rec.record("exit_trigger_to_exit_fill", -5.0)  # clamped to 0
    s = summarize(load(tmp_path / "latency.jsonl"))
    assert s["order_sent_to_ack"]["n"] == 100
    assert s["order_sent_to_ack"]["p50"] == pytest.approx(50.5) and s["order_sent_to_ack"][
        "p95"
    ] == pytest.approx(95.05)
    assert s["order_sent_to_ack"]["max"] == 100 and s["exit_trigger_to_exit_fill"]["max"] == 0
    with pytest.raises(ValueError):
        rec.record("bogus", 1)
    assert summarize([]) == {}
