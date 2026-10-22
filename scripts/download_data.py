#!/usr/bin/env python
"""Download historical candles from Wallex into data/raw/ (T-201).

Public market-data endpoints only (no orders). Examples:
    python scripts/download_data.py --days 120 --tfs 15m 1h
    python scripts/download_data.py --aux-source     # also convert Binance kline files in data/aux/
    python scripts/download_data.py --from-snapshot  # no network: use data/snapshot (T-206)
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.data.history import default_symbols, download, load_binance_aux, write_coverage_report
from siavash.logging import setup_logging


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config", help="config directory")
    ap.add_argument(
        "--symbols", nargs="*", default=None, help="canonical symbols (default: universe candidates)"
    )
    ap.add_argument("--tfs", nargs="*", default=["15m", "1h"], choices=[t.value for t in Timeframe])
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--report", default="reports/data_coverage.md")
    ap.add_argument(
        "--aux-source", action="store_true", help="also convert Binance kline files from --aux-dir"
    )
    ap.add_argument("--aux-dir", default="data/aux")
    ap.add_argument(
        "--from-snapshot",
        action="store_true",
        help="no API access: copy data/snapshot/processed into data/processed (judges)",
    )
    return ap.parse_args(argv)


def from_snapshot(
    snapshot: Path = Path("data/snapshot/processed"), dst: Path = Path("data/processed")
) -> int:
    files = sorted(snapshot.glob("*.parquet"))
    if not files:
        print(f"no snapshot in {snapshot}", file=sys.stderr)
        return 2
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dst / f.name)
    print(f"copied {len(files)} snapshot files -> {dst} (no API access used)")
    return 0


async def _run(args: argparse.Namespace) -> int:
    if args.from_snapshot:
        return from_snapshot()
    from siavash.exchange.client import WallexHttp
    from siavash.exchange.market_data import WallexMarketData

    cfg = load_config(args.config)
    setup_logging("INFO", cfg.base.paths.logs)
    symbols = args.symbols or default_symbols(cfg)
    tfs = [Timeframe(t) for t in args.tfs]
    async with WallexHttp.from_config(cfg) as http:
        md = WallexMarketData(
            http, ticker_cache_s=cfg.base.exchange.ticker_cache_s, min_notional=cfg.base.exchange.min_notional
        )
        rows = await download(md, symbols, tfs, args.days, args.out, report_path=None)
    if args.aux_source:
        rows += load_binance_aux(args.aux_dir, f"{args.out}/aux")
    write_coverage_report(rows, args.report)
    print(f"downloaded {len(rows)} series; report: {args.report}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
