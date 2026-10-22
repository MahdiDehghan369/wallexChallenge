"""T-202: cleaning on deliberately corrupted data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from siavash.data.clean import clean_dir, clean_ohlcv, load_processed
from siavash.data.history import parquet_source, write_parquet

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _df(n: int = 40) -> pd.DataFrame:
    ts = [T0 + timedelta(minutes=15 * i) for i in range(n)]
    close = 100 + np.arange(n, dtype=float)
    return pd.DataFrame(
        {
            "ts_open": ts,
            "open": close - 0.5,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 10.0,
        }
    )


def _broken() -> pd.DataFrame:
    df = _df()
    df = pd.concat([df, df.iloc[[5]]])  # duplicate
    df.loc[df.index == 8, "high"] = 50.0  # invalid OHLC (high < close)
    df.loc[df.index == 9, "close"] = 0.0  # zero price
    df = df[~df.index.isin([12, 13])]  # 2-bar gap -> ffill
    df = df[~df.index.isin(range(20, 26))]  # 6-bar gap -> NaN
    df.loc[df.index == 30, ["open", "high", "low", "close"]] = [128.0, 170.0, 127.0, 160.0]  # +23 % jump
    off = df.iloc[[0]].copy()
    off["ts_open"] = T0 + timedelta(minutes=15 * 35, seconds=40)  # off-grid duplicate of bar 35
    return pd.concat([df, off]).sample(frac=1, random_state=0)  # shuffled


def test_clean_corrupted() -> None:
    out, rep = clean_ohlcv(_broken(), "15m", max_ffill_bars=3, outlier_jump_pct=20, name="X")
    assert rep.duplicates == 1
    assert rep.invalid_ohlc == 1 and rep.non_positive == 1
    assert rep.off_grid_dropped == 1
    assert out["ts_open"].is_monotonic_increasing and out["ts_open"].is_unique
    secs = out["ts_open"].astype("int64") // 10**9
    assert (secs % 900 == 0).all()
    assert len(out) == 40  # full grid
    # bars 8, 9 (dropped) and 12, 13 (missing) are short gaps -> forward filled
    filled = out[out["is_filled"]]
    assert sorted(filled.index.tolist()) == [8, 9, 12, 13]
    assert (filled["volume"] == 0).all()
    assert out.loc[12, "close"] == out.loc[11, "close"] == out.loc[12, "high"]
    # 6-bar gap stays NaN and is reported
    assert out.loc[20:25, "close"].isna().all()
    assert rep.gaps == [(T0 + timedelta(minutes=15 * 20), T0 + timedelta(minutes=15 * 25), 6)]
    assert out.loc[30, "is_outlier"] and rep.outliers >= 1


def test_clean_dir_and_report(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_parquet(
        _broken().rename_axis(None).reset_index(drop=True),
        raw / "BTC_TMN_15m.parquet",
        "wallex",
        "BTC/TMN",
        "15m",
    )
    reports = clean_dir(raw, tmp_path / "processed", tmp_path / "q.md")
    p = tmp_path / "processed" / "BTC_TMN_15m.parquet"
    df = load_processed(p)
    assert len(df) == 40 and df["is_filled"].sum() == 4 and df["is_outlier"].dtype == bool
    assert parquet_source(p) == "wallex"
    text = (tmp_path / "q.md").read_text(encoding="utf-8")
    assert "Unfilled gaps" in text and "BTC_TMN_15m.parquet" in text
    assert reports[0].nan_bars == 6


def test_clean_input_already_clean() -> None:
    out, rep = clean_ohlcv(_df(), "15m")
    assert rep.filled_bars == 0 and not rep.gaps and len(out) == 40 and not out["is_outlier"].any()
