#!/usr/bin/env python
"""Walk-forward backtests, parameter sensitivity and version comparison (T-904).

Examples:
    python scripts/run_walkforward.py --synthetic --days 45 --quick
    python scripts/run_walkforward.py --processed data/processed --model lgbm_v1
    python scripts/run_walkforward.py --processed data/processed --fee-pct 0.2   # what-if fees (config untouched)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from siavash.backtest.market_data import frames_from_15m, processed_frames
from siavash.backtest.walkforward import run_walkforward, with_fees, write_walkforward_report
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market
from siavash.logging import setup_logging


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--synthetic", action="store_true")
    src.add_argument("--processed", default="data/processed")
    ap.add_argument("--days", type=int, default=45, help="synthetic: number of days")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--symbols", nargs="*", default=None, help="universe for the engine runs")
    ap.add_argument("--window-days", type=float, default=7)
    ap.add_argument("--warmup-days", type=float, default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--model", default=None, help="model version for the +ML variants (e.g. lgbm_v1)")
    ap.add_argument("--models-root", default=None)
    ap.add_argument("--fee-pct", type=float, default=None, help="what-if maker=taker fee in percent")
    ap.add_argument(
        "--reuse-runs",
        action="store_true",
        help="re-read engine metrics from existing <out>/runs/*/logs instead of re-running the engine",
    )
    ap.add_argument("--config", default="config")
    ap.add_argument("--out", default="reports/backtest/walkforward")
    args = ap.parse_args(argv)

    cfg = with_fees(load_config(args.config), args.fee_pct)
    setup_logging("WARNING", cfg.base.paths.logs, console=True)
    if args.synthetic:
        frames = frames_from_15m(make_synthetic_market(seed=args.seed, days=args.days))
        warmup = (
            args.warmup_days
            if args.warmup_days is not None
            else min(cfg.base.bt.warmup_days, args.days * 0.3)
        )
        universe = args.symbols or ["BTC/TMN", "ETH/TMN"]
    else:
        if not Path(args.processed).exists():
            print(
                f"no data in {args.processed}: run `make download-data validate-data` or use --synthetic",
                file=sys.stderr,
            )
            return 2
        frames = processed_frames(args.processed)
        warmup, universe = args.warmup_days, args.symbols
    rep = asyncio.run(
        run_walkforward(
            cfg,
            frames,
            Path(args.out),
            args.window_days,
            args.quick,
            universe,
            args.model,
            args.models_root,
            warmup,
            args.reuse_runs,
        )
    )
    if args.fee_pct is not None:
        rep.notes.append(f"what-if fees: maker = taker = {args.fee_pct} % (config files unchanged)")
    path = write_walkforward_report(rep, cfg, Path(args.out))
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
