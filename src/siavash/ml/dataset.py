"""ML dataset (T-402).

Same feature code as live (``build_features`` + ``market_features`` + ``implied_features``) over
historical candles; every SignalGenerator runs on every closed 15m candle **without the regime
filter** (all strategies enabled) and the rule regime is added as one-hot features. Each candidate is
labelled with the triple barrier (T-401) on the candles after the signal.

Output columns: ``ts, symbol, strategy, regime, <FEATURES>, regime_<R>..., label, ret_pct,
exit_reason, bars_held``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from siavash.core.models import RegimeAssessment
from siavash.core.types import Regime, StrategyId, Timeframe
from siavash.data.implied import implied_features
from siavash.features.builder import FeatureParams, add_cross_features, build_features
from siavash.features.feature_set import FEATURE_SET_VERSION, FEATURES
from siavash.features.regime import RuleRegimeProvider, market_features
from siavash.logging import get_logger
from siavash.ml.labeling import label_candidate
from siavash.signals.fee_filter import round_trip_fee_pct
from siavash.signals.registry import build_generators

log = get_logger(__name__)

REGIME_COLUMNS = [f"regime_{r.value}" for r in Regime]
META_COLUMNS = ["ts", "symbol", "strategy", "regime"]
LABEL_COLUMNS = ["label", "ret_pct", "exit_reason", "bars_held"]
MODEL_COLUMNS = [*FEATURES, *REGIME_COLUMNS]  # model inputs


def dataset_path(root: str | Path = "data/ml") -> Path:
    return Path(root) / f"dataset_{FEATURE_SET_VERSION}.parquet"


def slippage_pct(cfg: Any, symbol: str) -> float:
    """Assumed one-way slippage: half the assumed spread + extra slippage (``base.bt``)."""
    return float(cfg.base.bt.spread_for(symbol)) / 2 + float(cfg.base.bt.extra_slippage_pct)


def symbol_frames(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]], cfg: Any, btc: str = "BTC/TMN"
) -> dict[str, pd.DataFrame]:
    """Per-symbol feature frames (index = 15m close time) with market and implied features attached."""
    params = FeatureParams.from_config(cfg)
    feats = {
        s: build_features({tf: v for tf, v in fr.items()}, params)
        for s, fr in frames.items()
        if Timeframe.M15 in fr and not fr[Timeframe.M15].empty
    }
    mkt = market_features(feats, btc) if btc in feats else pd.DataFrame()
    window = int(cfg.strategy.s3.z_window_bars)
    out: dict[str, pd.DataFrame] = {}
    for s, df in feats.items():
        df = add_cross_features(df, mkt)
        base, _, quote = s.partition("/")
        legs = (s, f"{base}/USDT", "USDT/TMN")
        if quote == "TMN" and base != "USDT" and all(x in frames for x in legs):
            tmn, usdt, usdt_tmn = (frames[x][Timeframe.M15] for x in legs)
            imp = implied_features(tmn, usdt, usdt_tmn, window_bars=window)
            df = add_cross_features(df, imp)
        out[s] = df
    return out


def build_dataset(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    cfg: Any,
    symbols: list[str] | None = None,
    btc: str = "BTC/TMN",
) -> pd.DataFrame:
    """Labelled candidates for every TMN market in ``frames`` (or ``symbols``)."""
    rule = RuleRegimeProvider(cfg)
    gens = build_generators(cfg)
    fee = round_trip_fee_pct(cfg)
    feats = symbol_frames(frames, cfg, btc)
    targets = symbols or sorted(s for s in feats if s.endswith("/TMN"))
    rows: list[dict[str, Any]] = []
    for sym in targets:
        if sym not in feats:
            continue
        df = feats[sym]
        m15 = (
            frames[sym][Timeframe.M15].dropna(subset=["close"]).sort_values("ts_open").reset_index(drop=True)
        )
        opens = m15["ts_open"].astype("int64").to_numpy()
        slip = slippage_pct(cfg, sym)
        for ts, r in df.iterrows():
            row = {k: float(v) for k, v in r.items()}  # same row shape as FeatureService.row (NaN kept)
            regime, _ = rule.classify(row)
            ra = RegimeAssessment(
                ts=ts,
                regime=regime,
                risk_multiplier=1.0,
                enabled_strategies=list(StrategyId),
                reasons=[],
                source="rule",
                rule_regime=regime,
            )
            cands = []
            for g in gens:
                try:
                    cands += g.generate(sym, ts.to_pydatetime(), row, ra)
                except Exception as e:  # a broken signal must not abort the whole dataset
                    log.warning("dataset_signal_failed", symbol=sym, error=str(e))
            if not cands:
                continue
            i0 = int(np.searchsorted(opens, pd.Timestamp(ts).value, side="left"))
            for c in cands:
                n = -(-c.time_stop_minutes // 15)
                if i0 + n > len(m15):  # outcome window runs past the data: label would be biased
                    continue
                future = m15.iloc[i0 : i0 + n]
                label, ret, reason, held = label_candidate(c, future, fee, slip)
                rec: dict[str, Any] = {
                    "ts": ts,
                    "symbol": sym,
                    "strategy": c.strategy.value,
                    "regime": regime.value,
                }
                rec.update({f: r.get(f, np.nan) for f in FEATURES})
                rec.update({col: float(col == f"regime_{regime.value}") for col in REGIME_COLUMNS})
                rec.update({"label": label, "ret_pct": ret, "exit_reason": reason, "bars_held": held})
                rows.append(rec)
    cols = [*META_COLUMNS, *MODEL_COLUMNS, *LABEL_COLUMNS]
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["ts", "symbol", "strategy"]).reset_index(drop=True)


def dataset_report(ds: pd.DataFrame) -> str:
    """Markdown summary: samples per strategy / market, class balance, unfiltered mean return."""
    lines = [f"# ML dataset ({FEATURE_SET_VERSION})", ""]
    if ds.empty:
        return "\n".join([*lines, "_no candidates_", ""])
    lines += [
        f"- samples: **{len(ds)}** · period {ds['ts'].min()} → {ds['ts'].max()}",
        f"- positive class (label=1): {ds['label'].mean() * 100:.1f} %",
        f"- unfiltered mean net return: {ds['ret_pct'].mean():.3f} % per trade",
        "",
    ]
    for key in ("strategy", "symbol", "regime", "exit_reason"):
        g = ds.groupby(key).agg(n=("label", "size"), pos=("label", "mean"), ret=("ret_pct", "mean"))
        lines += [f"## By {key}", "", f"| {key} | samples | label=1 % | mean ret % |", "|---|---:|---:|---:|"]
        lines += [f"| {k} | {int(v.n)} | {v.pos * 100:.1f} | {v.ret:.3f} |" for k, v in g.iterrows()]
        lines.append("")
    return "\n".join(lines)
