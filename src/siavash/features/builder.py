"""Multi-timeframe feature builder (T-303).

The output index is the **close time** of each 15m candle (``ts_open + 15m``, UTC). Features of
higher timeframes are aligned with ``merge_asof`` on their own close time (backward), so at any
15m close only fully closed 1h/4h candles are visible (no lookahead).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from siavash.core.types import Timeframe
from siavash.features import indicators as ind
from siavash.features.feature_set import TF_AUX_NAMES, TF_FEATURE_NAMES, TIMEFRAMES


@dataclass(frozen=True)
class FeatureParams:
    """Indicator parameters that come from config (periods in feature names are fixed)."""

    donchian_n: int = 20
    bb_n: int = 20
    bb_k: float = 2.0

    @classmethod
    def from_config(cls, cfg: Any) -> FeatureParams:
        """Build from ``AppConfig`` (``strategy.s1.donchian_n``, ``strategy.s2.bb_n/bb_k``)."""
        return cls(
            donchian_n=cfg.strategy.s1.donchian_n, bb_n=cfg.strategy.s2.bb_n, bb_k=cfg.strategy.s2.bb_k
        )


def _ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.reset_index() if "ts_open" not in df.columns else df
    out = out.sort_values("ts_open").drop_duplicates("ts_open", keep="last").reset_index(drop=True)
    return out


def tf_features(df: pd.DataFrame, tf: Timeframe | str, params: FeatureParams | None = None) -> pd.DataFrame:
    """Features for one timeframe, indexed by candle close time, columns ``{tf}_{name}``."""
    timeframe = Timeframe(tf)
    p = params or FeatureParams()
    d = _ohlcv_frame(df)
    close = d["close"].astype(float)
    e20, e50, e200 = ind.ema(close, 20), ind.ema(close, 50), ind.ema(close, 200)
    atr14 = ind.atr(d, 14)
    mid, upper, lower, width = ind.bollinger(close, p.bb_n, p.bb_k)
    _, _, hist = ind.macd(close)
    dh, dl = ind.donchian(d, p.donchian_n)
    k, _ = ind.stochastic(d, 14, 3)
    band = upper - lower
    cols: dict[str, pd.Series] = {
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "ema20_slope": (e20 / e20.shift(1) - 1.0) * 100.0,
        "ema50_dist": (close / e50 - 1.0) * 100.0,
        "ema200_dist": (close / e200 - 1.0) * 100.0,
        "adx14": ind.adx(d, 14),
        "rsi14": ind.rsi(close, 14),
        "atr14_pct": atr14 / close * 100.0,
        "bb_width_pct": width,
        "bb_pos": ((close - lower) / band).where(band != 0, 0.5),
        "macd_hist_norm": hist / close * 100.0,
        "donchian_high_dist": (close / dh - 1.0) * 100.0,
        "donchian_low_dist": (close / dl - 1.0) * 100.0,
        "stoch_k": k,
        "vol_z20": ind.zscore(d["volume"].astype(float), 20),
        "ret_1": close.pct_change(1, fill_method=None) * 100.0,
        "ret_4": close.pct_change(4, fill_method=None) * 100.0,
        "ret_16": close.pct_change(16, fill_method=None) * 100.0,
        "rv20": ind.realized_vol(close, 20) * 100.0,
        "close": close,
        "atr14": atr14,
        "bb_mid": mid,
        "bb_lower": lower,
        "bb_upper": upper,
        "donchian_high": dh,
    }
    names = [*TF_FEATURE_NAMES, *TF_AUX_NAMES]
    out = pd.DataFrame({f"{timeframe.value}_{n}": cols[n] for n in names})
    out.index = pd.DatetimeIndex(d["ts_open"] + timedelta(seconds=timeframe.seconds), name="ts")
    return out.replace([np.inf, -np.inf], np.nan)


def build_features(
    candles: Mapping[Timeframe | str, pd.DataFrame], params: FeatureParams | None = None
) -> pd.DataFrame:
    """Join 15m/1h/4h features on the 15m close time (higher TFs: last *closed* candle)."""
    frames = {Timeframe(k): v for k, v in candles.items()}
    if Timeframe.M15 not in frames:
        raise ValueError("15m candles are required")
    base = tf_features(frames[Timeframe.M15], Timeframe.M15, params)
    out = base.reset_index()
    for tf_name in TIMEFRAMES[1:]:
        tf = Timeframe(tf_name)
        cols = [f"{tf.value}_{n}" for n in (*TF_FEATURE_NAMES, *TF_AUX_NAMES)]
        if tf in frames and len(frames[tf]):
            higher = tf_features(frames[tf], tf, params).reset_index()
            out = pd.merge_asof(
                out.sort_values("ts"), higher.sort_values("ts"), on="ts", direction="backward"
            )
        else:
            for c in cols:
                out[c] = np.nan
    return out.set_index("ts").sort_index()


def latest_row(
    candles: Mapping[Timeframe | str, pd.DataFrame], params: FeatureParams | None = None
) -> dict[str, float]:
    """Live mode: same code path as :func:`build_features`, last row only."""
    df = build_features(candles, params)
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {k: float(v) for k, v in row.items()}


def add_cross_features(df: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """Attach ``x_*`` columns (indexed by time) using the latest value at or before each row."""
    if extra is None or extra.empty:
        return df
    left = df.reset_index()
    right = extra.copy()
    right.index = pd.DatetimeIndex(right.index, name="ts")
    right = right.reset_index().sort_values("ts")
    dup = [c for c in right.columns if c != "ts" and c in left.columns]
    left = left.drop(columns=dup)
    merged = pd.merge_asof(left.sort_values("ts"), right, on="ts", direction="backward")
    return merged.set_index("ts")
