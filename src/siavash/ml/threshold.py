"""Calibration and threshold selection (T-404).

- Isotonic calibration on out-of-fold (OOF) predictions. For *evaluation* each fold is calibrated
  with a calibrator fit on the other folds (leave-one-fold-out), so the θ choice is not scored on
  the data the calibrator saw; the final calibrator is fit on all OOF predictions.
- For θ ∈ {0.45 … 0.70, step 0.025}: cumulative net return, number of trades and max drawdown of the
  filtered trades (in time order, additive % returns) over all folds.
- θ = argmax(return / max_drawdown) subject to ``trades/day ≥ ml.min_trades_per_day``.
- If no θ beats "no filter" (all trades) on the same score, ML should stay disabled — the report
  says so explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

THETAS: tuple[float, ...] = tuple(round(0.45 + 0.025 * i, 3) for i in range(11))  # 0.45 … 0.70


@dataclass
class ThresholdResult:
    theta: float | None  # None: no θ satisfies the trade-frequency constraint
    table: pd.DataFrame  # one row per θ
    baseline: dict[str, Any]  # no filter
    per_fold_best: dict[int, float | None]
    recommend_enabled: bool
    calibrator: IsotonicRegression | None
    min_trades_per_day: float


def fit_calibrator(p: np.ndarray | pd.Series, y: np.ndarray | pd.Series) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(np.asarray(p, dtype=float), np.asarray(y, dtype=float))
    return iso


def cross_calibrated(oof: pd.DataFrame) -> pd.Series:
    """Leave-one-fold-out calibrated probabilities (a single fold is calibrated on itself)."""
    out = pd.Series(np.nan, index=oof.index, dtype=float)
    folds = sorted(oof["fold"].unique())
    for k in folds:
        test = oof["fold"] == k
        train = ~test if len(folds) > 1 else test
        if oof.loc[train, "label"].nunique() < 2:
            out[test] = oof.loc[test, "p_raw"]
            continue
        iso = fit_calibrator(oof.loc[train, "p_raw"], oof.loc[train, "label"])
        out[test] = iso.predict(oof.loc[test, "p_raw"].to_numpy(dtype=float))
    return out


def max_drawdown(rets: pd.Series) -> float:
    """Max drawdown (percentage points) of the additive equity curve of per-trade net returns."""
    if rets.empty:
        return 0.0
    eq = np.concatenate([[0.0], rets.cumsum().to_numpy(dtype=float)])
    return float((np.maximum.accumulate(eq) - eq).max())


def _score(cum: float, dd: float) -> float:
    if dd <= 1e-12:
        return math.inf if cum > 0 else cum
    return cum / dd


def evaluate(trades: pd.DataFrame, days: float) -> dict[str, Any]:
    t = trades.sort_values("ts")
    cum, dd = float(t["ret_pct"].sum()), max_drawdown(t["ret_pct"])
    return {
        "n_trades": len(t),
        "trades_per_day": round(len(t) / days, 3) if days > 0 else 0.0,
        "cum_ret_pct": round(cum, 4),
        "mean_ret_pct": round(float(t["ret_pct"].mean()), 4) if len(t) else None,
        "max_dd_pct": round(dd, 4),
        "score": _score(cum, dd) if len(t) else -math.inf,
    }


def _best(rows: list[dict[str, Any]], min_tpd: float) -> dict[str, Any] | None:
    ok = [r for r in rows if r["trades_per_day"] >= min_tpd and r["n_trades"] > 0]
    return max(ok, key=lambda r: (r["score"], r["cum_ret_pct"])) if ok else None


def select_threshold(oof: pd.DataFrame, min_trades_per_day: float) -> ThresholdResult:
    if oof.empty:
        return ThresholdResult(None, pd.DataFrame(), {}, {}, False, None, min_trades_per_day)
    o = oof.copy()
    o["p_cal"] = cross_calibrated(o)
    days = max((o["ts"].max() - o["ts"].min()).total_seconds() / 86400, 1e-9)
    rows = [{"theta": th, **evaluate(o[o["p_cal"] >= th], days)} for th in THETAS]
    baseline = {"theta": None, **evaluate(o, days)}
    best = _best(rows, min_trades_per_day)
    per_fold: dict[int, float | None] = {}
    for k, g in o.groupby("fold"):
        fdays = max((g["ts"].max() - g["ts"].min()).total_seconds() / 86400, 1e-9)
        fb = _best(
            [{"theta": th, **evaluate(g[g["p_cal"] >= th], fdays)} for th in THETAS], min_trades_per_day
        )
        per_fold[int(k)] = fb["theta"] if fb else None
    beats = best is not None and best["score"] > baseline["score"]
    calibrator = fit_calibrator(o["p_raw"], o["label"]) if o["label"].nunique() == 2 else None
    return ThresholdResult(
        best["theta"] if best else None,
        pd.DataFrame(rows),
        baseline,
        per_fold,
        beats,
        calibrator,
        min_trades_per_day,
    )


def _f(v: Any, nd: int = 3) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞" if v > 0 else "−∞"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def threshold_report(res: ThresholdResult) -> str:
    lines = ["## Calibration and threshold (T-404)", ""]
    if res.table.empty:
        return "\n".join([*lines, "_no out-of-fold predictions_", ""])
    b = res.baseline
    lines += [
        "Isotonic calibration, leave-one-fold-out for evaluation. Score = cumulative net return / max "
        f"drawdown of the filtered trades; constraint: ≥ {res.min_trades_per_day} trades/day.",
        "",
        "| θ | trades | trades/day | cum ret % | mean ret % | max DD % | score |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| no filter | {b['n_trades']} | {_f(b['trades_per_day'])} | {_f(b['cum_ret_pct'])} | {_f(b['mean_ret_pct'])} "
        f"| {_f(b['max_dd_pct'])} | {_f(b['score'])} |",
    ]
    for r in res.table.itertuples(index=False):
        mark = " **←**" if res.theta is not None and abs(r.theta - res.theta) < 1e-9 else ""
        lines.append(
            f"| {r.theta:.3f}{mark} | {r.n_trades} | {_f(r.trades_per_day)} | {_f(r.cum_ret_pct)} | {_f(r.mean_ret_pct)} "
            f"| {_f(r.max_dd_pct)} | {_f(r.score)} |"
        )
    stab = ", ".join(f"fold {k}: {_f(v)}" for k, v in res.per_fold_best.items())
    lines += ["", f"- best θ per fold (stability): {stab}"]
    if res.theta is None:
        lines.append(
            f"- **no θ keeps ≥ {res.min_trades_per_day} trades/day → recommend `ml.enabled: false`.**"
        )
    elif res.recommend_enabled:
        lines.append(
            f"- selected **θ = {res.theta:.3f}** (beats the unfiltered strategy on return / drawdown)."
        )
    else:
        lines.append(
            f"- best θ = {res.theta:.3f} does **not** beat the unfiltered strategy → **recommend `ml.enabled: false`** "
            "(the ML filter adds no value on this data)."
        )
    return "\n".join([*lines, ""])
