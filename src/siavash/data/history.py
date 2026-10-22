"""Historical candle downloader (T-201).

Writes ``data/raw/{BASE}_{QUOTE}_{tf}.parquet`` (columns ``ts_open, open, high, low, close, volume``,
UTC). Resumes from the last stored candle. Parquet schema metadata carries ``source``
(``wallex`` or ``binance_aux``) and the symbol/timeframe. Optional auxiliary source: public Binance
kline CSV/ZIP files that a human drops in ``data/aux/`` (USDT pairs only), stored in ``data/raw/aux/``.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle
from siavash.core.symbols import split
from siavash.core.types import Timeframe
from siavash.logging import get_logger

log = get_logger(__name__)

COLUMNS = ["ts_open", "open", "high", "low", "close", "volume"]
SOURCE_WALLEX = "wallex"
SOURCE_BINANCE = "binance_aux"


def raw_path(out: str | Path, symbol: str, tf: Timeframe | str) -> Path:
    """``out/BASE_QUOTE_tf.parquet``."""
    base, quote = split(symbol)
    return Path(out) / f"{base}_{quote}_{Timeframe(tf).value}.parquet"


def write_parquet(df: pd.DataFrame, path: Path, source: str, symbol: str, tf: Timeframe | str) -> None:
    """Write OHLCV with schema metadata (source, symbol, timeframe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [c for c in df.columns if c in COLUMNS or c == "is_filled"]
    table = pa.Table.from_pandas(df[cols].reset_index(drop=True), preserve_index=False)
    meta = dict(table.schema.metadata or {})
    meta.update(
        {b"source": source.encode(), b"symbol": symbol.encode(), b"timeframe": Timeframe(tf).value.encode()}
    )
    pq.write_table(table.replace_schema_metadata(meta), path, compression="zstd")


def read_parquet(path: str | Path) -> pd.DataFrame:
    """Read an OHLCV parquet file (``ts_open`` as UTC timestamps)."""
    df = pd.read_parquet(path)
    df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
    return df


def parquet_source(path: str | Path) -> str | None:
    """``source`` value from schema metadata."""
    meta = pq.read_schema(path).metadata or {}
    v = meta.get(b"source")
    return v.decode() if v else None


def candles_to_frame(candles: Iterable[Candle]) -> pd.DataFrame:
    """``Candle`` models -> OHLCV frame."""
    rows = [
        {
            "ts_open": c.ts_open,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]
    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
    return df


@dataclass
class CoverageRow:
    symbol: str
    tf: str
    source: str
    start: datetime | None
    end: datetime | None
    candles: int
    gap_pct: float


def coverage(df: pd.DataFrame, symbol: str, tf: Timeframe | str, source: str) -> CoverageRow:
    """Range, count and percentage of missing bars on the regular time grid."""
    timeframe = Timeframe(tf)
    if df.empty:
        return CoverageRow(symbol, timeframe.value, source, None, None, 0, 100.0)
    start, end = df["ts_open"].min(), df["ts_open"].max()
    expected = int((end - start).total_seconds() // timeframe.seconds) + 1
    have = int(df["ts_open"].nunique())
    gap = round(100.0 * (1 - have / expected), 3)
    return CoverageRow(symbol, timeframe.value, source, start.to_pydatetime(), end.to_pydatetime(), have, gap)


def write_coverage_report(rows: Sequence[CoverageRow], path: str | Path) -> None:
    """``reports/data_coverage.md``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Data coverage",
        "",
        f"_generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "| symbol | tf | source | start (UTC) | end (UTC) | candles | gaps % |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        s = r.start.strftime("%Y-%m-%d %H:%M") if r.start else "—"
        e = r.end.strftime("%Y-%m-%d %H:%M") if r.end else "—"
        lines.append(f"| {r.symbol} | {r.tf} | {r.source} | {s} | {e} | {r.candles} | {r.gap_pct} |")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def download_symbol(
    md: MarketData, symbol: str, tf: Timeframe, days: int, out: str | Path, now: datetime
) -> pd.DataFrame:
    """Download (or resume) one file; returns the full stored frame. Only closed candles are stored."""
    path = raw_path(out, symbol, tf)
    end = datetime.fromtimestamp(int(now.timestamp()) // tf.seconds * tf.seconds, tz=UTC)  # align to grid
    start = end - timedelta(days=days)
    existing = read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
    if not existing.empty:
        start = max(start, existing["ts_open"].max().to_pydatetime() + timedelta(seconds=tf.seconds))
    last_closed_open = end - timedelta(seconds=tf.seconds)
    new = pd.DataFrame(columns=COLUMNS)
    if start <= last_closed_open:
        candles = await md.get_candles(symbol, tf, start, last_closed_open + timedelta(seconds=1))
        new = candles_to_frame(candles)
    frames = [f for f in (existing, new) if not f.empty]
    full = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    if not full.empty:
        full = full.drop_duplicates("ts_open", keep="last").sort_values("ts_open").reset_index(drop=True)
        write_parquet(full, path, SOURCE_WALLEX, symbol, tf)
    log.info("download_symbol", symbol=symbol, tf=tf.value, new=len(new), total=len(full))
    return full


async def download(
    md: MarketData,
    symbols: Sequence[str],
    tfs: Sequence[Timeframe] = (Timeframe.M15, Timeframe.H1),
    days: int = 120,
    out: str | Path = "data/raw",
    clock: Clock | None = None,
    report_path: str | Path | None = "reports/data_coverage.md",
) -> list[CoverageRow]:
    """Download every (symbol, tf); write the coverage report. Failures are logged and skipped."""
    now = clock.now() if clock else datetime.now(UTC)
    rows: list[CoverageRow] = []
    for sym in symbols:
        for tf in tfs:
            try:
                df = await download_symbol(md, sym, tf, days, out, now)
            except Exception as e:  # one bad market must not stop the whole download
                log.warning("download_failed", symbol=sym, tf=tf.value, error=str(e))
                df = pd.DataFrame(columns=COLUMNS)
            rows.append(coverage(df, sym, tf, SOURCE_WALLEX))
    if report_path:
        write_coverage_report(rows, report_path)
    return rows


# ---------------------------------------------------------------------------- auxiliary (Binance klines)

BINANCE_TF = {"15m": Timeframe.M15, "1h": Timeframe.H1, "4h": Timeframe.H4, "1m": Timeframe.M1}


def _read_binance_csv(data: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(data), header=None)
    if not str(df.iloc[0, 0]).replace(".", "", 1).isdigit():  # header row present
        df = df.iloc[1:]
    t = pd.to_numeric(df[0])
    unit = "us" if t.max() > 1e14 else "ms"
    return pd.DataFrame(
        {
            "ts_open": pd.to_datetime(t, unit=unit, utc=True),
            "open": pd.to_numeric(df[1]),
            "high": pd.to_numeric(df[2]),
            "low": pd.to_numeric(df[3]),
            "close": pd.to_numeric(df[4]),
            "volume": pd.to_numeric(df[5]),
        }
    )


def load_binance_aux(aux_dir: str | Path = "data/aux", out: str | Path = "data/raw/aux") -> list[CoverageRow]:
    """Convert public Binance kline files ``SYMBOL-TF-*.csv|zip`` (e.g. ``BTCUSDT-15m-2026-09.zip``)."""
    groups: dict[tuple[str, Timeframe], list[pd.DataFrame]] = {}
    for f in sorted(Path(aux_dir).glob("*")):
        if f.suffix not in (".csv", ".zip"):
            continue
        parts = f.stem.split("-")
        if len(parts) < 2 or parts[1] not in BINANCE_TF or not parts[0].endswith("USDT"):
            log.warning("aux_skip", file=f.name)
            continue
        symbol = f"{parts[0][:-4]}/USDT"
        tf = BINANCE_TF[parts[1]]
        if f.suffix == ".zip":
            with zipfile.ZipFile(f) as z:
                for name in z.namelist():
                    if name.endswith(".csv"):
                        groups.setdefault((symbol, tf), []).append(_read_binance_csv(z.read(name)))
        else:
            groups.setdefault((symbol, tf), []).append(_read_binance_csv(f.read_bytes()))
    rows = []
    for (symbol, tf), frames in groups.items():
        df = pd.concat(frames).drop_duplicates("ts_open").sort_values("ts_open").reset_index(drop=True)
        write_parquet(df, raw_path(out, symbol, tf), SOURCE_BINANCE, symbol, tf)
        rows.append(coverage(df, symbol, tf, SOURCE_BINANCE))
    return rows


def default_symbols(cfg: object) -> list[str]:
    """``universe.candidates`` plus ``USDT/TMN`` and the USDT pair of every TMN market."""
    cands: list[str] = list(cfg.strategy.universe.candidates)  # type: ignore[attr-defined]
    out = list(dict.fromkeys(cands))
    for s in cands:
        base, quote = split(s)
        if quote == "TMN" and base != "USDT" and f"{base}/USDT" not in out:
            out.append(f"{base}/USDT")
    if "USDT/TMN" not in out:
        out.append("USDT/TMN")
    return out
