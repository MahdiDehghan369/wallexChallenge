"""Edge diagnostics on historical data: candidates, edge breakdown, fee sweep, variants, hold-out.

Discipline (no overfitting):
- the last ``holdout_days`` are a hold-out: dev candidates whose time-stop window reaches into it are
  purged; nothing in the dev reports reads hold-out rows;
- variants with a parameter grid are scored walk-forward: in window ``w`` the variant with the best
  cumulative net return over windows ``< w`` is traded; only those out-of-sample windows are summed;
- the ML filter is scored with a nested threshold (chosen on earlier folds only) next to the
  optimistic in-sample θ of T-404;
- the hold-out is evaluated once (the script refuses a second run).

Costs: maker = taker = round-trip / 2; taker legs pay the assumed slippage (``base.bt``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from siavash.core.models import RegimeAssessment
from siavash.core.types import StrategyId, Timeframe
from siavash.features.regime import RuleRegimeProvider
from siavash.logging import get_logger
from siavash.ml.dataset import MODEL_COLUMNS, slippage_pct, symbol_frames
from siavash.ml.threshold import max_drawdown, select_threshold
from siavash.ml.train import predict_raw, walk_forward
from siavash.research.profiles import DiagnosticSettings, Profile, Variant, variant_config
from siavash.research.simulate import Bars, gross_return, labels, net_return, non_overlapping, simulate
from siavash.research.variants import setup_fn

log = get_logger(__name__)

BASELINE = "baseline"
TEHRAN_OFFSET_MIN = 210  # UTC+3:30 (label only)


@dataclass(frozen=True)
class Split:
    data_start: pd.Timestamp
    data_end: pd.Timestamp
    holdout_start: pd.Timestamp


def make_split(frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]], holdout_days: float) -> Split:
    m15 = [fr[Timeframe.M15] for s, fr in frames.items() if s.endswith("/TMN") and Timeframe.M15 in fr]
    start = min(pd.Timestamp(d["ts_open"].min()) for d in m15)
    end = max(pd.Timestamp(d["ts_open"].max()) for d in m15) + pd.Timedelta(minutes=15)
    return Split(start, end, end - pd.Timedelta(days=holdout_days))


def turnover_rank(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]], symbols: Iterable[str], until: pd.Timestamp
) -> list[str]:
    """Symbols by dev-period quote turnover (Σ close × volume of 15m bars), highest first."""
    tv = {}
    for s in symbols:
        d = frames[s][Timeframe.M15]
        d = d[d["ts_open"] < until]
        tv[s] = float((d["close"] * d["volume"]).sum())
    return sorted(tv, key=lambda s: -tv[s])


def build_candidates(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    cfg: Any,
    variants: list[Variant],
    split: Split,
    denylist: frozenset[str],
    btc: str = "BTC/TMN",
) -> pd.DataFrame:
    """One row per (variant, setup) with raw fill / exit prices; ML features on baseline rows."""
    rule = RuleRegimeProvider(cfg)
    feats = symbol_frames(frames, cfg, btc)
    targets = sorted(s for s in feats if s.endswith("/TMN") and s not in denylist)
    ranked = turnover_rank(frames, targets, split.holdout_start)
    plans = []
    for v in variants:
        n = v.profile.symbols.top_liquid
        syms = set(ranked[:n]) if n else None
        regime_map = (
            variant_config(cfg, v).strategy.regime.enabled_strategies if v.profile.kind == "rules" else None
        )
        plans.append((v, setup_fn(cfg, v), syms, regime_map))
    rows: list[dict[str, Any]] = []
    for sym in targets:
        bars = Bars.from_frame(frames[sym][Timeframe.M15])
        slip = slippage_pct(cfg, sym)
        active = [p for p in plans if p[2] is None or sym in p[2]]
        log.info("diag_symbol", symbol=sym, variants=len(active))
        for ts, r in feats[sym].iterrows():
            row = {k: float(x) for k, x in r.items()}
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
            for v, fn, _, regime_map in active:
                for s in fn(sym, pd.Timestamp(ts), row, ra):
                    ex = v.profile.execution
                    limit = s.entry_ref * (1 - ex.entry_offset_pct / 100)
                    res = simulate(bars, s, ex.mode, ex.entry_ttl_bars, limit)
                    if res is None:
                        continue
                    if regime_map is not None:
                        allowed = s.strategy in regime_map.get(regime.value, [])
                    else:
                        allowed = regime.value != "PANIC"
                    end = pd.Timestamp(ts) + pd.Timedelta(minutes=s.time_stop_minutes)
                    period = (
                        "holdout"
                        if ts >= split.holdout_start
                        else "dev"
                        if end <= split.holdout_start
                        else "purged"
                    )
                    rec: dict[str, Any] = {
                        "variant": v.key,
                        "family": v.family,
                        "ts": pd.Timestamp(ts),
                        "symbol": sym,
                        "strategy": s.strategy,
                        "regime": regime.value,
                        "allowed_by_regime": allowed,
                        "period": period,
                        "expected_move_pct": s.expected_move_pct,
                        "slip_pct": slip,
                        "filled": res.filled,
                        "entry_raw": res.entry_raw,
                        "exit_raw": res.exit_raw,
                        "entry_slip": res.entry_slip,
                        "entry_maker": res.entry_maker,
                        "exit_slip": res.exit_slip,
                        "exit_maker": res.exit_maker,
                        "reason": res.reason,
                        "bars_held": res.bars_held,
                        "exit_ts": res.exit_ts,
                    }
                    if v.key == BASELINE:
                        rec.update(
                            {f: row.get(f, np.nan) for f in MODEL_COLUMNS if not f.startswith("regime_")}
                        )
                        rec.update({f"regime_{x}": float(x == regime.value) for x in _regimes()})
                    rows.append(rec)
    return pd.DataFrame(rows).sort_values(["variant", "ts", "symbol", "strategy"]).reset_index(drop=True)


def _regimes() -> list[str]:
    return [c.removeprefix("regime_") for c in MODEL_COLUMNS if c.startswith("regime_")]


# ---------------------------------------------------------------------------------------- helpers
def _stats(x: pd.Series) -> dict[str, float]:
    n = len(x)
    mean = float(x.mean()) if n else math.nan
    se = float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else math.nan
    return {"n": n, "mean": mean, "se": se, "t": mean / se if se and se > 0 else math.nan}


def _f(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def live_filters(df: pd.DataFrame, cfg: Any, rt: float, regime_filter: Mapping[str, bool]) -> pd.Series:
    """Fee filter (``expected_move ≥ fee_multiple × rt``) and the profile's regime filter."""
    fee_ok = df["expected_move_pct"] >= float(cfg.strategy.fee_multiple) * rt
    reg = df["family"].map(regime_filter).fillna(True).astype(bool)
    return fee_ok & (df["allowed_by_regime"] | ~reg) & df["filled"]


