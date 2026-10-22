"""T-402: dataset on synthetic data — columns, labels, no lookahead, determinism."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from siavash.backtest.market_data import frames_from_15m
from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.data.synthetic import make_synthetic_market
from siavash.features.feature_set import FEATURES
from siavash.ml.dataset import LABEL_COLUMNS, MODEL_COLUMNS, REGIME_COLUMNS, build_dataset, dataset_report


@pytest.fixture(scope="module")
def frames() -> dict:
    return frames_from_15m(make_synthetic_market(seed=3, days=30))


@pytest.fixture(scope="module")
def ds(frames: dict) -> pd.DataFrame:
    return build_dataset(frames, load_config("config"))


def test_columns_and_labels(ds: pd.DataFrame) -> None:
    assert len(ds) > 20
    for col in ["ts", "symbol", "strategy", "regime", *MODEL_COLUMNS, *LABEL_COLUMNS]:
        assert col in ds.columns
    assert set(ds["label"].unique()) <= {0, 1}
    assert set(ds["exit_reason"]) <= {"TP", "SL", "TIME"}
    assert (ds[REGIME_COLUMNS].sum(axis=1) == 1).all()
    assert set(ds["symbol"]) <= {"BTC/TMN", "ETH/TMN", "USDT/TMN"}
    assert ds["strategy"].nunique() >= 2  # regime filter is off: several strategies appear
    # label consistent with the net return for time stops
    t = ds[ds["exit_reason"] == "TIME"]
    assert ((t["ret_pct"] > 0).astype(int) == t["label"]).all()


def test_no_lookahead(frames: dict) -> None:
    """Truncating the data after a time must not change the features of earlier samples."""
    cfg = load_config("config")
    full = build_dataset(frames, cfg)
    cut = full["ts"].quantile(0.6)
    trunc_frames = {
        s: {tf: df[df["ts_open"] + pd.Timedelta(seconds=tf.seconds) <= cut] for tf, df in fr.items()}
        for s, fr in frames.items()
    }
    # keep a full outcome window after the cut so labels can still be computed
    for s in trunc_frames:
        m15 = frames[s][Timeframe.M15]
        trunc_frames[s][Timeframe.M15] = m15[m15["ts_open"] < cut + pd.Timedelta(days=1)]
    part = build_dataset(trunc_frames, cfg)
    key = ["ts", "symbol", "strategy"]
    a = full[full["ts"] <= cut - pd.Timedelta(days=1)].set_index(key)
    b = part.set_index(key).reindex(a.index)
    assert len(a) > 0 and b.notna().any(axis=1).all()
    assert np.allclose(a[FEATURES].to_numpy(dtype=float), b[FEATURES].to_numpy(dtype=float), equal_nan=True)


def test_deterministic_and_report(frames: dict, ds: pd.DataFrame) -> None:
    again = build_dataset(frames, load_config("config"))
    pd.testing.assert_frame_equal(ds, again)
    text = dataset_report(ds)
    assert "By strategy" in text and "label=1" in text
