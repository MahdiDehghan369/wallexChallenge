"""T-406: LgbmScorer — OK / SKIPPED / ERROR, EV formula, latency < 50 ms."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from siavash.config import load_config
from siavash.core.models import Candidate
from siavash.core.types import ComponentStatus, Regime, StrategyId
from siavash.ml.dataset import MODEL_COLUMNS, REGIME_COLUMNS
from siavash.ml.registry import load_model, save_model
from siavash.ml.scorer import LgbmScorer, ev_pct, regime_features
from siavash.ml.threshold import fit_calibrator
from siavash.signals.fee_filter import round_trip_fee_pct

C = Candidate(
    candidate_id="C1",
    ts=datetime(2026, 1, 1, tzinfo=UTC),
    symbol="BTC/TMN",
    strategy=StrategyId.S1_TREND_BREAKOUT,
    entry_ref_price=100.0,
    stop_loss=98.0,
    take_profit=104.0,
    tp1_price=103.0,
    time_stop_minutes=720,
    expected_move_pct=3.0,
    conditions={},
)


def ml_cfg(enabled: bool = True, version: str | None = "lgbm_v1") -> Any:
    cfg = load_config("config")
    ml = cfg.strategy.ml.model_copy(update={"enabled": enabled, "model_version": version, "threshold": 0.6})
    return cfg.model_copy(update={"strategy": cfg.strategy.model_copy(update={"ml": ml})})


@pytest.fixture(scope="module")
def models_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("models")
    rng = np.random.default_rng(1)
    X = pd.DataFrame(rng.normal(size=(500, len(MODEL_COLUMNS))), columns=MODEL_COLUMNS)
    y = (X["15m_rsi14"] > 0).astype(int)
    m = lgb.LGBMClassifier(
        n_estimators=40, num_leaves=7, seed=42, deterministic=True, num_threads=1, verbose=-1
    )
    m.fit(X, y)
    save_model(m, MODEL_COLUMNS, fit_calibrator(m.predict_proba(X)[:, 1], y), root=root)
    return root


def row(rsi: float) -> dict[str, float]:
    r = {f: 0.0 for f in MODEL_COLUMNS if f not in REGIME_COLUMNS}
    r["15m_rsi14"] = rsi
    return {**r, **regime_features(Regime.TREND_UP)}


def test_ok_score_and_ev(models_root: Path) -> None:
    cfg = ml_cfg()
    s = LgbmScorer(cfg, models_root=str(models_root))
    hi, lo = s.score(C, row(2.0)), s.score(C, row(-2.0))
    assert hi.status is ComponentStatus.OK and hi.model_version == "lgbm_v1" and hi.threshold == 0.6
    assert hi.p_win is not None and lo.p_win is not None and hi.p_win > lo.p_win
    assert hi.ev_pct == pytest.approx(ev_pct(C, hi.p_win, round_trip_fee_pct(cfg)), abs=1e-5)
    assert hi.ev_pct == pytest.approx(
        hi.p_win * 3.0 - (1 - hi.p_win) * 2.0 - round_trip_fee_pct(cfg), abs=1e-5
    )
    assert len(hi.top_features) == 5 and hi.top_features[0][0] == "15m_rsi14"


def test_skipped_and_error(models_root: Path) -> None:
    assert (
        LgbmScorer(ml_cfg(enabled=False), models_root=str(models_root)).score(C, row(1)).status
        is ComponentStatus.SKIPPED
    )
    missing = LgbmScorer(ml_cfg(version="lgbm_v9"), models_root=str(models_root))
    assert missing.score(C, row(1)).status is ComponentStatus.SKIPPED
    s = LgbmScorer(ml_cfg(), models_root=str(models_root))
    bad = s.score(C, {"15m_rsi14": "not-a-number"})  # type: ignore[dict-item]
    assert bad.status is ComponentStatus.ERROR and bad.model_version == "lgbm_v1"


def test_latency_under_50ms(models_root: Path) -> None:
    s = LgbmScorer(ml_cfg(), model=load_model("lgbm_v1", models_root))
    s.score(C, row(0.5))  # warm-up (explainer construction)
    t0 = time.perf_counter()
    for i in range(20):
        s.score(C, row(i / 10))
    assert (time.perf_counter() - t0) / 20 < 0.05
