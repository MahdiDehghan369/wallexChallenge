"""Implied TMN price and deviation (T-205).

``implied_TMN(X) = X/USDT × USDT/TMN``; ``dev_pct = (X/TMN / implied − 1) × 100``. A strongly
negative rolling z-score of ``dev_pct`` means the TMN market lags the USDT market (signal S3).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta

import numpy as np
import pandas as pd

from siavash.core.models import MarketInfo, OrderBook
from siavash.core.types import Timeframe


def implied_deviation(
    tmn: pd.Series, usdt: pd.Series, usdt_tmn: pd.Series, window_bars: int = 96
) -> pd.DataFrame:
    """Columns ``implied, dev_pct, x_implied_z, x_implied_dev_pct`` on the inner-joined timestamps.

    Missing/NaN prices give NaN rows; the z-score uses only the trailing ``window_bars`` rows.
    """
    joined = pd.concat({"tmn": tmn, "usdt": usdt, "usdt_tmn": usdt_tmn}, axis=1, join="inner").sort_index()
    implied = joined["usdt"] * joined["usdt_tmn"]
    dev_pct = (joined["tmn"] / implied - 1.0) * 100.0
    mean = dev_pct.rolling(window_bars, min_periods=window_bars).mean()
    std = dev_pct.rolling(window_bars, min_periods=window_bars).std(ddof=0)
    z = ((dev_pct - mean) / std).where(std > 0)
    out = pd.DataFrame(
        {
            "implied": implied,
            "dev_pct": dev_pct,
            "x_implied_z": z,
            "x_implied_dev_pct": dev_pct,
            "x_implied_dev_mean_pct": mean,
        },
        index=joined.index,
    )
    return out.replace([np.inf, -np.inf], np.nan)


def close_series(df: pd.DataFrame, tf: Timeframe | str = Timeframe.M15) -> pd.Series:
    """OHLCV frame (``ts_open`` column) -> close prices indexed by candle close time."""
    timeframe = Timeframe(tf)
    idx = pd.DatetimeIndex(df["ts_open"] + timedelta(seconds=timeframe.seconds), name="ts")
    return pd.Series(df["close"].to_numpy(dtype=float), index=idx)


def implied_features(
    tmn_df: pd.DataFrame,
    usdt_df: pd.DataFrame,
    usdt_tmn_df: pd.DataFrame,
    window_bars: int = 96,
    tf: Timeframe | str = Timeframe.M15,
) -> pd.DataFrame:
    """``x_implied_z`` / ``x_implied_dev_pct`` (+ aux ``x_implied_dev_mean_pct``) for one base asset."""
    dev = implied_deviation(
        close_series(tmn_df, tf), close_series(usdt_df, tf), close_series(usdt_tmn_df, tf), window_bars
    )
    return dev[["x_implied_z", "x_implied_dev_pct", "x_implied_dev_mean_pct"]]


def implied_pairs(
    markets: Iterable[MarketInfo], allowed: Callable[[str], bool] | None = None
) -> list[tuple[str, str]]:
    """``[(X/TMN, X/USDT)]`` for bases with both pairs active (and allowed, if a predicate is given)."""
    active = {m.symbol for m in markets if m.is_active and m.is_spot}
    if "USDT/TMN" not in active:
        return []
    out = []
    for sym in sorted(active):
        base, _, quote = sym.partition("/")
        if quote != "TMN" or base == "USDT":
            continue
        usdt_sym = f"{base}/USDT"
        if usdt_sym not in active:
            continue
        if allowed is not None and not (allowed(sym) and allowed(usdt_sym)):
            continue
        out.append((sym, usdt_sym))
    return out


def implied_from_books(
    book_tmn: OrderBook, book_usdt: OrderBook, book_usdt_tmn: OrderBook
) -> dict[str, float] | None:
    """Live snapshot from book mids: ``{"implied", "tmn_mid", "dev_pct"}``; ``None`` if a book is empty."""
    mids = [book_tmn.mid, book_usdt.mid, book_usdt_tmn.mid]
    if any(m is None or m <= 0 for m in mids):
        return None
    tmn_mid, usdt_mid, usdt_tmn_mid = (float(m) for m in mids)  # type: ignore[arg-type]
    implied = usdt_mid * usdt_tmn_mid
    return {"implied": implied, "tmn_mid": tmn_mid, "dev_pct": (tmn_mid / implied - 1.0) * 100.0}
