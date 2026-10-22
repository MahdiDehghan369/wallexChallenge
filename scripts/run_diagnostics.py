#!/usr/bin/env python
"""Edge diagnostics on the real dataset (research only; live config untouched).

Writes to reports/diagnostics/: edge_breakdown.md, fee_sensitivity.md, zero_fee_markets.md,
variants.md (dev period only) and — with ``--holdout``, once — holdout.md.

Examples:
    python scripts/run_diagnostics.py                     # build candidates + all dev reports
    python scripts/run_diagnostics.py --steps edge fees   # reuse data/diagnostics/candidates.parquet
    python scripts/run_diagnostics.py --holdout           # final hold-out (refuses a second run)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from siavash.backtest.market_data import processed_frames
from siavash.config import load_config
from siavash.exchange.guard import load_denylist
from siavash.research.diagnostics import (
    build_candidates,
    buy_and_hold,
    drift_per_bar,
    edge_breakdown,
    fee_sweep,
    holdout_report,
    make_split,
    variants_report,
    windows,
    zero_fee_report,
)
from siavash.research.profiles import expand, load_profiles, load_settings

STEPS = ("build", "edge", "fees", "zero-fee", "variants")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--processed", default="data/processed")
    ap.add_argument("--config", default="config")
    ap.add_argument("--profiles", default="config/profiles")
    ap.add_argument("--markets", default="docs/vendor/wallex/samples/hector_markets_flags.json")
    ap.add_argument("--cache", default="data/diagnostics/candidates.parquet")
    ap.add_argument("--out", default="reports/diagnostics")
    ap.add_argument("--steps", nargs="*", default=list(STEPS), choices=STEPS)
    ap.add_argument("--holdout", action="store_true", help="evaluate the final hold-out (only once)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    settings = load_settings(args.profiles)
    profiles = load_profiles(args.profiles)
    variants = [v for p in profiles for v in expand(p)]
    denylist = load_denylist(Path(args.config) / "denylist.txt")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    regime_filter = {p.name: p.regime_filter for p in profiles}

    if "zero-fee" in args.steps and not args.holdout:
        (out / "zero_fee_markets.md").write_text(
            zero_fee_report(args.markets, denylist, list(cfg.risk.allowed_quotes)), encoding="utf-8"
        )
        print(f"-> {out / 'zero_fee_markets.md'}")

    need_data = args.holdout or any(s in args.steps for s in ("build", "edge", "fees", "variants"))
    if not need_data:
        return 0
    if not Path(args.processed).exists():
        print(f"no data in {args.processed}: run `make download-data validate-data`", file=sys.stderr)
        return 2
    frames = processed_frames(args.processed)
    split = make_split(frames, settings.holdout_days)
    cache = Path(args.cache)
    if "build" in args.steps or not cache.exists():
        cand = build_candidates(frames, cfg, variants, split, denylist)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cand.to_parquet(cache, index=False)
        print(f"{len(cand)} setups -> {cache}")
    cand = pd.read_parquet(cache)
    print(cand.groupby(["variant", "period"]).size().unstack(fill_value=0).to_string())
    traded = sorted(cand["symbol"].unique())
    dev_edges = windows(split, cand.loc[cand["period"] == "dev", "ts"].min(), settings.window_days)
    drift = drift_per_bar(frames, traded, split.data_start, split.holdout_start)

    if args.holdout:
        done = out / "holdout_evaluated.json"
        if done.exists():
            print(f"hold-out already evaluated ({done}); it is evaluated once by design", file=sys.stderr)
            return 3
        (out / "holdout.md").write_text(
            holdout_report(
                cand,
                cfg,
                settings,
                split,
                profiles,
                variants,
                buy_and_hold(frames, traded, split.holdout_start, split.data_end),
            ),
            encoding="utf-8",
        )
        done.write_text(
            json.dumps(
                {
                    "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "holdout_start": str(split.holdout_start),
                    "data_end": str(split.data_end),
                    "n_variants": len(variants),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"-> {out / 'holdout.md'}")
        return 0

    if "edge" in args.steps:
        (out / "edge_breakdown.md").write_text(edge_breakdown(cand, settings, split, drift), encoding="utf-8")
        print(f"-> {out / 'edge_breakdown.md'}")
    if "fees" in args.steps:
        text, rows = fee_sweep(cand, cfg, settings, regime_filter)
        (out / "fee_sensitivity.md").write_text(text, encoding="utf-8")
        (out / "fee_sensitivity.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"-> {out / 'fee_sensitivity.md'}")
    if "variants" in args.steps:
        bench = buy_and_hold(frames, traded, dev_edges[1], dev_edges[-1])
        text, summary = variants_report(cand, cfg, settings, split, profiles, variants, bench, drift)
        (out / "variants.md").write_text(text, encoding="utf-8")
        (out / "variants.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"-> {out / 'variants.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
