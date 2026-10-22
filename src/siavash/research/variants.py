"""Setup generators for the diagnostic profiles.

``rules``: the live ``SignalGenerator``s (S1/S2/S3) built from the profile's in-memory config.
``h1_breakout`` / ``trend_follow``: research-only long setups evaluated on closed 1h candles (the
15m row at a full hour carries the just-closed 1h and the last closed 4h candle). Every threshold
comes from the profile ``params``; a missing parameter is an error, not a default.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pandas as pd

from siavash.core.models import RegimeAssessment
from siavash.research.profiles import Variant, variant_config
from siavash.research.simulate import Setup
from siavash.signals.base import feats
from siavash.signals.registry import build_generators

SetupFn = Callable[[str, pd.Timestamp, Mapping[str, float], RegimeAssessment], list[Setup]]


def setup_fn(cfg: Any, v: Variant) -> SetupFn:
    kind = v.profile.kind
    if kind == "rules":
        return _rules(variant_config(cfg, v))
    if kind == "h1_breakout":
        return _h1_breakout(v.params)
    if kind == "trend_follow":
        return _trend_follow(v.params)
    raise ValueError(f"unknown profile kind {kind!r}")


def _rules(vcfg: Any) -> SetupFn:
    gens = build_generators(vcfg)

    def fn(sym: str, ts: pd.Timestamp, row: Mapping[str, float], ra: RegimeAssessment) -> list[Setup]:
        out = []
        for g in gens:
            for c in g.generate(sym, ts.to_pydatetime(), row, ra):
                target = c.tp1_price if c.tp1_price is not None else c.take_profit  # T-401 convention
                out.append(
                    Setup(
                        ts=ts,
                        symbol=sym,
                        strategy=c.strategy.value,
                        entry_ref=c.entry_ref_price,
                        stop_loss=c.stop_loss,
                        take_profit=target,
                        time_stop_minutes=c.time_stop_minutes,
                        expected_move_pct=c.expected_move_pct,
                    )
                )
        return out

    return fn


def _need(p: Mapping[str, float], *keys: str) -> list[float]:
    missing = [k for k in keys if k not in p]
    if missing:
        raise KeyError(f"profile params missing: {missing}")
    return [float(p[k]) for k in keys]


H1_KEYS = ("1h_close", "1h_donchian_high_dist", "1h_vol_z20", "4h_ema20", "4h_ema50", "4h_adx14")


def _h1_breakout(p: Mapping[str, float]) -> SetupFn:
    """1h close above the previous 1h Donchian high with volume, 4h uptrend filter, fixed % targets."""
    vol_z, adx4, tp, sl, tstop = _need(p, "vol_z_min", "adx_4h_min", "tp_pct", "sl_pct", "time_stop_min")

    def fn(sym: str, ts: pd.Timestamp, row: Mapping[str, float], ra: RegimeAssessment) -> list[Setup]:
        if ts.minute != 0:
            return []
        v = feats(row, H1_KEYS)
        if v is None:
            return []
        ok = (
            v["1h_donchian_high_dist"] > 0
            and v["1h_vol_z20"] > vol_z
            and v["4h_ema20"] > v["4h_ema50"]
            and v["4h_adx14"] > adx4
        )
        if not ok:
            return []
        close = v["1h_close"]
        return [
            Setup(
                ts,
                sym,
                "V_H1_BREAKOUT",
                close,
                close * (1 - sl / 100),
                close * (1 + tp / 100),
                int(tstop),
                tp,
            )
        ]

    return fn


TF_KEYS = ("1h_close", "1h_atr14", "1h_ema20", "1h_ema50", "1h_adx14", "1h_ret_1", "4h_ema20", "4h_ema50")


def _trend_follow(p: Mapping[str, float]) -> SetupFn:
    """Long-only trend following: 1h and 4h EMA20 > EMA50, 1h close above EMA20 and rising, ATR stop,
    ATR trailing stop, a distant ATR take-profit and a long time stop."""
    adx1, sl_atr, trail_atr, tp_atr, tstop = _need(
        p, "adx_1h_min", "sl_atr", "trail_atr", "tp_atr", "time_stop_min"
    )

    def fn(sym: str, ts: pd.Timestamp, row: Mapping[str, float], ra: RegimeAssessment) -> list[Setup]:
        if ts.minute != 0:
            return []
        v = feats(row, TF_KEYS)
        if v is None or v["1h_atr14"] <= 0:
            return []
        ok = (
            v["4h_ema20"] > v["4h_ema50"]
            and v["1h_ema20"] > v["1h_ema50"]
            and v["1h_close"] > v["1h_ema20"]
            and v["1h_ret_1"] > 0
            and v["1h_adx14"] > adx1
        )
        if not ok:
            return []
        close, atr = v["1h_close"], v["1h_atr14"]
        return [
            Setup(
                ts,
                sym,
                "V_TREND_FOLLOW",
                close,
                close - sl_atr * atr,
                close + tp_atr * atr,
                int(tstop),
                tp_atr * atr / close * 100,
                trail_dist=trail_atr * atr,
            )
        ]

    return fn