def taken_trades(df: pd.DataFrame, cfg: Any, rt: float, regime_filter: Mapping[str, bool]) -> pd.DataFrame:
    """Live-like trades: filters, then one open position per (variant, symbol); ``net`` at ``rt``."""
    f = df[live_filters(df, cfg, rt, regime_filter)]
    t = f[non_overlapping(f)].copy()
    t["net"] = net_return(t, rt / 2, rt / 2)
    return t


# ---------------------------------------------------------------------------------------- 1. edge
def edge_breakdown(
    cand: pd.DataFrame, settings: DiagnosticSettings, split: Split, drift: Mapping[str, float] | None = None
) -> str:
    b = cand[(cand["variant"] == BASELINE) & (cand["period"] == "dev")]
    t = with_excess(b[non_overlapping(b, ("variant", "symbol", "strategy"))], drift)
    t["net0"] = net_return(t, 0.0, 0.0)  # after assumed slippage, before fees
    t["hour_utc"] = t["ts"].dt.hour
    mins = settings.min_group_trades
    lines = [
        "# Edge breakdown — gross returns before fees and slippage",
        "",
        f"Data: real 15m candles {split.data_start:%Y-%m-%d} → {split.holdout_start:%Y-%m-%d} (dev period; the last "
        f"{settings.holdout_days:g} days are a hold-out and are **not** in this report). Strategies S1/S2/S3 with the "
        "current `config/strategy.yaml`, every TMN market of the dataset minus the denylist, **no regime filter** (the "
        "regime is a column below), taker entry at the next 15m open, T-401 triple barrier (TP1 as target for S1).",
        "",
        f"- candidates: {len(b)} (overlapping); trades with one open position per symbol × strategy: **{len(t)}**",
        "- **gross** = exit / entry − 1 without fees and slippage; win % = share of trades with gross > 0",
        "- **BE fee** = break-even round-trip fee = mean gross (the fee at which the mean net return is 0 with zero "
        "slippage); **BE fee after slip.** = the same after the assumed spread / slippage of `base.bt` "
        "(negative = loses before any fee)",
        f"- t = mean / standard error; groups with < {mins} trades are marked _(noise)_; trades of one symbol can "
        "still overlap across strategies, so t is optimistic",
        "- **drift-adj.** = gross − (the market's mean 15m return over the dev period × bars held): what is left "
        "after the passive drift a long position collects. The dev period was a strong rally in TMN terms "
        "(toman depreciation + crypto up; see `variants.md` → buy and hold), so this is the part of the edge that "
        "does **not** need the rally to continue",
        "",
    ]
    ov = _stats(t["gross"])
    ex = _stats(t["excess"])
    lines += [
        f"**All trades:** n={ov['n']}, win {(_pct(t['gross'] > 0))} %, mean gross {_f(ov['mean'])} %, "
        f"t={_f(ov['t'], 2)}, drift-adj. {_f(ex['mean'])} % (t={_f(ex['t'], 2)}), BE fee {_f(ov['mean'])} %, "
        f"BE fee after slippage {_f(float(t['net0'].mean()))} %, mean bars held {t['bars_held'].mean():.1f}",
        "",
    ]
    t["hour_utc_label"] = t["hour_utc"].map(lambda h: f"{h:02d} UTC ({_tehran(h)} Tehran)")
    for key, title in (
        ("strategy", "strategy"),
        ("symbol", "market"),
        ("regime", "regime (rule regime at signal time)"),
        ("hour_utc_label", "hour of day (signal candle close)"),
        ("reason", "exit reason"),
    ):
        lines += _group_table(t, [key], title, mins)
    lines += _group_table(t, ["strategy", "regime"], "strategy × regime", mins)
    lines += _group_table(t, ["strategy", "symbol"], "strategy × market", mins, top=None)
    return "\n".join(lines)


