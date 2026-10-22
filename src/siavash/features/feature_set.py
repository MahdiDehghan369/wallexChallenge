"""Feature-set definition (T-303).

Names follow section 8.3: ``"{tf}_{name}"`` for per-timeframe features and ``x_*`` for cross /
market features. Percent units everywhere (``ret_4 = 1.2`` means +1.2 %). Changing any
definition requires bumping :data:`FEATURE_SET_VERSION` (models check it on load).
"""

from __future__ import annotations

FEATURE_SET_VERSION = "fs_v1"

TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")

TF_FEATURE_NAMES: tuple[str, ...] = (
    "ema20",
    "ema50",
    "ema200",
    "ema20_slope",
    "ema50_dist",
    "ema200_dist",
    "adx14",
    "rsi14",
    "atr14_pct",
    "bb_width_pct",
    "bb_pos",
    "macd_hist_norm",
    "donchian_high_dist",
    "donchian_low_dist",
    "stoch_k",
    "vol_z20",
    "ret_1",
    "ret_4",
    "ret_16",
    "rv20",
)

# raw levels kept alongside features (needed by signals for prices; not model inputs)
TF_AUX_NAMES: tuple[str, ...] = ("close", "atr14", "bb_mid", "bb_lower", "bb_upper", "donchian_high")

CROSS_FEATURES: tuple[str, ...] = (
    "x_implied_z",
    "x_implied_dev_pct",
    "x_btc_ret_1h",
    "x_btc_ret_4h",
    "x_btc_adx_1h",
    "x_btc_rv_1h",
    "x_breadth_ema50",
    "x_avg_ret_4h",
    "x_rv_percentile_7d",
)

TF_FEATURES: list[str] = [f"{tf}_{name}" for tf in TIMEFRAMES for name in TF_FEATURE_NAMES]
AUX_COLUMNS: list[str] = [f"{tf}_{name}" for tf in TIMEFRAMES for name in TF_AUX_NAMES]
FEATURES: list[str] = [*TF_FEATURES, *CROSS_FEATURES]
