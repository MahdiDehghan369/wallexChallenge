"""T-904: walk-forward helpers (fast; the engine path is covered by scripts/run_walkforward.py)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from siavash.backtest.market_data import frames_from_15m
from siavash.backtest.walkforward import (
    WalkForwardReport,
    benchmarks,
    candidate_metrics,
    cfg_set,
    data_range,
    equity_drawdown,
    inert_params,
    llm_status_counts,
    rule_filter,
    sensitivity_grid,
    structural_notes,
    with_fees,
    write_walkforward_report,
)
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market


@pytest.fixture(scope="module")
def frames() -> dict:
    return frames_from_15m(make_synthetic_market(seed=11, days=20))


def test_cfg_set_and_fees() -> None:
    cfg = load_config("config")
    c = cfg_set(cfg, "strategy.s3.z_entry", 3.0)
    assert c.strategy.s3.z_entry == 3.0 and cfg.strategy.s3.z_entry != 3.0  # original untouched
    f = with_fees(cfg, 0.1)
    assert f.risk.fees.maker_pct == 0.1 and f.risk.fees.taker_pct == 0.1
    assert with_fees(cfg, None) is cfg


def test_rule_filter_applies_regime_and_fee() -> None:
    cfg = load_config("config")
    allowed = cfg.strategy.regime.enabled_strategies
    regime = next(r for r, s in allowed.items() if s)
    ok_strategy = allowed[regime][0]
    ds = pd.DataFrame(
        {
            "ts": [pd.Timestamp("2026-01-01", tz="UTC")] * 3,
            "strategy": [ok_strategy, ok_strategy, "NOT_A_STRATEGY"],
            "regime": [regime] * 3,
            "expected_move_pct": [10.0, 0.1, 10.0],
            "ret_pct": [1.0, 1.0, 1.0],
        }
    )
    out = rule_filter(ds, cfg)
    assert len(out) == 1 and out.iloc[0]["expected_move_pct"] == 10.0


def test_sensitivity_benchmarks_and_report(frames: dict, tmp_path: Path) -> None:
    cfg = with_fees(load_config("config"), 0.05)
    start, end = data_range(frames)
    start = start + timedelta(days=5)
    sens = sensitivity_grid(cfg, frames, start, end, (1.5, 2.5), (1.0, 2.0), ["BTC/TMN", "ETH/TMN"])
    cols = {"cum_ret_pct", "sum_dd_pct", "equity_dd_pct", "score", "n_trades", "n_S1", "n_S3", "inert"}
    assert len(sens) == 4 and cols <= set(sens.columns)
    assert (sens["n_S1"] + sens["n_S2"] + sens["n_S3"] == sens["n_trades"]).all()
    bm = benchmarks(frames, start, end)
    assert bm["cash"] == 0.0 and "buy_hold_btc_return_pct" in bm
    no_trades = pd.DataFrame(
        {"ts": pd.Series([], dtype="datetime64[ns, UTC]"), "ret_pct": pd.Series([], dtype=float)}
    )
    empty = candidate_metrics(no_trades, start, end, cfg)
    assert empty["n_trades"] == 0 and empty["equity_dd_pct"] == 0.0
    cmp = pd.DataFrame(
        [
            {"variant": "+ML", "net_return_pct": 0.6, "n_trades": 1, "trades_per_day": 0.05},
            {
                "variant": "+ML+LLM (cached)",
                "net_return_pct": 0.6,
                "n_trades": 1,
                "trades_per_day": 0.05,
                "analyst_fallback": 10,
                "reviewer_fallback": 3,
                "cache_rows": 0,
            },
        ]
    )
    theta = pd.DataFrame(
        [
            {"theta": "no ML", "n_trades": 5, "cum_ret_pct": -1.0, "score": -0.1},
            {"theta": 0.6, "n_trades": 2, "cum_ret_pct": 2.0, "score": 1.0},
        ]
    )
    rep = WalkForwardReport(
        pd.DataFrame(), sens, theta, cmp, bm, (start, end), ["note"], (start - timedelta(days=30), end)
    )
    path = write_walkforward_report(rep, cfg, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "## Recommendation" in text and "parameter cells" in text
    assert (
        "IN-SAMPLE" in text
        and "consider enabling ML" not in text
        and "reports/diagnostics/SUMMARY.md" in text
    )
    assert "used no LLM answer" in text and "the cache was empty" in text
    assert 'fails the "regular activity" criterion' in text
    assert "not an account drawdown" in text
    assert (tmp_path / "sensitivity_return.png").exists() and (tmp_path / "sensitivity_dd.png").exists()


def test_equity_drawdown_and_inert() -> None:
    t = pd.DataFrame(
        {
            "ts": pd.to_datetime(["2026-01-01 00:00", "2026-01-01 00:15", "2026-01-01 00:30"], utc=True),
            "ret_pct": [10.0, -50.0, 10.0],
            "bars_held": [0, 0, 0],
        }
    )
    # 20 % per trade: 1 → 1.02 → 0.918 → 0.93636; DD = 1 − 0.918/1.02 = 10 %
    assert equity_drawdown(t, 0.2) == pytest.approx(10.0)
    # the second trade exits last → no drawdown before it, then −10 % of 1.0404
    t2 = t.assign(bars_held=[0, 10, 0])
    assert equity_drawdown(t2, 0.2) == pytest.approx(10.0)
    assert inert_params({"n_S1": 3, "n_S3": 0}) == "z_entry"
    assert inert_params({"n_S1": 0, "n_S3": 0}) == "z_entry, sl_atr"
    assert inert_params({"n_S1": 1, "n_S3": 1}) == "—"


def test_structural_note_s3_tp_cap_below_fee_filter() -> None:
    cfg = load_config("config")
    tight = cfg_set(with_fees(cfg, 0.35), "strategy.s3.tp_pct", 1.5)
    assert any("S3 can never pass the fee filter" in n for n in structural_notes(tight))
    assert structural_notes(cfg_set(with_fees(cfg, 0.05), "strategy.s3.tp_pct", 1.5)) == []


def test_llm_status_counts(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    (logs / "llm").mkdir(parents=True)
    recs = [
        {"type": "REGIME_UPDATE", "regime": {"status": "fallback"}},
        {"type": "REGIME_UPDATE", "regime": {"status": "ok"}},
        {"type": "ENTRY_CANDIDATE", "llm_review": {"status": "fallback"}},
        {"type": "ENTRY_CANDIDATE", "llm_review": None},
    ]
    (logs / "decisions.jsonl").write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    con = sqlite3.connect(logs / "llm" / "cache.sqlite")
    con.execute("create table cache (k text)")
    con.commit()
    con.close()
    c = llm_status_counts(logs)
    assert c == {"analyst_fallback": 1, "analyst_ok": 1, "reviewer_fallback": 1, "cache_rows": 0}


def test_data_range(frames: dict) -> None:
    s, e = data_range(frames)
    assert s.tzinfo is not None and e - s >= timedelta(days=19)
    assert s == datetime(2026, 1, 1, tzinfo=UTC) or s.year >= 2025
