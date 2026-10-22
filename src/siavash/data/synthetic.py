"""Deterministic synthetic market data (T-008).

Generates BTC/USDT, ETH/USDT, USDT/TMN and the TMN pairs with:
- GBM-like returns with regime switches (trend up / trend down / range / high vol),
- ``X/TMN ≈ X/USDT × USDT/TMN`` with a ``lag_bars`` delay plus AR(1) noise and occasional
  dislocation shocks, so the implied-deviation signal (S3) is testable.

Fully deterministic for a given seed. Used by tests and by ``--synthetic`` script modes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from siavash.core.models import Candle
from siavash.core.types import Timeframe

SYN_START = datetime(2026, 1, 1, tzinfo=UTC)
OHLCV_COLUMNS = ["ts_open", "open", "high", "low", "close", "volume"]

# per-15m-bar parameters (scaled by sqrt(bar/15m) for other timeframes)
_REGIMES: dict[str, tuple[float, float]] = {
    "trend_up": (0.0006, 0.0030),
    "trend_down": (-0.0005, 0.0035),
    "range": (0.0, 0.0025),
    "high_vol": (0.0, 0.0080),
}
_REGIME_P = [0.3, 0.2, 0.35, 0.15]


def _regime_path(rng: np.random.Generator, n: int, bars_per_day: int) -> list[str]:
    names = list(_REGIMES)
    out: list[str] = []
    while len(out) < n:
        length = int(rng.integers(bars_per_day // 2, bars_per_day * 2 + 1))
        out.extend([names[int(rng.choice(len(names), p=_REGIME_P))]] * length)
    return out[:n]


def _log_path(rng: np.random.Generator, regimes: list[str], start_price: float, scale: float) -> np.ndarray:
    n = len(regimes)
    logp = np.empty(n)
    logp[0] = np.log(start_price)
    anchor = logp[0]
    prev = regimes[0]
    for i in range(1, n):
        reg = regimes[i]
        if reg != prev and reg == "range":
            anchor = logp[i - 1]
        prev = reg
        mu, sigma = _REGIMES[reg]
        shock = rng.normal(0.0, sigma * scale)
        if reg == "range":
            r = -0.05 * (logp[i - 1] - anchor) + shock
        else:
            r = mu * scale * scale + shock
        logp[i] = logp[i - 1] + r
    return logp


def _ohlcv(
    rng: np.random.Generator, ts: pd.DatetimeIndex, close: np.ndarray, sigma: np.ndarray, base_volume: float
) -> pd.DataFrame:
    n = len(close)
    open_ = np.empty(n)
    open_[0] = close[0] * float(np.exp(rng.normal(0, sigma[0])))
    open_[1:] = close[:-1]
    hi_ext = np.exp(np.abs(rng.normal(0, 1, n)) * sigma * 0.6)
    lo_ext = np.exp(-np.abs(rng.normal(0, 1, n)) * sigma * 0.6)
    high = np.maximum(open_, close) * hi_ext
    low = np.minimum(open_, close) * lo_ext
    ret = np.abs(np.log(close / open_))
    volume = base_volume * rng.lognormal(0.0, 0.4, n) * (1.0 + 40.0 * ret) * (sigma / sigma.mean())
    return pd.DataFrame(
        {"ts_open": ts, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def make_synthetic_market(
    seed: int = 42,
    days: int = 30,
    tf: str = "15m",
    start: datetime = SYN_START,
    lag_bars: int = 2,
    include_eth: bool = True,
) -> dict[str, pd.DataFrame]:
    """Return ``{symbol: DataFrame[ts_open, open, high, low, close, volume]}`` (UTC ``ts_open``).

    Symbols: ``BTC/USDT``, ``USDT/TMN``, ``BTC/TMN`` and, if ``include_eth``, ``ETH/USDT``, ``ETH/TMN``.
    """
    timeframe = Timeframe(tf)
    rng = np.random.default_rng(seed)
    bars_per_day = 86400 // timeframe.seconds
    n = days * bars_per_day
    scale = float(np.sqrt(timeframe.seconds / 900))
    ts = pd.date_range(start=start, periods=n, freq=f"{timeframe.seconds}s", tz=UTC)

    regimes = _regime_path(rng, n, bars_per_day)
    sigma_btc = np.array([_REGIMES[r][1] * scale for r in regimes])
    btc_usdt = np.exp(_log_path(rng, regimes, 60000.0, scale))

    # USDT/TMN: slow mean-reverting walk around 100k TMN
    u = np.empty(n)
    u[0] = np.log(100_000.0)
    for i in range(1, n):
        u[i] = u[i - 1] + 0.00002 * scale * scale - 0.01 * (u[i - 1] - u[0]) + rng.normal(0, 0.0008 * scale)
    usdt_tmn = np.exp(u)

    def tmn_from(usdt_pair: np.ndarray) -> np.ndarray:
        implied = usdt_pair * usdt_tmn
        lagged = np.concatenate([np.full(lag_bars, implied[0]), implied[:-lag_bars]]) if lag_bars else implied
        noise = np.zeros(n)
        dislocation = np.zeros(n)
        for i in range(1, n):
            noise[i] = 0.5 * noise[i - 1] + rng.normal(0, 0.0015)
            kick = -0.025 if rng.random() < 0.01 else 0.0
            dislocation[i] = 0.6 * dislocation[i - 1] + kick
        return lagged * np.exp(noise + dislocation)

    out: dict[str, pd.DataFrame] = {
        "BTC/USDT": _ohlcv(rng, ts, btc_usdt, sigma_btc, 50.0),
        "USDT/TMN": _ohlcv(rng, ts, usdt_tmn, np.full(n, 0.0008 * scale), 2_000_000.0),
        "BTC/TMN": _ohlcv(rng, ts, tmn_from(btc_usdt), sigma_btc, 5.0),
    }
    if include_eth:
        btc_r = np.diff(np.log(btc_usdt), prepend=np.log(btc_usdt[0]))
        eth_r = 1.1 * btc_r + rng.normal(0, 0.0025 * scale, n)
        eth_r[0] = 0.0
        eth_usdt = 3000.0 * np.exp(np.cumsum(eth_r))
        sigma_eth = sigma_btc * 1.2
        out["ETH/USDT"] = _ohlcv(rng, ts, eth_usdt, sigma_eth, 600.0)
        out["ETH/TMN"] = _ohlcv(rng, ts, tmn_from(eth_usdt), sigma_eth, 60.0)
    for df in out.values():
        df.attrs["regimes"] = regimes
    return out


def resample_ohlcv(df: pd.DataFrame, tf: str | Timeframe) -> pd.DataFrame:
    """Aggregate an OHLCV frame (``ts_open`` column) into a higher timeframe; only complete bars."""
    timeframe = Timeframe(tf)
    g = df.set_index("ts_open").resample(f"{timeframe.seconds}s", label="left", closed="left")
    agg = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = g["close"].count()
    if len(df) > 1:
        base_s = int((df["ts_open"].iloc[1] - df["ts_open"].iloc[0]).total_seconds())
        need = timeframe.seconds // base_s
        agg = agg[counts >= need]
    return agg.dropna().reset_index()


def to_candles(df: pd.DataFrame, symbol: str, tf: str | Timeframe) -> list[Candle]:
    """Convert an OHLCV frame to ``Candle`` models."""
    timeframe = Timeframe(tf)
    is_filled = df["is_filled"] if "is_filled" in df.columns else pd.Series(False, index=df.index)
    return [
        Candle(
            symbol=symbol,
            timeframe=timeframe,
            ts_open=row.ts_open.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            is_filled=bool(f),
        )
        for row, f in zip(df.itertuples(index=False), is_filled, strict=True)
    ]
