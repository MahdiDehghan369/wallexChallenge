#!/usr/bin/env python
"""Walk-forward training + threshold selection + model registry (T-403 / T-404 / T-405).

Examples:
    python scripts/train_model.py --synthetic --days 60          # self-contained demo
    python scripts/train_model.py                               # data/ml/dataset_fs_v1.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import roc_auc_score

from siavash.backtest.market_data import frames_from_15m
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market
from siavash.features.feature_set import FEATURE_SET_VERSION
from siavash.ml.dataset import build_dataset, dataset_path
from siavash.ml.registry import frame_hash, save_model
from siavash.ml.threshold import select_threshold, threshold_report
from siavash.ml.train import walk_forward, walkforward_report


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=str(dataset_path()))
    ap.add_argument(
        "--synthetic", action="store_true", help="build a synthetic dataset instead of reading one"
    )
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--config", default="config")
    ap.add_argument("--report", default="reports/ml_walkforward.md")
    ap.add_argument("--oof", default=f"data/ml/oof_{FEATURE_SET_VERSION}.parquet")
    ap.add_argument("--models", default="models")
    ap.add_argument("--no-save", dest="save", action="store_false", help="do not write a model version")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    if args.synthetic:
        ds = build_dataset(frames_from_15m(make_synthetic_market(seed=args.seed, days=args.days)), cfg)
    else:
        if not Path(args.dataset).exists():
            print(
                f"missing {args.dataset}: run scripts/build_dataset.py first (or --synthetic)",
                file=sys.stderr,
            )
            return 2
        ds = pd.read_parquet(args.dataset)
    res = walk_forward(ds, cfg)
    th = select_threshold(res.oof, cfg.strategy.ml.min_trades_per_day)
    for p in (args.report, args.oof):
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    res.oof.to_parquet(args.oof, index=False)
    report = walkforward_report(res, cfg.strategy.ml.threshold) + "\n" + threshold_report(th)
    Path(args.report).write_text(report, encoding="utf-8")
    print(f"{len(res.fold_metrics)} folds, {len(res.oof)} OOF samples -> {args.report}, {args.oof}")
    verdict = "recommend ml.enabled=true" if th.recommend_enabled else "recommend ml.enabled=false"
    print(f"threshold: θ={th.theta} ({verdict})")
    if args.save and res.model is not None:
        o = res.oof
        pooled_auc = float(roc_auc_score(o["label"], o["p_raw"])) if o["label"].nunique() == 2 else None
        version = save_model(
            res.model,
            res.features,
            th.calibrator,
            {
                "data_source": "synthetic" if args.synthetic else args.dataset,
                "data_range": {"start": ds["ts"].min(), "end": ds["ts"].max()},
                "data_hash": frame_hash(ds),
                "n_samples": len(ds),
                "metrics": {"oof_auc": pooled_auc, "folds": len(res.fold_metrics), "oof_samples": len(o)},
                "threshold": th.theta,
                "recommend_enabled": th.recommend_enabled,
                "seed": res.params.get("seed"),
                "lgbm_params": res.params,
            },
            root=args.models,
        )
        print(f"saved model {args.models}/{version} (set strategy.yaml ml.model_version to use it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
