"""Model registry (T-405).

``models/lgbm_v{N}/``: ``model.txt`` (LightGBM booster), ``calibrator.pkl`` (isotonic, may be absent),
``metadata.json`` (created_at, feature_set_version, features, data range, data hash, metrics,
threshold, seed, git_sha). ``load_model`` refuses a model whose ``feature_set_version`` differs from
the running code's :data:`FEATURE_SET_VERSION`.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from siavash.features.feature_set import FEATURE_SET_VERSION
from siavash.runtime import detect_git_sha

PREFIX = "lgbm_v"
_VERSION_RE = re.compile(rf"^{PREFIX}(\d+)$")


class ModelMismatch(Exception):
    """The saved model was trained on another feature set version."""


class ModelNotFound(FileNotFoundError):
    pass


def frame_hash(df: pd.DataFrame) -> str:
    """Content hash of a dataset (row order and values)."""
    h = pd.util.hash_pandas_object(df.reset_index(drop=True), index=False).to_numpy()
    return "sha256:" + hashlib.sha256(h.tobytes()).hexdigest()


def list_versions(root: str | Path = "models") -> list[str]:
    r = Path(root)
    if not r.exists():
        return []
    found = [
        (int(m.group(1)), p.name) for p in r.iterdir() if p.is_dir() and (m := _VERSION_RE.match(p.name))
    ]
    return [name for _, name in sorted(found)]


def next_version(root: str | Path = "models") -> str:
    versions = list_versions(root)
    n = int(_VERSION_RE.match(versions[-1]).group(1)) + 1 if versions else 1  # type: ignore[union-attr]
    return f"{PREFIX}{n}"


def save_model(
    model: lgb.LGBMClassifier | lgb.Booster,
    features: list[str],
    calibrator: Any = None,
    metadata: dict[str, Any] | None = None,
    root: str | Path = "models",
    version: str | None = None,
) -> str:
    """Write a new model version; returns its name (``lgbm_vN``)."""
    version = version or next_version(root)
    d = Path(root) / version
    d.mkdir(parents=True, exist_ok=False)
    booster = model.booster_ if isinstance(model, lgb.LGBMClassifier) else model
    booster.save_model(str(d / "model.txt"))
    if calibrator is not None:
        with (d / "calibrator.pkl").open("wb") as fh:
            pickle.dump(calibrator, fh)
    meta = {
        "version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "feature_set_version": FEATURE_SET_VERSION,
        "features": list(features),
        "git_sha": detect_git_sha(),
        **(metadata or {}),
    }
    (d / "metadata.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return version


@dataclass
class LoadedModel:
    version: str
    booster: lgb.Booster
    calibrator: Any
    metadata: dict[str, Any]
    features: list[str]
    _explainer: Any = field(default=None, repr=False)

    def frame(self, rows: pd.DataFrame | dict[str, Any]) -> pd.DataFrame:
        df = pd.DataFrame([rows]) if isinstance(rows, dict) else rows
        return df.reindex(columns=self.features).astype(float)

    def predict_raw(self, rows: pd.DataFrame | dict[str, Any]) -> np.ndarray:
        return np.asarray(self.booster.predict(self.frame(rows)), dtype=float)

    def predict(self, rows: pd.DataFrame | dict[str, Any]) -> np.ndarray:
        """Calibrated P(win) (raw probability when no calibrator was saved)."""
        p = self.predict_raw(rows)
        return np.asarray(self.calibrator.predict(p), dtype=float) if self.calibrator is not None else p

    @property
    def explainer(self) -> Any:
        if self._explainer is None:
            import shap

            self._explainer = shap.TreeExplainer(self.booster)
        return self._explainer


def load_model(version: str | None = None, root: str | Path = "models") -> LoadedModel:
    """Load ``version`` (latest when None); raises ``ModelNotFound`` / ``ModelMismatch``."""
    if version is None:
        versions = list_versions(root)
        if not versions:
            raise ModelNotFound(f"no model in {root}")
        version = versions[-1]
    d = Path(root) / version
    if not (d / "model.txt").exists():
        raise ModelNotFound(str(d))
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    if meta.get("feature_set_version") != FEATURE_SET_VERSION:
        raise ModelMismatch(
            f"{version} was trained on {meta.get('feature_set_version')}, code uses {FEATURE_SET_VERSION}"
        )
    calibrator = None
    if (d / "calibrator.pkl").exists():
        with (d / "calibrator.pkl").open("rb") as fh:
            calibrator = pickle.load(fh)
    booster = lgb.Booster(model_file=str(d / "model.txt"))
    return LoadedModel(version, booster, calibrator, meta, list(meta["features"]))
