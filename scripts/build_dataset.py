#!/usr/bin/env python
"""Build the labelled ML dataset (T-402).

Examples:
    python scripts/build_dataset.py --synthetic --out /tmp/ds.parquet
    python scripts/build_dataset.py --processed data/processed          # -> data/ml/dataset_fs_v1.parquet
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from siavash.backtest.market_data import frames_from_15m, processed_frames
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market
from siavash.ml.dataset import build_dataset, dataset_path, dataset_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--synthetic", action="store_true")
    src.add_argument("--processed", default="data/processed")
    ap.add_argument("--days", type=int, default=45, help="synthetic: number of days")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--config", default="config")
    ap.add_argument("--out", default=None, help=f"default {dataset_path()}")
    ap.add_argument("--report", default="reports/ml_dataset.md")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.synthetic:
        frames = frames_from_15m(make_synthetic_market(seed=args.seed, days=args.days))
    else:
        if not Path(args.processed).exists():
            print(
                f"no data in {args.processed}: run `make download-data validate-data` or use --synthetic",
                file=sys.stderr,
            )
            return 2
        frames = processed_frames(args.processed)
    ds = build_dataset(frames, cfg, args.symbols)
    out = Path(args.out) if args.out else dataset_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_parquet(out, index=False)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(dataset_report(ds), encoding="utf-8")
    print(f"{len(ds)} samples -> {out} (report {report})")
    if not ds.empty:
        print(ds.groupby("strategy")["label"].agg(["size", "mean"]).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
