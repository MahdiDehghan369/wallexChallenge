#!/usr/bin/env python
"""Data snapshot for the judges (T-206): data/processed (+ recorded data/live) → data/snapshot/.

Writes zstd-compressed parquet files, ``MANIFEST.json`` (sha256, rows, range per file) and ``README.md``.
``make backtest ARGS="--snapshot"`` and ``scripts/download_data.py --from-snapshot`` use it without any
API access.

    python scripts/make_snapshot.py --dry-run
    python scripts/make_snapshot.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LFS_LIMIT_MB = 90


def sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def live_frames(live_dir: Path) -> dict[str, pd.DataFrame]:
    """``data/live/{day}/candles_{tf}.jsonl`` → ``{"BASE_QUOTE_tf": frame}``."""
    from siavash.reporting.consistency import load_live_frames

    out = {}
    for sym, by_tf in load_live_frames(live_dir).items():
        for tf, df in by_tf.items():
            out[f"{sym.replace('/', '_')}_{tf.value}"] = df
    return out


def plan(processed: Path, live: Path) -> list[tuple[str, pd.DataFrame]]:
    items = [(f"processed/{f.name}", pd.read_parquet(f)) for f in sorted(processed.glob("*.parquet"))]
    if live.exists():
        items += [(f"live/{name}.parquet", df) for name, df in sorted(live_frames(live).items())]
    return items


def readme(manifest: dict, total_mb: float) -> str:
    lfs = (
        f"\n**Size {total_mb:.1f} MB > {LFS_LIMIT_MB} MB:** track the parquet files with git-lfs "
        "(`git lfs track 'data/snapshot/**/*.parquet'`) or upload the folder and put the download link here.\n"
        if total_mb > LFS_LIMIT_MB
        else ""
    )
    return f"""# Data snapshot

Frozen copy of the data used for the reported backtests (T-206), so judges can reproduce results
without a Wallex API key.

- `processed/` — cleaned 15m / 1h candles (`data/processed/`, see `reports/data_quality.md`)
- `live/` — candles recorded during the challenge (`data/live/`), if any
- `MANIFEST.json` — sha256, rows and time range of every file (verify with `sha256sum`)

Created {manifest["created_at"]} from git `{manifest["git_sha"]}` · {len(manifest["files"])} files · {total_mb:.1f} MB.
{lfs}
Use it:

```bash
make backtest ARGS="--snapshot --llm off"
python scripts/download_data.py --from-snapshot
```
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--processed", default="data/processed")
    ap.add_argument("--live", default="data/live")
    ap.add_argument("--out", default="data/snapshot")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    processed, live, out = (ROOT / args.processed, ROOT / args.live, ROOT / args.out)
    if not processed.exists():
        print(f"{processed} missing: run `make download-data validate-data` first", file=sys.stderr)
        return 2
    items = plan(processed, live)
    if args.dry_run:
        for rel, df in items:
            print(f"[dry-run] {out.name}/{rel}  rows={len(df)}")
        print(f"[dry-run] {len(items)} files -> {out} (MANIFEST.json, README.md); nothing written")
        return 0
    from siavash.runtime import detect_git_sha

    files = []
    for rel, df in items:
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(dst, index=False, compression="zstd")
        ts = (
            pd.to_datetime(df["ts_open"], utc=True)
            if "ts_open" in df.columns
            else pd.Series(dtype="datetime64[ns, UTC]")
        )
        files.append(
            {
                "path": rel,
                "sha256": sha256(dst),
                "bytes": dst.stat().st_size,
                "rows": len(df),
                "start": str(ts.min()) if len(ts) else None,
                "end": str(ts.max()) if len(ts) else None,
            }
        )
    manifest = {"created_at": datetime.now(UTC).isoformat(), "git_sha": detect_git_sha(), "files": files}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    total_mb = sum(f["bytes"] for f in files) / 1e6
    (out / "README.md").write_text(readme(manifest, total_mb), encoding="utf-8")
    print(f"{len(files)} files, {total_mb:.1f} MB -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
