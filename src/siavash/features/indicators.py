"""Vectorised technical indicators (T-301).

All functions are causal: the value at bar ``t`` depends only on bars ``<= t`` (``donchian``
excludes bar ``t`` itself). Inputs are ``pd.Series`` or ``pd.DataFrame`` with columns
``open, high, low, close, volume``. Wilder smoothing is seeded with the simple mean of the first
``n`` values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA with ``alpha = 2/(n+1)``, seeded with the first value; NaN for the first n-1 bars."""
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    """Simple moving average over ``n`` bars."""
    return s.rolling(n, min_periods=n).mean()


def wilder(x: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA): seed = mean of first ``n`` valid values, then ``r = r + (x - r)/n``."""
    values = x.to_numpy(dtype=float)
    out = pd.Series(np.nan, index=x.index, dtype=float)
    valid = np.flatnonzero(~np.isnan(values))
    if len(valid) < n:
        return out
    first = int(valid[0])
    seed_pos = first + n - 1
    if np.isnan(values[first : seed_pos + 1]).any():
        return out
    seed = float(values[first : seed_pos + 1].mean())
    y = pd.Series(values.copy(), index=x.index, dtype=float)
    y.iloc[:seed_pos] = np.nan
    y.iloc[seed_pos] = seed
    return y.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI in [0, 100]."""
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    return out.where(avg_gain.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    """True range; the first bar uses ``high - low``."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder average true range."""
    return wilder(true_range(df), n)


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Bollinger bands ``(mid, upper, lower, width_pct)`` with population std (ddof=0)."""
    mid = sma(s, n)
    std = s.rolling(n, min_periods=n).std(ddof=0)
    upper = mid + k * std
    lower = mid - k * std
    width_pct = (upper - lower) / mid * 100.0
    return mid, upper, lower, width_pct


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ADX."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    plus_dm.iloc[0] = np.nan
    minus_dm.iloc[0] = np.nan
    tr = true_range(df)
    tr.iloc[0] = np.nan
    atr_ = wilder(tr, n)
    plus_di = 100.0 * wilder(plus_dm, n) / atr_
    minus_di = 100.0 * wilder(minus_dm, n) / atr_
    denom = plus_di + minus_di
    dx = (100.0 * (plus_di - minus_di).abs() / denom).where(denom != 0, 0.0).where(denom.notna())
    return wilder(dx, n)


def macd(
    s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """``(macd, signal, hist)``."""
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def donchian(df: pd.DataFrame, n: int = 20) -> tuple[pd.Series, pd.Series]:
    """Donchian channel ``(high, low)`` of the previous ``n`` bars (excludes the current bar)."""
    high = df["high"].rolling(n, min_periods=n).max().shift(1)
    low = df["low"].rolling(n, min_periods=n).min().shift(1)
    return high, low


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic oscillator ``(%K, %D)``; %K raw, %D = SMA(%K, d)."""
    ll = df["low"].rolling(n, min_periods=n).min()
    hh = df["high"].rolling(n, min_periods=n).max()
    rng = hh - ll
    k = (100.0 * (df["close"] - ll) / rng).where(rng != 0, 50.0).where(rng.notna())
    return k, sma(k, d)


def obv(df: pd.DataFrame) -> pd.Series:
    """On-balance volume starting at 0."""
    direction = np.sign(df["close"].diff()).fillna(0.0)
    return (direction * df["volume"]).cumsum()


def zscore(s: pd.Series, n: int) -> pd.Series:
    """Rolling z-score with population std; 0 when std is 0."""
    mean = s.rolling(n, min_periods=n).mean()
    std = s.rolling(n, min_periods=n).std(ddof=0)
    return ((s - mean) / std).where(std != 0, 0.0).where(std.notna())


def realized_vol(close: pd.Series, n: int) -> pd.Series:
    """Rolling std (ddof=0) of log returns over ``n`` bars, as a fraction (not annualised)."""
    r = pd.Series(np.log(close.to_numpy(dtype=float)), index=close.index).diff()
    return r.rolling(n, min_periods=n).std(ddof=0)
