#!/usr/bin/env python
"""Run a backtest with the live engine (T-901 / T-905).

Examples:
    python scripts/run_backtest.py --synthetic --days 30 --llm off
    python scripts/run_backtest.py --processed data/processed --from 2026-08-01 --to 2026-10-20 --llm off
    python scripts/run_backtest.py --snapshot            # data/snapshot (no API key needed)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from siavash.backtest.engine import processed_md_factory, run_backtest, synthetic_md_factory
from siavash.config import load_config
from siavash.logging import setup_logging


def _date(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s).replace(tzinfo=UTC) if s else None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--synthetic", action="store_true", help="deterministic synthetic market")
    src.add_argument("--processed", default=None, help="directory with cleaned parquet files")
    src.add_argument("--snapshot", action="store_true", help="use data/snapshot/processed")
    ap.add_argument("--days", type=int, default=30, help="synthetic: number of days")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--warmup-days", type=float, default=None)
    ap.add_argument("--llm", choices=["off", "cached", "live_sampled"], default="off")
    ap.add_argument("--sample-rate", type=float, default=0.2)
    ap.add_argument("--symbols", nargs="*", default=None, help="restrict the universe")
    ap.add_argument("--config", default="config")
    ap.add_argument("--out", default="reports/backtest")
    ap.add_argument("--no-report", action="store_true", help="skip REPORT.md / charts")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config(args.config)
    setup_logging("WARNING", cfg.base.paths.logs, console=True)
    if args.synthetic:
        factory, source = (
            synthetic_md_factory(cfg, args.days, args.seed),
            f"synthetic(seed={args.seed},days={args.days})",
        )
        warmup = (
            args.warmup_days
            if args.warmup_days is not None
            else min(cfg.base.bt.warmup_days, max(args.days * 0.2, 3))
        )
        universe = args.symbols or ["BTC/TMN", "ETH/TMN"]
    else:
        root = Path("data/snapshot/processed") if args.snapshot else Path(args.processed or "data/processed")
        if not root.exists():
            print(
                f"no data in {root}: run `make download-data validate-data` (or use --synthetic)",
                file=sys.stderr,
            )
            return 2
        factory, source, warmup, universe = (
            processed_md_factory(cfg, root),
            str(root),
            args.warmup_days,
            args.symbols,
        )
    res = run_backtest(
        cfg,
        _date(args.date_from),
        _date(args.date_to),
        args.llm,
        md_factory=factory,
        sample_rate=args.sample_rate,
        out_root=args.out,
        config_dir=args.config,
        universe=universe,
        data_source=source,
        warmup_days=warmup,
    )
    if not args.no_report:
        from siavash.backtest.report import write_report

        print(f"report: {write_report(res)}", file=sys.stderr)
    m = res.metrics
    keys = [
        "net_return_pct",
        "pnl_tmn",
        "max_drawdown_pct",
        "n_trades",
        "win_rate",
        "profit_factor",
        "trades_per_day",
        "total_fees_tmn",
    ]
    print(
        json.dumps(
            {
                "run_id": res.run_id,
                "out": str(res.out_dir),
                "universe": res.universe,
                **{k: m.get(k) for k in keys},
                "pipeline": m["pipeline"],
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
