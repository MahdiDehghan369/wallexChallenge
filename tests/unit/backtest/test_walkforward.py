"""T-904: walk-forward helpers (fast; the engine path is covered by scripts/run_walkforward.py)."""

from __future__ import annotations

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
    rule_filter,
    sensitivity_grid,
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
    assert len(sens) == 4 and {"cum_ret_pct", "max_dd_pct", "score", "n_trades"} <= set(sens.columns)
    bm = benchmarks(frames, start, end)
    assert bm["cash"] == 0.0 and "buy_hold_btc_return_pct" in bm
    no_trades = pd.DataFrame(
        {"ts": pd.Series([], dtype="datetime64[ns, UTC]"), "ret_pct": pd.Series([], dtype=float)}
    )
    empty = candidate_metrics(no_trades, start, end)
    assert empty["n_trades"] == 0
    rep = WalkForwardReport(pd.DataFrame(), sens, pd.DataFrame(), pd.DataFrame(), bm, (start, end), ["note"])
    path = write_walkforward_report(rep, cfg, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "## Recommendation" in text and "parameter cells" in text
    assert (tmp_path / "sensitivity_return.png").exists() and (tmp_path / "sensitivity_dd.png").exists()


def test_data_range(frames: dict) -> None:
    s, e = data_range(frames)
    assert s.tzinfo is not None and e - s >= timedelta(days=19)
    assert s == datetime(2026, 1, 1, tzinfo=UTC) or s.year >= 2025