def _pct(mask: pd.Series) -> str:
    return f"{float(mask.mean()) * 100:.1f}" if len(mask) else "—"


def _tehran(h: int) -> str:
    m = (h * 60 + TEHRAN_OFFSET_MIN) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _group_table(
    t: pd.DataFrame, keys: list[str], title: str, mins: int, top: int | None = None
) -> list[str]:
    head = " | ".join(keys)
    out = [
        f"## By {title}",
        "",
        f"| {head} | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % "
        "| BE fee after slip. % |",
        "|" + "---|" * len(keys) + "---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows = []
    for k, g in t.groupby(keys):
        st = _stats(g["gross"])
        kk = k if isinstance(k, tuple) else (k,)
        rows.append((kk, st, g))
    rows.sort(key=lambda r: -r[1]["mean"] if math.isfinite(r[1]["mean"]) else 0)
    if keys[0] in ("hour_utc_label",):
        rows.sort(key=lambda r: str(r[0]))
    for kk, st, g in rows[:top] if top else rows:
        noise = " _(noise)_" if st["n"] < mins else ""
        out.append(
            f"| {' | '.join(str(x) for x in kk)}{noise} | {st['n']} | {_pct(g['gross'] > 0)} | {_f(st['mean'])} "
            f"| {_f(float(g['gross'].median()))} | {_f(st['t'], 2)} | {_f(float(g['excess'].mean()))} "
            f"| {_f(st['mean'])} | {_f(float(g['net0'].mean()))} |"
        )
    return [*out, ""]


# ---------------------------------------------------------------------------------------- 2. fees
def ml_frame(b: pd.DataFrame, rt: float) -> pd.DataFrame:
    """T-402 dataset re-labelled at round-trip fee ``rt`` (exactly what the T-401 labeller would give)."""
    net = net_return(b, rt / 2, rt / 2)
    ds = b[["ts", "symbol", "strategy", *MODEL_COLUMNS]].copy()
    ds["ret_pct"] = net
    ds["label"] = labels(b, net)
    return ds


def nested_filter(
    oof: pd.DataFrame, fractions: list[float], min_tpd: float
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(kept, all)`` over folds ≥ 1: in fold k keep ``p_raw ≥`` the threshold that maximised the
    cumulative net return over folds < k (top-q fractions of past predictions, ≥ ``min_tpd`` trades/day)."""
    folds = sorted(oof["fold"].unique())
    kept, base = [], []
    for k in folds[1:]:
        past, cur = oof[oof["fold"] < k], oof[oof["fold"] == k]
        days = max((past["ts"].max() - past["ts"].min()).total_seconds() / 86400, 1e-9)
        best: tuple[float, float] | None = None
        for q in fractions:
            thr = float(past["p_raw"].quantile(1 - q))
            sel = past[past["p_raw"] >= thr]
            if len(sel) / days < min_tpd:
                continue
            cum = float(sel["ret_pct"].sum())
            if best is None or cum > best[0]:
                best = (cum, thr)
        thr = best[1] if best else -math.inf
        kept.append(cur[cur["p_raw"] >= thr])
        base.append(cur)
    empty = oof.iloc[0:0]
    return (pd.concat(kept) if kept else empty), (pd.concat(base) if base else empty)


def fee_sweep(
    cand: pd.DataFrame, cfg: Any, settings: DiagnosticSettings, regime_filter: Mapping[str, bool]
) -> tuple[str, list[dict[str, Any]]]:
    b = cand[(cand["variant"] == BASELINE) & (cand["period"] == "dev") & cand["filled"]].reset_index(
        drop=True
    )
    min_tpd = float(cfg.strategy.ml.min_trades_per_day)
    rows: list[dict[str, Any]] = []
    for rt in settings.fee_rt_pct:
        ds = ml_frame(b, rt)
        live = taken_trades(
            cand[(cand["variant"] == BASELINE) & (cand["period"] == "dev")], cfg, rt, regime_filter
        )
        wf = walk_forward(ds, cfg)
        oof = wf.oof
        thr = select_threshold(oof, min_tpd)
        best = thr.table[thr.table["theta"] == thr.theta].iloc[0] if thr.theta is not None else None
        kept, base = nested_filter(oof, settings.nested_keep_fractions, min_tpd)
        auc = wf.fold_metrics["auc"].dropna()
        rows.append(
            {
                "rt": rt,
                "n": len(ds),
                "mean_all": float(ds["ret_pct"].mean()),
                "pos": float(ds["label"].mean()),
                "live_n": len(live),
                "live_mean": float(live["net"].mean()) if len(live) else math.nan,
                "live_cum": float(live["net"].sum()),
                "oof_n": len(oof),
                "oof_mean": float(oof["ret_pct"].mean()) if len(oof) else math.nan,
                "auc": float(auc.mean()) if len(auc) else math.nan,
                "theta": thr.theta,
                "theta_n": int(best["n_trades"]) if best is not None else 0,
                "theta_mean": float(best["mean_ret_pct"])
                if best is not None and best["n_trades"]
                else math.nan,
                "nested_base_n": len(base),
                "nested_base_mean": float(base["ret_pct"].mean()) if len(base) else math.nan,
                "nested_n": len(kept),
                "nested_mean": float(kept["ret_pct"].mean()) if len(kept) else math.nan,
                "nested_cum": float(kept["ret_pct"].sum()),
                "nested_dd": max_drawdown(kept.sort_values("ts")["ret_pct"]) if len(kept) else 0.0,
            }
        )
        log.info("diag_fee_done", rt=rt)
    lines = [
        "# Fee sensitivity — baseline strategies, with and without the ML filter",
        "",
        "Dev period only (hold-out excluded). For each round-trip fee (maker = taker = rt / 2, plus the assumed "
        "slippage on taker legs) the T-402 dataset is **re-labelled** (TIME exits flip with the fee) and the T-403 "
        "LightGBM walk-forward (45 d train / 12 h embargo / 7 d test, `strategy.yaml > ml`) is re-run.",
        "",
        "- **all candidates**: every S1/S2/S3 candidate (the ML dataset; overlapping, no filters)",
        "- **live rules, no ML**: fee filter (`fee_multiple` × rt) + regime filter + one open position per symbol",
        "- **ML θ (in-sample)**: best θ of the T-404 table — θ is chosen on the same OOF predictions it is scored on "
        "(optimistic)",
        "- **ML nested**: in fold k the threshold is chosen on folds < k only (honest); compare with "
        "**no ML, same folds**",
        "",
        "| RT fee % | candidates | mean net all % | label=1 % | live rules: trades | mean % | cum % | mean OOF AUC "
        "| ML θ | θ trades | θ mean % | no ML same folds: n | mean % | ML nested: n | mean % | cum % | max DD % |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['rt']:.1f} | {r['n']} | {_f(r['mean_all'])} | {r['pos'] * 100:.1f} | {r['live_n']} | "
            f"{_f(r['live_mean'])} | {_f(r['live_cum'], 1)} | {_f(r['auc'])} | {_f(r['theta'])} | {r['theta_n']} | "
            f"{_f(r['theta_mean'])} | {r['nested_base_n']} | {_f(r['nested_base_mean'])} | {r['nested_n']} | "
            f"{_f(r['nested_mean'])} | {_f(r['nested_cum'], 1)} | {_f(r['nested_dd'], 1)} |"
        )
    return "\n".join([*lines, ""]), rows


# ---------------------------------------------------------------------------------------- 4. variants
def windows(split: Split, start: pd.Timestamp, days: float) -> list[pd.Timestamp]:
    edges = [start.floor("D")]
    while edges[-1] + pd.Timedelta(days=days) < split.holdout_start:
        edges.append(edges[-1] + pd.Timedelta(days=days))
    return [*edges, split.holdout_start]


def _window_index(ts: pd.Series, edges: list[pd.Timestamp]) -> pd.Series:
    inner = pd.DatetimeIndex(edges[1:]).as_unit("ns").asi8
    pos = np.searchsorted(inner, pd.DatetimeIndex(ts).as_unit("ns").asi8, side="right")
    return pd.Series(pos, index=ts.index)


def walk_forward_select(
    trades: pd.DataFrame, family_variants: list[str], n_windows: int, min_trades: int
) -> tuple[pd.DataFrame, list[str | None]]:
    """OOS trades of a family: in window w trade the variant with the best cumulative net over
    windows < w (at least ``min_trades`` past trades); window 0 is never traded (nothing to learn from)."""
    picks: list[str | None] = [None]
    out = []
    for w in range(1, n_windows):
        past = trades[trades["window"] < w]
        score = {v: (float(g["net"].sum()), len(g)) for v, g in past.groupby("variant")}
        ok = [v for v in family_variants if score.get(v, (0.0, 0))[1] >= min_trades]
        pick = max(ok, key=lambda v: score[v][0]) if ok else None
        picks.append(pick)
        if pick is not None:
            out.append(trades[(trades["window"] == w) & (trades["variant"] == pick)])
    return (pd.concat(out) if out else trades.iloc[0:0]), picks


def variants_report(
    cand: pd.DataFrame,
    cfg: Any,
    settings: DiagnosticSettings,
    split: Split,
    profiles: list[Profile],
    variants: list[Variant],
    bench: Mapping[str, float] | None = None,
    drift: Mapping[str, float] | None = None,
) -> tuple[str, dict[str, Any]]:
    dev = cand[cand["period"] == "dev"]
    regime_filter = {p.name: p.regime_filter for p in profiles}
    edges = windows(split, dev["ts"].min(), settings.window_days)
    nw = len(edges) - 1
    oos_days = (edges[-1] - edges[1]).total_seconds() / 86400
    fam_vars: dict[str, list[str]] = {}
    for v in variants:
        fam_vars.setdefault(v.family, []).append(v.key)
    summary: dict[str, Any] = {"n_variants": len(variants), "families": {}, "edges": [str(e) for e in edges]}
    lines = [
        "# Variant profiles — walk-forward on the dev period",
        "",
        f"Profiles: `config/profiles/*.yaml` (the live `config/strategy.yaml` is not modified). **{len(profiles)} "
        f"profiles expanded to {len(variants)} variants** (grid points count as separate variants). Dev period "
        f"{edges[0]:%Y-%m-%d} → {split.holdout_start:%Y-%m-%d} in {nw} windows of {settings.window_days:g} days; "
        f"the last {settings.holdout_days:g} days are the hold-out (see `holdout.md`).",
        "",
        "Every number is a **live-like trade**: fee filter (`fee_multiple` × rt), the profile's regime filter, one "
        "open position per variant × symbol, net of fees (maker = taker = rt / 2) and of the assumed slippage on "
        "taker legs. Families with a grid are walk-forward: window w trades the variant that was best on windows < w "
        f"(≥ {settings.selection_min_trades} past trades). The OOS columns cover windows 1…{nw - 1} "
        f"({oos_days:.0f} days) for every family, so families are comparable; window 0 is only used for learning.",
        "",
    ]
    for p in profiles:
        lines.append(f"- **{p.name}** ({len(fam_vars.get(p.name, []))} variants): {p.description}")
    lines.append("")
    for rt in settings.fee_rt_pct:
        t = taken_trades(dev, cfg, rt, regime_filter)
        t["window"] = _window_index(t["ts"], edges)
        lines += [
            f"## Round-trip fee {rt:.1f} %",
            "",
            "| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for fam, keys in fam_vars.items():
            ft = t[t["family"] == fam]
            if len(keys) > 1:
                oos, picks = walk_forward_select(ft, keys, nw, settings.selection_min_trades)
            else:
                oos, picks = ft[ft["window"] >= 1], [None, *[keys[0]] * (nw - 1)]
            wins = oos.groupby("window")["net"].sum()
            pos = int((wins > 0).sum())
            dd = max_drawdown(oos.sort_values("ts")["net"]) if len(oos) else 0.0
            pk = ", ".join(_short(x) for x in picks[1:]) if len(keys) > 1 else "—"
            lines.append(
                f"| {fam} | {len(keys)} | {len(oos)} | {len(oos) / oos_days:.2f} | {_pct(oos['net'] > 0)} | "
                f"{_f(float(oos['net'].mean()) if len(oos) else math.nan)} | {_f(float(oos['net'].sum()), 1)} | "
                f"{_f(dd, 1)} | {pos}/{nw - 1} | {pk} |"
            )
            summary["families"].setdefault(fam, {})[f"{rt:g}"] = {
                "oos_trades": len(oos),
                "oos_mean": float(oos["net"].mean()) if len(oos) else None,
                "oos_cum": float(oos["net"].sum()),
                "windows_pos": pos,
            }
        lines.append("")
    # in-sample matrix (all dev windows) — for reference only
    lines += [
        "## Every variant, whole dev period (in-sample — for reference, not for selection)",
        "",
        "Mean net % per trade [trades] at each round-trip fee; `gross` = before fees and slippage (fee filter off); "
        "`drift-adj.` = gross − market drift × bars held (see `edge_breakdown.md`); `hold h` = mean hours held.",
        "",
        "| variant | gross mean % [n] | drift-adj. % | hold h | "
        + " | ".join(f"RT {rt:.1f} %" for rt in settings.fee_rt_pct)
        + " |",
        "|---|---:|---:|---:|" + "---:|" * len(settings.fee_rt_pct),
    ]
    per_rt = {rt: taken_trades(dev, cfg, rt, regime_filter) for rt in settings.fee_rt_pct}
    g0 = dev[dev["filled"]]
    g0 = g0[non_overlapping(g0)]
    for v in variants:
        gv = g0[g0["variant"] == v.key]
        cells = []
        for rt in settings.fee_rt_pct:
            tv = per_rt[rt]
            tv = tv[tv["variant"] == v.key]
            cells.append(f"{_f(float(tv['net'].mean()) if len(tv) else math.nan)} [{len(tv)}]")
        gx = with_excess(gv, drift)
        gm = float(gx["gross"].mean()) if len(gv) else math.nan
        em = float(gx["excess"].mean()) if len(gv) else math.nan
        fill = ""
        if v.profile.execution.mode == "maker":
            fv = dev[dev["variant"] == v.key]
            fill = f" · fill {_pct(fv['filled'])} %"
        lines.append(
            f"| {v.key} | {_f(gm)} [{len(gv)}]{fill} | {_f(em)} | {gx['bars_held'].mean() / 4:.1f} | "
            + " | ".join(cells)
            + " |"
        )
    lines.append("")
    # symbol-restricted families: where does the result come from?
    for p in profiles:
        if p.symbols.top_liquid is None:
            continue
        keys = fam_vars.get(p.name, [])
        lines += [
            f"## {p.name} by market (OOS windows 1…{nw - 1}, single variant)",
            "",
            "| market | " + " | ".join(f"RT {rt:.1f} %: mean [n]" for rt in settings.fee_rt_pct) + " |",
            "|---|" + "---:|" * len(settings.fee_rt_pct),
        ]
        sub = dev[dev["variant"].isin(keys)]
        tt = {rt: taken_trades(sub, cfg, rt, regime_filter) for rt in settings.fee_rt_pct}
        for sym in sorted(sub["symbol"].unique()):
            cells = []
            for rt in settings.fee_rt_pct:
                x = tt[rt]
                x = x[(x["symbol"] == sym) & (_window_index(x["ts"], edges) >= 1)]
                cells.append(f"{_f(float(x['net'].mean()) if len(x) else math.nan)} [{len(x)}]")
            lines.append(f"| {sym} | " + " | ".join(cells) + " |")
        lines.append("")
    if bench:
        lines += [
            f"## Benchmark: buy and hold over the same OOS windows ({edges[1]:%Y-%m-%d} → {edges[-1]:%Y-%m-%d})",
            "",
            "Price change of each market (TMN quote; part of any long-only TMN return is toman depreciation). "
            "One round trip of fees only, so this is the cost-free reference a long-only variant must beat.",
            "",
            "| market | buy & hold % |",
            "|---|---:|",
        ]
        lines += [f"| {s} | {r:.2f} |" for s, r in sorted(bench.items(), key=lambda kv: -kv[1])]
        lines += [f"| **equal-weight mean** | **{float(np.mean(list(bench.values()))):.2f}** |", ""]
    return "\n".join([*lines, ""]), summary


def drift_per_bar(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    symbols: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float]:
    """Mean 15m close-to-close return (%) of each symbol in ``[start, end)`` — the passive drift a long
    position collects per bar held."""
    out = {}
    for s in symbols:
        d = frames[s][Timeframe.M15].dropna(subset=["close"]).sort_values("ts_open")
        d = d[(d["ts_open"] >= start) & (d["ts_open"] < end)]
        out[s] = float(d["close"].pct_change().mean() * 100.0) if len(d) > 1 else 0.0
    return out


def with_excess(t: pd.DataFrame, drift: Mapping[str, float] | None) -> pd.DataFrame:
    """``gross`` and ``excess`` (= gross − symbol drift × bars held) columns."""
    t = t.copy()
    t["gross"] = gross_return(t)
    t["excess"] = t["gross"] - t["symbol"].map(drift or {}).fillna(0.0) * t["bars_held"]
    return t


def buy_and_hold(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    symbols: Iterable[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, float]:
    """Close-to-close change (%) of each symbol's 15m candles between ``start`` and ``end``."""
    out = {}
    for s in symbols:
        d = frames[s][Timeframe.M15].dropna(subset=["close"])
        d = d[(d["ts_open"] >= start) & (d["ts_open"] < end)]
        if len(d) > 1:
            out[s] = (float(d["close"].iloc[-1]) / float(d["close"].iloc[0]) - 1.0) * 100.0
    return out


def _short(key: str | None) -> str:
    if key is None:
        return "none"
    return key[key.find("[") + 1 : -1] if "[" in key else key


# ---------------------------------------------------------------------------------------- 5. hold-out
def holdout_report(
    cand: pd.DataFrame,
    cfg: Any,
    settings: DiagnosticSettings,
    split: Split,
    profiles: list[Profile],
    variants: list[Variant],
    bench: Mapping[str, float] | None = None,
) -> str:
    regime_filter = {p.name: p.regime_filter for p in profiles}
    dev, ho = cand[cand["period"] == "dev"], cand[cand["period"] == "holdout"]
    days = settings.holdout_days
    fam_vars: dict[str, list[str]] = {}
    for v in variants:
        fam_vars.setdefault(v.family, []).append(v.key)
    lines = [
        "# Final hold-out (evaluated once)",
        "",
        f"Hold-out: setups from {split.holdout_start:%Y-%m-%d %H:%M} to {split.data_end:%Y-%m-%d %H:%M} UTC "
        f"({days:g} days, never used before this run). For each family the variant is the one with the best "
        "cumulative net return over the **whole dev period** at the same fee; the ML filter is the final model "
        "(fit on all dev candidates) with the top-q threshold chosen on the dev OOF predictions. "
        "Setups whose time-stop window runs past the data end are dropped.",
        "",
        "| RT fee % | family | variant | trades | trades/day | win % | mean net % | cum % | max DD % |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rt in settings.fee_rt_pct:
        td, th = taken_trades(dev, cfg, rt, regime_filter), taken_trades(ho, cfg, rt, regime_filter)
        for fam, keys in fam_vars.items():
            score = {k: float(td[td["variant"] == k]["net"].sum()) for k in keys}
            pick = max(keys, key=lambda k: score[k])
            x = th[th["variant"] == pick]
            dd = max_drawdown(x.sort_values("ts")["net"]) if len(x) else 0.0
            lines.append(
                f"| {rt:.1f} | {fam} | {_short(pick) if len(keys) > 1 else '—'} | {len(x)} | {len(x) / days:.2f} | "
                f"{_pct(x['net'] > 0)} | {_f(float(x['net'].mean()) if len(x) else math.nan)} | "
                f"{_f(float(x['net'].sum()), 1)} | {_f(dd, 1)} |"
            )
    for p in profiles:
        if p.symbols.top_liquid is None:
            continue
        lines += [
            "",
            f"### {p.name} by market (hold-out)",
            "",
            "| RT fee % | market | trades | mean net % | cum % |",
        ]
        lines.append("|---:|---|---:|---:|---:|")
        for rt in settings.fee_rt_pct:
            th = taken_trades(ho[ho["family"] == p.name], cfg, rt, regime_filter)
            for sym, g in th.groupby("symbol"):
                lines.append(
                    f"| {rt:.1f} | {sym} | {len(g)} | {_f(float(g['net'].mean()))} | {_f(float(g['net'].sum()), 1)} |"
                )
    if bench:
        lines += [
            "",
            "### Benchmark: buy and hold over the hold-out",
            "",
            "| market | buy & hold % |",
            "|---|---:|",
        ]
        lines += [f"| {s} | {r:.2f} |" for s, r in sorted(bench.items(), key=lambda kv: -kv[1])]
        lines.append(f"| **equal-weight mean** | **{float(np.mean(list(bench.values()))):.2f}** |")
    lines += [
        "",
        "## ML filter on the hold-out (baseline candidates, no fee / regime filter, overlapping)",
        "",
        "| RT fee % | candidates | mean net no ML % | keep q | kept | mean net ML % | cum ML % |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    bd = dev[(dev["variant"] == BASELINE) & dev["filled"]].reset_index(drop=True)
    bh = ho[(ho["variant"] == BASELINE) & ho["filled"]].reset_index(drop=True)
    min_tpd = float(cfg.strategy.ml.min_trades_per_day)
    for rt in settings.fee_rt_pct:
        dsd, dsh = ml_frame(bd, rt), ml_frame(bh, rt)
        wf = walk_forward(dsd, cfg)
        if wf.model is None or wf.oof.empty or dsh.empty:
            lines.append(f"| {rt:.1f} | {len(dsh)} | — | — | — | — | — |")
            continue
        oof = wf.oof
        odays = max((oof["ts"].max() - oof["ts"].min()).total_seconds() / 86400, 1e-9)
        best: tuple[float, float, float] | None = None
        for q in settings.nested_keep_fractions:
            thr = float(oof["p_raw"].quantile(1 - q))
            sel = oof[oof["p_raw"] >= thr]
            if len(sel) / odays >= min_tpd and (best is None or sel["ret_pct"].sum() > best[0]):
                best = (float(sel["ret_pct"].sum()), thr, q)
        p_ho = predict_raw(wf.model, dsh[wf.features])
        thr, q = (best[1], best[2]) if best else (-math.inf, 1.0)
        kept = dsh[p_ho >= thr]
        lines.append(
            f"| {rt:.1f} | {len(dsh)} | {_f(float(dsh['ret_pct'].mean()))} | {q:g} | {len(kept)} | "
            f"{_f(float(kept['ret_pct'].mean()) if len(kept) else math.nan)} | {_f(float(kept['ret_pct'].sum()), 1)} |"
        )
    return "\n".join([*lines, ""])


# ---------------------------------------------------------------------------------------- 3. zero fee
def zero_fee_report(markets_path: str | Path, denylist: frozenset[str], allowed_quotes: list[str]) -> str:
    """``is_zero_fee`` of ``/hector/web/v1/markets`` (``result.markets[]``) vs the denylist."""
    d = json.loads(Path(markets_path).read_text(encoding="utf-8"))
    markets = d["result"]["markets"]
    zero = [m for m in markets if m.get("is_zero_fee") is True]
    lines = [
        "# Zero-fee markets (`is_zero_fee` of `/hector/web/v1/markets`)",
        "",
        f"Source: `{markets_path}` ({len(markets)} markets"
        + (f", fetched {d['fetched_at']}" if "fetched_at" in d else "")
        + f"). Allowed = `is_spot`, quote in {allowed_quotes}, not in `config/denylist.txt`.",
        "",
        f"- markets with `is_zero_fee: true`: **{len(zero)}**",
    ]
    allowed = []
    for m in zero:
        sym = f"{m['base_asset']}/{m['quote_asset']}"
        why = []
        if not m.get("is_spot"):
            why.append("not spot")
        if m["quote_asset"] not in allowed_quotes:
            why.append(f"quote {m['quote_asset']} not allowed")
        if sym in denylist:
            why.append("denylist")
        lines.append(f"  - {sym}: {'allowed' if not why else 'excluded (' + ', '.join(why) + ')'}")
        if not why:
            allowed.append(sym)
    lines += [
        f"- **allowed spot TMN markets with zero fee: {len(allowed)}**"
        + (f" → {', '.join(allowed)}" if allowed else ""),
        "",
        "`is_zero_fee` is a market flag; whether it applies to API orders of this account is not documented "
        "— confirm with `/v1/account/fee` (T-108) before relying on it.",
        "",
    ]
    return "\n".join(lines)
