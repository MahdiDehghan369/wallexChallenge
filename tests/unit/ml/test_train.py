"""T-403: walk-forward is deterministic and leak-free."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from siavash.backtest.market_data import frames_from_15m
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market
from siavash.ml.dataset import build_dataset
from siavash.ml.train import make_folds, walk_forward, walkforward_report


def small_cfg() -> Any:
    cfg = load_config("config")
    ml = cfg.strategy.ml.model_copy(
        update={
            "train_days": 12,
            "test_days": 4,
            "embargo_hours": 12,
            "lgbm": {**cfg.strategy.ml.lgbm, "n_estimators": 50},
        }
    )
    return cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update={"ml": ml})})


@pytest.fixture(scope="module")
def ds() -> pd.DataFrame:
    return build_dataset(frames_from_15m(make_synthetic_market(seed=5, days=40)), load_config("config"))


def test_folds_have_embargo() -> None:
    ts = pd.Series(pd.date_range("2026-01-01", periods=40 * 96, freq="15min", tz="UTC"))
    folds = make_folds(ts, 12, 4, 12)
    assert len(folds) >= 5
    for f in folds:
        assert f.test_start - f.train_end == pd.Timedelta(hours=12)
        assert f.test_end - f.test_start == pd.Timedelta(days=4)
    assert folds[1].train_start - folds[0].train_start == pd.Timedelta(days=4)  # sliding window


def test_deterministic_and_no_leak(ds: pd.DataFrame) -> None:
    cfg = small_cfg()
    a = walk_forward(ds, cfg)
    b = walk_forward(ds, cfg)
    assert len(a.oof) > 0 and not a.fold_metrics.empty
    pd.testing.assert_frame_equal(a.oof, b.oof)  # identical predictions
    for f in a.folds:
        test_ts = a.oof[a.oof["fold"] == f.k]["ts"]
        assert ((test_ts >= f.test_start) & (test_ts < f.test_end)).all()
        assert not ((test_ts >= f.train_start) & (test_ts < f.test_start)).any()  # not in train + embargo
    assert a.model is not None
    assert list(a.model.feature_name_) == [c.replace(" ", "_") for c in a.features]
    text = walkforward_report(a, cfg.strategy.ml.threshold)
    assert "pooled OOF AUC" in text and "| fold |" in text
