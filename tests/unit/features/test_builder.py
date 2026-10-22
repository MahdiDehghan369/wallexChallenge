"""T-303: feature builder incl. anti-lookahead."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from siavash.core.types import Timeframe
from siavash.features.builder import add_cross_features, build_features, latest_row
from siavash.features.feature_set import AUX_COLUMNS, FEATURE_SET_VERSION, FEATURES, TF_FEATURES
from tests.fakes.synthetic import make_synthetic_market, resample_ohlcv

TF = {Timeframe.M15: 900, Timeframe.H1: 3600, Timeframe.H4: 14400}


@pytest.fixture(scope="module")
def candles() -> dict[Timeframe, pd.DataFrame]:
    m15 = make_synthetic_market(seed=5, days=45)["BTC/TMN"]
    return {
        Timeframe.M15: m15,
        Timeframe.H1: resample_ohlcv(m15, "1h"),
        Timeframe.H4: resample_ohlcv(m15, "4h"),
    }


@pytest.fixture(scope="module")
def full(candles: dict[Timeframe, pd.DataFrame]) -> pd.DataFrame:
    return build_features(candles)


def _truncate(candles: dict[Timeframe, pd.DataFrame], t: pd.Timestamp) -> dict[Timeframe, pd.DataFrame]:
    return {
        tf: df[df["ts_open"] + timedelta(seconds=TF[tf]) <= t].reset_index(drop=True)
        for tf, df in candles.items()
    }


def test_feature_names() -> None:
    assert FEATURE_SET_VERSION == "fs_v1"
    assert len(TF_FEATURES) == 60 and len(set(FEATURES)) == len(FEATURES)
    assert "15m_rsi14" in FEATURES and "4h_ema200_dist" in FEATURES and "x_implied_z" in FEATURES


def test_shape_and_index(full: pd.DataFrame, candles: dict[Timeframe, pd.DataFrame]) -> None:
    assert len(full) == len(candles[Timeframe.M15])
    assert full.index[0] == candles[Timeframe.M15]["ts_open"].iloc[0] + timedelta(minutes=15)
    assert set(TF_FEATURES + AUX_COLUMNS) <= set(full.columns)
    late = full.iloc[-1]
    assert not late[TF_FEATURES].isna().any()


def test_no_lookahead_random_times(candles: dict[Timeframe, pd.DataFrame], full: pd.DataFrame) -> None:
    rng = np.random.default_rng(1)
    idx = full.index[300:]
    for t in rng.choice(idx, size=20, replace=False):
        t = pd.Timestamp(t)
        part = build_features(_truncate(candles, t))
        assert part.index[-1] == t
        a = part.iloc[-1][TF_FEATURES + AUX_COLUMNS].to_numpy(dtype=float)
        b = full.loc[t][TF_FEATURES + AUX_COLUMNS].to_numpy(dtype=float)
        assert np.array_equal(np.isnan(a), np.isnan(b)), t
        m = ~np.isnan(a)
        np.testing.assert_allclose(a[m], b[m], rtol=1e-9, atol=1e-9, err_msg=str(t))


def test_higher_tf_uses_last_closed_candle(
    candles: dict[Timeframe, pd.DataFrame], full: pd.DataFrame
) -> None:
    h1 = candles[Timeframe.H1].set_index("ts_open")
    day = candles[Timeframe.M15]["ts_open"].iloc[0].normalize() + timedelta(days=3)
    t_mid = day + timedelta(hours=10, minutes=15)  # 1h candle 10:00 not closed yet
    t_end = day + timedelta(hours=11)  # 1h candle 10:00 just closed
    assert full.loc[t_mid, "1h_close"] == h1.loc[day + timedelta(hours=9), "close"]
    assert full.loc[t_end, "1h_close"] == h1.loc[day + timedelta(hours=10), "close"]


def test_latest_row_matches(candles: dict[Timeframe, pd.DataFrame], full: pd.DataFrame) -> None:
    row = latest_row(candles)
    assert row["15m_rsi14"] == pytest.approx(full.iloc[-1]["15m_rsi14"])


def test_add_cross_features(full: pd.DataFrame) -> None:
    extra = pd.DataFrame({"x_implied_z": np.arange(len(full), dtype=float)}, index=full.index)
    out = add_cross_features(full, extra)
    assert out["x_implied_z"].iloc[-1] == len(full) - 1
    assert len(out) == len(full)
