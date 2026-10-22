"""T-405: save / load round trip, version numbering, feature-set check, SHAP top-k."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from siavash.features.feature_set import FEATURE_SET_VERSION
from siavash.ml.explain import explain
from siavash.ml.registry import (
    ModelMismatch,
    ModelNotFound,
    frame_hash,
    list_versions,
    load_model,
    save_model,
)
from siavash.ml.threshold import fit_calibrator

FEATS = ["a", "b", "c", "d"]


@pytest.fixture(scope="module")
def trained() -> tuple[lgb.LGBMClassifier, pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(400, 4)), columns=FEATS)
    y = pd.Series((X["a"] + 0.3 * rng.normal(size=400) > 0).astype(int))
    m = lgb.LGBMClassifier(
        n_estimators=30, num_leaves=7, seed=42, deterministic=True, num_threads=1, verbose=-1
    )
    m.fit(X, y)
    return m, X, y


def test_round_trip(tmp_path: Path, trained) -> None:  # type: ignore[no-untyped-def]
    m, X, y = trained
    cal = fit_calibrator(m.predict_proba(X)[:, 1], y)
    v1 = save_model(m, FEATS, cal, {"threshold": 0.6, "data_hash": frame_hash(X)}, root=tmp_path)
    v2 = save_model(m, FEATS, None, {}, root=tmp_path)
    assert (v1, v2) == ("lgbm_v1", "lgbm_v2") and list_versions(tmp_path) == ["lgbm_v1", "lgbm_v2"]
    meta = json.loads((tmp_path / v1 / "metadata.json").read_text())
    assert (
        meta["feature_set_version"] == FEATURE_SET_VERSION
        and meta["threshold"] == 0.6
        and meta["features"] == FEATS
    )
    for f in ("model.txt", "calibrator.pkl", "metadata.json"):
        assert (tmp_path / v1 / f).exists()

    lm = load_model(v1, tmp_path)
    np.testing.assert_allclose(lm.predict_raw(X), m.predict_proba(X)[:, 1], rtol=1e-9)
    p = lm.predict(X.iloc[:5])
    assert ((p >= 0) & (p <= 1)).all()
    assert load_model(None, tmp_path).version == "lgbm_v2"  # latest
    # a dict row with missing / extra keys is aligned to the training features
    assert lm.predict({"a": 1.0, "b": 0.0, "zzz": 5.0}).shape == (1,)


def test_feature_set_mismatch_and_missing(tmp_path: Path, trained) -> None:  # type: ignore[no-untyped-def]
    m, _, _ = trained
    v = save_model(m, FEATS, root=tmp_path)
    meta_p = tmp_path / v / "metadata.json"
    meta = json.loads(meta_p.read_text())
    meta["feature_set_version"] = "fs_v0"
    meta_p.write_text(json.dumps(meta))
    with pytest.raises(ModelMismatch):
        load_model(v, tmp_path)
    with pytest.raises(ModelNotFound):
        load_model("lgbm_v99", tmp_path)
    with pytest.raises(ModelNotFound):
        load_model(None, tmp_path / "empty")


def test_explain_top_k(tmp_path: Path, trained) -> None:  # type: ignore[no-untyped-def]
    m, X, _ = trained
    lm = load_model(save_model(m, FEATS, root=tmp_path), tmp_path)
    top = explain(lm, X.iloc[0].to_dict(), k=3)
    assert len(top) == 3 and all(isinstance(f, str) and isinstance(v, float) for f, v in top)
    assert abs(top[0][1]) >= abs(top[1][1]) >= abs(top[2][1])
    assert top[0][0] == "a"  # the only informative feature dominates
