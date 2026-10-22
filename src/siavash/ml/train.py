"""Walk-forward training (T-403).

LightGBM with fixed parameters from ``strategy.yaml > ml.lgbm`` (``seed=42``, ``deterministic=true``,
``num_threads=1``). Sliding folds: train ``train_days`` → embargo ``embargo_hours`` → test
``test_days``; the window then moves forward by ``test_days``. No test sample lies inside its fold's
train window or embargo. The final model is fit on all data. Out-of-fold predictions feed the
calibration / threshold step (T-404).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from siavash.logging import get_logger
from siavash.ml.dataset import MODEL_COLUMNS

log = get_logger(__name__)


@dataclass(frozen=True)
class Fold:
    k: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp  # exclusive
    test_start: pd.Timestamp  # = train_end + embargo
    test_end: pd.Timestamp  # exclusive


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    fold_metrics: pd.DataFrame
    oof: pd.DataFrame  # ts, symbol, strategy, fold, label, ret_pct, p_raw
    model: lgb.LGBMClassifier | None
    features: list[str]
    params: dict[str, Any] = field(default_factory=dict)


def make_folds(ts: pd.Series, train_days: float, test_days: float, embargo_hours: float) -> list[Fold]:
    start, end = pd.Timestamp(ts.min()), pd.Timestamp(ts.max())
    train, test, emb = timedelta(days=train_days), timedelta(days=test_days), timedelta(hours=embargo_hours)
    folds: list[Fold] = []
    k = 0
    while True:
        ts0 = start + k * test
        tr_end = ts0 + train
        te_start = tr_end + emb
        if te_start > end:
            break
        folds.append(Fold(k, ts0, tr_end, te_start, te_start + test))
        k += 1
    return folds


def lgbm_params(cfg: Any) -> dict[str, Any]:
    p = dict(cfg.strategy.ml.lgbm)
    p.setdefault("seed", 42)
    p.setdefault("deterministic", True)
    p.setdefault("num_threads", 1)
    p["verbose"] = -1
    return p


def fit_model(X: pd.DataFrame, y: pd.Series, params: dict[str, Any]) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(**params)
    model.fit(X, y)
    return model


def predict_raw(model: lgb.LGBMClassifier, X: pd.DataFrame) -> np.ndarray:
    """Uncalibrated P(label=1)."""
    proba = np.asarray(model.predict_proba(X))
    return proba[:, 1]


def _fold_stats(t: pd.DataFrame, theta: float) -> dict[str, Any]:
    y, p, r = t["label"], t["p_raw"], t["ret_pct"]
    sel = t[p >= theta]
    two = y.nunique() == 2
    return {
        "n_test": len(t),
        "auc": round(float(roc_auc_score(y, p)), 4) if two else None,
        "brier": round(float(brier_score_loss(y, p)), 4),
        "precision_at_theta": round(float(sel["label"].mean()), 4) if len(sel) else None,
        "n_filtered": len(sel),
        "mean_ret_all": round(float(r.mean()), 4),
        "mean_ret_filtered": round(float(sel["ret_pct"].mean()), 4) if len(sel) else None,
        "cum_ret_all": round(float(r.sum()), 4),
        "cum_ret_filtered": round(float(sel["ret_pct"].sum()), 4),
    }


def walk_forward(ds: pd.DataFrame, cfg: Any, features: list[str] | None = None) -> WalkForwardResult:
    feats = features or MODEL_COLUMNS
    ml = cfg.strategy.ml
    params = lgbm_params(cfg)
    ds = ds.sort_values(["ts", "symbol", "strategy"]).reset_index(drop=True)
    folds = make_folds(ds["ts"], ml.train_days, ml.test_days, ml.embargo_hours)
    oof_parts, stats = [], []
    for f in folds:
        tr = ds[(ds["ts"] >= f.train_start) & (ds["ts"] < f.train_end)]
        te = ds[(ds["ts"] >= f.test_start) & (ds["ts"] < f.test_end)]
        if te.empty or tr["label"].nunique() < 2:
            log.info("wf_fold_skipped", fold=f.k, n_train=len(tr), n_test=len(te))
            continue
        model = fit_model(tr[feats], tr["label"], params)
        part = te[["ts", "symbol", "strategy", "label", "ret_pct"]].copy()
        part["fold"] = f.k
        part["p_raw"] = predict_raw(model, te[feats])
        oof_parts.append(part)
        stats.append(
            {
                "fold": f.k,
                "train_start": f.train_start,
                "test_start": f.test_start,
                "n_train": len(tr),
                **_fold_stats(part, ml.threshold),
            }
        )
    oof = (
        pd.concat(oof_parts, ignore_index=True)
        if oof_parts
        else pd.DataFrame(columns=["ts", "symbol", "strategy", "label", "ret_pct", "fold", "p_raw"])
    )
    final = fit_model(ds[feats], ds["label"], params) if ds["label"].nunique() == 2 else None
    return WalkForwardResult(folds, pd.DataFrame(stats), oof, final, list(feats), params)


def walkforward_report(res: WalkForwardResult, theta: float) -> str:
    lines = ["# ML walk-forward", ""]
    fm = res.fold_metrics
    if fm.empty:
        return "\n".join([*lines, "_not enough data for a single fold_", ""])
    o = res.oof
    sel = o[o["p_raw"] >= theta]
    auc = roc_auc_score(o["label"], o["p_raw"]) if o["label"].nunique() == 2 else float("nan")
    lines += [
        f"- folds: {len(fm)} · out-of-fold samples: {len(o)} · features: {len(res.features)}",
        f"- pooled OOF AUC: {auc:.4f} · Brier: {brier_score_loss(o['label'], o['p_raw']):.4f}",
        f"- θ = {theta}: {len(sel)} of {len(o)} trades kept; mean net return "
        f"{o['ret_pct'].mean():.3f} % (all) vs {sel['ret_pct'].mean() if len(sel) else float('nan'):.3f} % (filtered)",
        "",
        "| fold | test start | train | test | AUC | Brier | prec@θ | kept | mean ret all | mean ret kept | cum all | cum kept |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in fm.itertuples(index=False):
        lines.append(
            f"| {r.fold} | {pd.Timestamp(r.test_start):%Y-%m-%d} | {r.n_train} | {r.n_test} | {_n(r.auc)} | {_n(r.brier)} "
            f"| {_n(r.precision_at_theta)} | {r.n_filtered} | {_n(r.mean_ret_all)} | {_n(r.mean_ret_filtered)} "
            f"| {_n(r.cum_ret_all)} | {_n(r.cum_ret_filtered)} |"
        )
    return "\n".join([*lines, ""])


def _n(v: Any) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.3f}"
