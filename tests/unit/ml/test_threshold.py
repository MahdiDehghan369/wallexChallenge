"""T-404: calibration, threshold grid, selection rule and the honest "disable ML" outcome."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from siavash.ml.threshold import THETAS, cross_calibrated, max_drawdown, select_threshold, threshold_report


def oof(informative: bool, n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.2, 0.9, n)
    win = rng.uniform(size=n) < (p if informative else 0.5)
    ret = np.where(win, 1.0, -0.8)
    return pd.DataFrame(
        {
            "ts": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
            "fold": np.repeat(np.arange(4), n // 4),
            "label": win.astype(int),
            "ret_pct": ret,
            "p_raw": p,
        }
    )


def test_grid_and_drawdown() -> None:
    assert THETAS[0] == 0.45 and THETAS[-1] == 0.70 and len(THETAS) == 11
    assert max_drawdown(pd.Series([1.0, -2.0, 0.5, -1.0, 3.0])) == pytest.approx(2.5)
    assert max_drawdown(pd.Series([1.0, 1.0])) == 0.0


def test_calibration_is_monotone_and_bounded() -> None:
    o = oof(True)
    cal = cross_calibrated(o)
    assert cal.between(0, 1).all() and cal.notna().all()
    for _, g in o.assign(cal=cal).groupby("fold"):
        s = g.sort_values("p_raw")["cal"].to_numpy()
        assert (np.diff(s) >= -1e-12).all()


def test_informative_model_selects_threshold() -> None:
    res = select_threshold(oof(True), min_trades_per_day=3)
    assert res.theta is not None and res.recommend_enabled
    row = res.table[res.table["theta"] == res.theta].iloc[0]
    assert row["trades_per_day"] >= 3 and row["score"] > res.baseline["score"]
    assert set(res.per_fold_best) == {0, 1, 2, 3} and res.calibrator is not None
    assert "selected **θ" in threshold_report(res)


def test_useless_model_recommends_disabling() -> None:
    res = select_threshold(oof(False, seed=1), min_trades_per_day=3)
    text = threshold_report(res)
    assert not res.recommend_enabled
    assert "ml.enabled: false" in text


def test_frequency_constraint() -> None:
    res = select_threshold(oof(True), min_trades_per_day=1000)
    assert res.theta is None and not res.recommend_enabled
    assert "no θ keeps" in threshold_report(res)
    assert math.isfinite(res.baseline["cum_ret_pct"])
