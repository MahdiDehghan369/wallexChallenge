"""T-905: report files are written from a BacktestResult (fast: fake logs, no engine run)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from siavash.backtest.engine import BacktestResult, synthetic_md_factory
from siavash.backtest.report import REPORT_FILES, data_hash, write_report
from siavash.config import load_config
from siavash.engine.clock import SimClock
from siavash.reporting.metrics import metrics_from_logs

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _fake_logs(logs: Path) -> None:
    logs.mkdir(parents=True)
    eq = [5_000_000, 5_020_000, 4_990_000, 5_050_000]
    pd.DataFrame(
        {
            "ts": [T0 + timedelta(hours=i) for i in range(4)],
            "equity_tmn": eq,
            "exposure_tmn": [0, 1e6, 1e6, 0],
        }
    ).to_csv(logs / "equity.csv", index=False)
    fills = pd.DataFrame(
        [
            {
                "ts": T0,
                "symbol": "BTC/TMN",
                "side": "buy",
                "price": 1e9,
                "qty": 0.001,
                "notional_tmn": 1e6,
                "fee": 1000,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1",
            },
            {
                "ts": T0 + timedelta(hours=3),
                "symbol": "BTC/TMN",
                "side": "sell",
                "price": 1.05e9,
                "qty": 0.001,
                "notional_tmn": 1.05e6,
                "fee": 1050,
                "fee_asset": "TMN",
                "position_id": "P1",
                "strategy": "S1",
            },
        ]
    )
    fills.to_csv(logs / "fills.csv", index=False)
    (logs / "decisions.jsonl").write_text("")


def test_write_report(tmp_path: Path) -> None:
    cfg = load_config("config")
    out = tmp_path / "run1"
    _fake_logs(out / "logs")
    metrics, trades = metrics_from_logs(out / "logs", 5_000_000)
    md = synthetic_md_factory(cfg, 3, seed=1)(SimClock(T0))
    res = BacktestResult(
        "run1",
        out,
        T0,
        T0 + timedelta(hours=3),
        ["BTC/TMN"],
        metrics,
        trades,
        "off",
        "synthetic",
        {
            "config": cfg,
            "md": md,
            "runtime": {"git_sha": "abc123", "config_hash": "sha256:x"},
            "model_version": None,
        },
    )
    report = write_report(res)
    for f in REPORT_FILES:
        assert (out / f).exists() and (out / f).stat().st_size > 0, f
    text = report.read_text(encoding="utf-8")
    assert "Headline" in text and "By strategy" in text and "| S1 | 1 |" in text
    rt = json.loads((out / "runtime.json").read_text())
    assert rt["git_sha"] == "abc123" and rt["data_hash"].startswith("sha256:")
    assert set(rt["prompt_versions"]) >= {"reviewer_v1", "analyst_v1"}
    assert rt["data_hash"] == data_hash(md)  # deterministic
    assert json.loads((out / "metrics.json").read_text())["n_trades"] == 1
    assert yaml.safe_load((out / "config_used.yaml").read_text(encoding="utf-8"))["risk"]
    assert "secrets" not in (out / "config_used.yaml").read_text(encoding="utf-8")
    assert len(pd.read_csv(out / "trades.csv")) == 1
