#!/usr/bin/env python
"""Median top-of-book spread per market from repeated ``GET /depth/all`` snapshots (research v2, H4).

Public market data only (no auth, no orders). There is no historical order book, so this is a
snapshot measurement at one time of day; ``config/research_v2.yaml > universe`` sets how many
snapshots and how far apart.

    python scripts/measure_spreads.py --out reports/research_v2/spreads.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from siavash.config import load_config
from siavash.logging import setup_logging


def spread_pct(book: object) -> float | None:
    bid, ask = getattr(book, "best_bid", None), getattr(book, "best_ask", None)
    if bid is None or ask is None or bid <= 0 or ask <= bid:
        return None
    return float((ask - bid) / ((ask + bid) / 2) * 100)


def _candidates(rcfg: dict, holdout_dir: str) -> list[str]:
    """DEV markets plus every TMN market with downloaded hold-out data."""
    ho = [p.name.rsplit("_", 1)[0].replace("_", "/") for p in Path(holdout_dir).glob("*_TMN_15m.parquet")]
    return sorted(set(rcfg["dev"]) | set(ho))


async def _run(args: argparse.Namespace) -> int:
    from siavash.exchange.client import WallexHttp
    from siavash.exchange.market_data import WallexMarketData

    cfg = load_config(args.config)
    rcfg = yaml.safe_load(Path(args.research_config).read_text(encoding="utf-8"))["universe"]
    n, gap = int(rcfg["spread_snapshots"]), float(rcfg["spread_snapshot_interval_s"])
    setup_logging("WARNING", cfg.base.paths.logs)
    samples: dict[str, list[float]] = {}
    times = []
    async with WallexHttp.from_config(cfg) as http:
        md = WallexMarketData(
            http, ticker_cache_s=cfg.base.exchange.ticker_cache_s, min_notional=cfg.base.exchange.min_notional
        )
        for i in range(n):
            books = None
            for attempt in range(2):
                try:
                    books = await md.get_all_orderbooks()
                    break
                except Exception as e:
                    print(f"snapshot {i + 1}: attempt {attempt + 1} failed: {str(e)[:80]}", flush=True)
                    await asyncio.sleep(gap / 2)
            if books is None:  # /depth/all down → one GET /v1/depth per candidate market
                books = {}
                for sym in _candidates(rcfg, args.holdout_data):
                    try:
                        books[sym] = await md.get_orderbook(sym)
                    except Exception as e:
                        print(f"snapshot {i + 1}: {sym} failed: {str(e)[:60]}", flush=True)
            times.append(datetime.now(UTC).isoformat(timespec="seconds"))
            for sym, b in books.items():
                s = spread_pct(b)
                if s is not None and sym.endswith("/TMN"):
                    samples.setdefault(sym, []).append(s)
            print(f"snapshot {i + 1}/{n}: {len(books)} books", flush=True)
            if i + 1 < n:
                await asyncio.sleep(gap)
    out = {
        "source": "GET /depth/all, or GET /v1/depth per market when /depth/all fails (public); "
        "top-of-book spread = (ask - bid) / mid * 100",
        "snapshots_utc": times,
        "median_spread_pct": {s: round(statistics.median(v), 4) for s, v in sorted(samples.items())},
        "n_samples": {s: len(v) for s, v in sorted(samples.items())},
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {args.out} ({len(samples)} TMN markets)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config")
    ap.add_argument("--research-config", default="config/research_v2.yaml")
    ap.add_argument("--out", default="reports/research_v2/spreads.json")
    ap.add_argument("--holdout-data", default="data/processed_v2")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
