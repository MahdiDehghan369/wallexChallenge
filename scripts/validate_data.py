#!/usr/bin/env python
"""Validate and clean data/raw/*.parquet into data/processed/ and write reports/data_quality.md (T-202)."""

from __future__ import annotations

import argparse
import sys

from siavash.config import load_config
from siavash.data.clean import clean_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/processed")
    ap.add_argument("--report", default="reports/data_quality.md")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    reports = clean_dir(
        args.raw, args.out, args.report, cfg.base.data.max_ffill_bars, cfg.base.data.outlier_jump_pct
    )
    gaps = sum(len(r.gaps) for r in reports)
    print(f"cleaned {len(reports)} files -> {args.out}; unfilled gaps: {gaps}; report: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
