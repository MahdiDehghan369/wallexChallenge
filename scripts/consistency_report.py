#!/usr/bin/env python
"""Backtest ↔ shadow ↔ live consistency report (T-1003) → reports/consistency.md + reports/shadow/REPORT.md.

python scripts/consistency_report.py --logs logs --live-data data/live --history data/processed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from siavash.config import load_config
from siavash.logging import setup_logging
from siavash.reporting.consistency import run_consistency


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default="logs", help="shadow / live logs directory")
    ap.add_argument("--live-data", default="data/live")
    ap.add_argument("--history", default="data/processed", help="downloaded history for indicator warm-up")
    ap.add_argument("--config", default="config")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging("WARNING", cfg.base.paths.logs, console=True)
    logs = Path(args.logs)
    if not (logs / "decisions.jsonl").exists():
        print(f"no decisions.jsonl in {logs}: run the shadow (make paper) first", file=sys.stderr)
        return 2
    path = asyncio.run(run_consistency(cfg, logs, Path(args.live_data), Path(args.history), Path(args.out)))
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
