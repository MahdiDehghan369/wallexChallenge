"""Data validation and cleaning (T-202).

For each raw OHLCV file: drop duplicate timestamps, snap/drop off-grid timestamps, drop invalid
OHLC rows (high < max(open, close), low > min(open, close), non-positive prices), flag jumps larger
than ``outlier_jump_pct`` (``is_outlier``), then re-index on the regular grid: gaps of at most
``max_ffill_bars`` are forward-filled (flat candle at the previous close, ``volume=0``,
``is_filled=True``); longer gaps stay NaN and are listed in ``reports/data_quality.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from siavash.core.types import Timeframe

PRICE_COLS = ["open", "high", "low", "close"]


@dataclass
class QualityReport:
    name: str
    tf: str
    rows_in: int = 0
    rows_out: int = 0
    duplicates: int = 0
    off_grid_snapped: int = 0
    off_grid_dropped: int = 0
    invalid_ohlc: int = 0
    non_positive: int = 0
    outliers: int = 0
    filled_bars: int = 0
    gaps: list[tuple[datetime, datetime, int]] = field(
        default_factory=list
    )  # (first missing, last missing, bars)

    @property
    def nan_bars(self) -> int:
        return sum(g[2] for g in self.gaps)


def clean_ohlcv(
    df: pd.DataFrame,
    tf: Timeframe | str,
    max_ffill_bars: int = 3,
    outlier_jump_pct: float = 20.0,
    name: str = "",
) -> tuple[pd.DataFrame, QualityReport]:
    """Return ``(clean_df, report)``; output columns: ts_open, OHLCV, is_filled, is_outlier."""
    timeframe = Timeframe(tf)
    step = timeframe.seconds
    rep = QualityReport(name=name, tf=timeframe.value, rows_in=len(df))
    d = df.copy()
    d["ts_open"] = pd.to_datetime(d["ts_open"], utc=True)
    d = d.sort_values("ts_open", kind="stable")

    before = len(d)
    d = d.drop_duplicates("ts_open", keep="last")
    rep.duplicates = before - len(d)

    secs = d["ts_open"].astype("int64") // 10**9
    off = secs % step != 0
    if off.any():
        snapped = pd.to_datetime((secs // step) * step, unit="s", utc=True)
        on_grid_existing = set(d.loc[~off, "ts_open"])
        keep = ~off | ~snapped.isin(on_grid_existing)
        d.loc[off, "ts_open"] = snapped[off]
        dupe_after = d.duplicated("ts_open", keep="first") & off
        keep &= ~dupe_after
        rep.off_grid_dropped = int((~keep).sum())
        rep.off_grid_snapped = int(off.sum()) - rep.off_grid_dropped
        d = d[keep]

    nonpos = (d[PRICE_COLS] <= 0).any(axis=1) | d[PRICE_COLS].isna().any(axis=1)
    rep.non_positive = int(nonpos.sum())
    d = d[~nonpos]
    bad = (d["high"] < d[["open", "close"]].max(axis=1)) | (d["low"] > d[["open", "close"]].min(axis=1))
    bad |= d["high"] < d["low"]
    rep.invalid_ohlc = int(bad.sum())
    d = d[~bad]

    if d.empty:
        out = pd.DataFrame(columns=["ts_open", *PRICE_COLS, "volume", "is_filled", "is_outlier"])
        return out, rep

    d = d.set_index("ts_open")
    grid = pd.date_range(d.index.min(), d.index.max(), freq=f"{step}s", tz=UTC)
    d = d.reindex(grid)
    d.index.name = "ts_open"
    missing = d["close"].isna()
    # run-length of consecutive missing bars
    run_id = (missing != missing.shift()).cumsum()
    run_len = missing.groupby(run_id).transform("sum")
    fillable = missing & (run_len <= max_ffill_bars)
    prev_close = d["close"].ffill()
    for col in PRICE_COLS:
        d.loc[fillable, col] = prev_close[fillable]
    d.loc[fillable, "volume"] = 0.0
    d["is_filled"] = fillable
    rep.filled_bars = int(fillable.sum())

    big = missing & ~fillable
    if big.any():
        for _, grp in d[big].groupby(run_id[big]):
            rep.gaps.append((grp.index[0].to_pydatetime(), grp.index[-1].to_pydatetime(), len(grp)))

    jump = (d["close"] / d["close"].shift(1) - 1.0).abs() * 100.0
    d["is_outlier"] = (jump > outlier_jump_pct).fillna(False)
    rep.outliers = int(d["is_outlier"].sum())
    out = d.reset_index()
    out["volume"] = out["volume"].astype(float)
    out["is_filled"] = out["is_filled"].astype(bool)
    out["is_outlier"] = out["is_outlier"].astype(bool)
    rep.rows_out = len(out)
    return out, rep


def tf_from_name(path: Path) -> Timeframe:
    """``BTC_TMN_15m.parquet`` -> ``Timeframe.M15``."""
    return Timeframe(path.stem.rsplit("_", 1)[-1])


def write_quality_report(reports: Sequence[QualityReport], path: str | Path) -> None:
    """``reports/data_quality.md``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Data quality",
        "",
        f"_generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "| file | tf | rows in | rows out | dup | off-grid snapped/dropped | invalid OHLC | ≤0 price | outliers | ffilled | NaN bars |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        lines.append(
            f"| {r.name} | {r.tf} | {r.rows_in} | {r.rows_out} | {r.duplicates} | {r.off_grid_snapped}/{r.off_grid_dropped} "
            f"| {r.invalid_ohlc} | {r.non_positive} | {r.outliers} | {r.filled_bars} | {r.nan_bars} |"
        )
    gaps = [(r.name, g) for r in reports for g in r.gaps]
    lines += ["", "## Unfilled gaps (left as NaN)", ""]
    if not gaps:
        lines.append("None.")
    else:
        lines += ["| file | first missing (UTC) | last missing (UTC) | bars |", "|---|---|---|---|"]
        for name, (a, b, n) in gaps:
            lines.append(f"| {name} | {a:%Y-%m-%d %H:%M} | {b:%Y-%m-%d %H:%M} | {n} |")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_dir(
    raw_dir: str | Path = "data/raw",
    out_dir: str | Path = "data/processed",
    report_path: str | Path | None = "reports/data_quality.md",
    max_ffill_bars: int = 3,
    outlier_jump_pct: float = 20.0,
) -> list[QualityReport]:
    """Clean every ``*.parquet`` under ``raw_dir`` (recursively, keeping sub-folders) into ``out_dir``."""
    from siavash.data.history import parquet_source, read_parquet, write_parquet

    raw = Path(raw_dir)
    reports = []
    for f in sorted(raw.rglob("*.parquet")):
        tf = tf_from_name(f)
        rel = f.relative_to(raw)
        df, rep = clean_ohlcv(read_parquet(f), tf, max_ffill_bars, outlier_jump_pct, name=str(rel))
        meta_source = parquet_source(f) or "unknown"
        base, quote, _ = f.stem.split("_", 2) if f.stem.count("_") >= 2 else (f.stem, "", "")
        out = Path(out_dir) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        write_parquet(df, out, meta_source, f"{base}/{quote}", tf)
        reports.append(rep)
    if report_path:
        write_quality_report(reports, report_path)
    return reports


def load_processed(path: str | Path) -> pd.DataFrame:
    """Read a processed file (``ts_open`` UTC; flags as bool)."""
    df = pd.read_parquet(path)
    df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
    for c in ("is_filled", "is_outlier"):
        if c in df.columns:
            df[c] = df[c].fillna(False).astype(bool)
    return df


def drop_nan_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with complete prices only (for indicator computation)."""
    return df[np.isfinite(df["close"].to_numpy(dtype=float))].reset_index(drop=True)
