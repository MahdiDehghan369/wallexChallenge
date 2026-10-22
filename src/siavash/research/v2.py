"""Research v2: three pre-registered hypotheses for large moves (``reports/research_v2/PREREGISTRATION.md``).

Every parameter comes from ``config/research_v2.yaml``. Signals use only bars closed at ``t``; entry is
a taker order at the next 15m open (``research.simulate``). One open position per variant × market.
Costs are applied afterwards (``simulate.net_return``) with the ``base.bt`` slippage model.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from siavash.core.types import Timeframe
from siavash.ml.dataset import slippage_pct
from siavash.research.simulate import Bars, Setup, gross_return, net_return, simulate

BAR = pd.Timedelta(minutes=15)
HYPOTHESES = ("H1", "H2", "H3")


# ------------------------------------------------------------------------------------------ config
def load_v2(path: str | Path = "config/research_v2.yaml") -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [h for h in HYPOTHESES if h not in cfg.get("hypotheses", {})]
    if missing:
        raise KeyError(f"research_v2.yaml: hypotheses missing {missing}")
    return cfg


@dataclass(frozen=True)
class V2Variant:
    hypothesis: str
    params: dict[str, float]

    @property
    def key(self) -> str:
        return f"{self.hypothesis}[" + ",".join(f"{k}={v:g}" for k, v in self.params.items()) + "]"


def variants(rc: Mapping[str, Any]) -> list[V2Variant]:
    out = []
    for h in HYPOTHESES:
        grid = rc["hypotheses"][h]["grid"]
        keys = list(grid)
        for combo in itertools.product(*(grid[k] for k in keys)):
            out.append(V2Variant(h, {k: float(v) for k, v in zip(keys, combo, strict=True)}))
    return out


def expected_move(v: V2Variant) -> float:
    return v.params["x_pct"] if v.hypothesis == "H1" else v.params["tp_pct"]


# ------------------------------------------------------------------------------------------ data
def dense_15m(m15: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Regular 15m grid ``[start, end)`` (index = ts_open). A bar without a trade keeps the last close,
    OHLC = that close and volume 0; ``age`` = bars since the last real candle (0 = real)."""
    d = m15.dropna(subset=["close"]).set_index("ts_open").sort_index()
    d = d[~d.index.duplicated(keep="last")]
    grid = pd.date_range(start, end - BAR, freq=BAR, tz="UTC")
    real = d.reindex(grid)
    is_real = real["close"].notna()
    c = real["close"].ffill()
    out = pd.DataFrame(
        {
            "open": real["open"].fillna(c),
            "high": real["high"].fillna(c),
            "low": real["low"].fillna(c),
            "close": c,
            "volume": real["volume"].fillna(0.0),
            "real": is_real,
        },
        index=grid,
    )
    run = (~is_real).astype(int)
    out["age"] = run.groupby(is_real.cumsum()).cumsum()
    return out


def bars_of(d: pd.DataFrame) -> Bars:
    v = d.dropna(subset=["close"])
    return Bars(
        (v.index.as_unit("ns").asi8 // 10**9),
        v["open"].to_numpy(float),
        v["high"].to_numpy(float),
        v["low"].to_numpy(float),
        v["close"].to_numpy(float),
    )


def _z(x: pd.Series, n: int) -> pd.Series:
    m, s = x.rolling(n).mean(), x.rolling(n).std(ddof=0)
    return (x - m) / s.replace(0.0, np.nan)


def market_features(
    tmn: pd.DataFrame, usdt: pd.DataFrame | None, ut: pd.DataFrame, rc: Mapping[str, Any]
) -> pd.DataFrame:
    """Per-bar signal inputs on the dense grid of ``tmn`` (row k = values at the **close** of bar k)."""
    h = rc["hypotheses"]
    stale = int(rc["stale_leg_bars"])
    f = pd.DataFrame(index=tmn.index)
    f["close"] = tmn["close"]
    if usdt is not None:
        implied = usdt["close"].reindex(tmn.index) * ut["close"].reindex(tmn.index)
        f["dev_pct"] = (tmn["close"] / implied - 1.0) * 100.0
        fresh = (
            (tmn["age"] <= stale)
            & (usdt["age"].reindex(tmn.index) <= stale)
            & (ut["age"].reindex(tmn.index) <= stale)
        )
        f["fresh"] = fresh & f["dev_pct"].notna()
    else:
        f["dev_pct"], f["fresh"] = np.nan, False
    # H2 inputs
    p2 = h["H2"]
    for k in p2["lookback_bars"]:
        f[f"r_tmn_{k}"] = (tmn["close"] / tmn["close"].shift(k) - 1.0) * 100.0
        f[f"r_usdt_{k}"] = (
            (usdt["close"] / usdt["close"].shift(k) - 1.0).reindex(tmn.index) * 100.0
            if usdt is not None
            else np.nan
        )
    vz = _z(tmn["volume"], int(p2["vol_z_window"]))
    f["vol_z_recent"] = vz.rolling(int(p2["vol_z_recent_bars"])).max()
    # H3 inputs (1h / 4h built from the 15m grid, label = bar open; known at open + period)
    p3 = h["H3"]
    h1 = tmn.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    prev_high = h1["high"].rolling(int(p3["high_lookback_h"])).max().shift(1)
    h1z = _z(h1["volume"], int(p3["vol_z_window"]))
    h4 = tmn.resample("4h", label="left", closed="left").agg({"close": "last"})
    ema = h4["close"].ewm(span=int(p3["ema_4h"]), adjust=False).mean()
    rising = (ema > ema.shift(1)).astype(float)
    rising.index = rising.index + pd.Timedelta(hours=4)  # known at the 4h close
    h1_close_t = h1.index + pd.Timedelta(hours=1)
    rising_at = rising.reindex(rising.index.union(h1_close_t)).ffill().reindex(h1_close_t)
    sig = pd.DataFrame(
        {
            "h3_break": (h1["close"] > prev_high).to_numpy(),
            "h3_volz": h1z.to_numpy(),
            "h3_ema_up": rising_at.to_numpy() == 1.0,
            "h1_close": h1["close"].to_numpy(),
        },
        index=h1.index + pd.Timedelta(minutes=45),  # last 15m bar of the hour (its close = 1h close)
    )
    f = f.join(sig)
    f["is_hour_close"] = f["h1_close"].notna()
    return f


# ------------------------------------------------------------------------------------------ signals
def signal_mask(f: pd.DataFrame, v: V2Variant, rc: Mapping[str, Any]) -> pd.Series:
    h = rc["hypotheses"][v.hypothesis]
    p = v.params
    if v.hypothesis == "H1":
        return f["fresh"] & (f["dev_pct"] <= -p["x_pct"])
    if v.hypothesis == "H2":
        hit = pd.Series(False, index=f.index)
        for k in h["lookback_bars"]:
            hit |= (f[f"r_tmn_{k}"] <= -p["y_pct"]) & (
                f[f"r_tmn_{k}"] - f[f"r_usdt_{k}"] <= -float(h["underperf_pct"])
            )
        return hit & (f["vol_z_recent"] >= float(h["vol_z_min"]))
    return (
        f["is_hour_close"]
        & f["h3_break"].eq(True)
        & (f["h3_volz"] >= float(h["vol_z_min"]))
        & f["h3_ema_up"].eq(True)
    )


def make_setup(sym: str, close_ts: pd.Timestamp, price: float, v: V2Variant, rc: Mapping[str, Any]) -> Setup:
    h = rc["hypotheses"][v.hypothesis]
    p = v.params
    if v.hypothesis == "H1":
        tp, sl = p["x_pct"], p["sl_pct"]
    elif v.hypothesis == "H2":
        tp, sl = p["tp_pct"], p["sl_pct"]
    else:
        tp, sl = p["tp_pct"], float(h["sl_pct"])
    return Setup(
        close_ts,
        sym,
        v.key,
        price,
        price * (1 - sl / 100),
        price * (1 + tp / 100),
        int(h["time_stop_min"]),
        tp,
    )


def market_trades(
    sym: str,
    tmn: pd.DataFrame,
    f: pd.DataFrame,
    vs: list[V2Variant],
    rc: Mapping[str, Any],
    cfg: Any,
    eval_start: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Taken trades of every variant on one market (one open position per variant; fee-free prices).

    SL / TP are set relative to the fill = the next bar's open (the taker entry price before slippage)."""
    has_px = tmn["close"].notna()
    bars = bars_of(tmn)  # rows with a price only, so exit flags are aligned on the same rows
    slip = slippage_pct(cfg, sym)
    exit_h1 = (f["fresh"] & (f["dev_pct"] >= float(rc["hypotheses"]["H1"]["exit_dev_pct"])))[
        has_px
    ].to_numpy()
    next_open = tmn["open"].shift(-1)
    rows = []
    for v in vs:
        m = signal_mask(f, v, rc).fillna(False).astype(bool)
        m = m & (f.index >= eval_start - BAR) & next_open.notna()
        busy_until: pd.Timestamp | None = None
        for ts_open in f.index[m.to_numpy()]:
            close_ts = ts_open + BAR
            if busy_until is not None and close_ts < busy_until:
                continue
            s = make_setup(sym, close_ts, float(next_open.at[ts_open]), v, rc)
            res = simulate(bars, s, "taker", exit_flags=exit_h1 if v.hypothesis == "H1" else None)
            if res is None:
                continue
            busy_until = res.exit_ts
            rows.append(
                {
                    "hypothesis": v.hypothesis,
                    "variant": v.key,
                    "symbol": sym,
                    "ts": close_ts,
                    "exit_ts": res.exit_ts,
                    "expected_move_pct": s.expected_move_pct,
                    "slip_pct": slip,
                    "filled": True,
                    "entry_raw": res.entry_raw,
                    "exit_raw": res.exit_raw,
                    "entry_slip": res.entry_slip,
                    "entry_maker": res.entry_maker,
                    "exit_slip": res.exit_slip,
                    "exit_maker": res.exit_maker,
                    "reason": res.reason,
                    "bars_held": res.bars_held,
                }
            )
    return rows


def build_trades(
    frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    symbols: list[str],
    usdt_frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
    rc: Mapping[str, Any],
    cfg: Any,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """All variants on ``symbols``; returns ``(trades, dense 15m frames)``. ``usdt_frames`` must hold
    USDT/TMN and the X/USDT pairs."""
    eval_start = start + pd.Timedelta(days=float(rc["warmup_days"]))
    ut = dense_15m(usdt_frames[rc["universe"]["usdt_tmn"]][Timeframe.M15], start, end)
    vs = variants(rc)
    rows: list[dict[str, Any]] = []
    dense: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        tmn = dense_15m(frames[sym][Timeframe.M15], start, end)
        pair = sym.replace("/TMN", "/USDT")
        usdt = dense_15m(usdt_frames[pair][Timeframe.M15], start, end) if pair in usdt_frames else None
        f = market_features(tmn, usdt, ut, rc)
        dense[sym] = tmn
        rows += market_trades(sym, tmn, f, vs, rc, cfg, eval_start)
    cols = ["hypothesis", "variant", "symbol", "ts", "exit_ts", "bars_held"]
    t = pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
    return t.sort_values(["variant", "ts", "symbol"]).reset_index(drop=True), dense


# ------------------------------------------------------------------------------------------ metrics
def add_returns(
    t: pd.DataFrame, dense: Mapping[str, pd.DataFrame], fees: list[float], eval_start: pd.Timestamp
) -> pd.DataFrame:
    """``gross``, ``excess`` (gross − market drift per bar × bars held; drift = mean 15m return from
    ``eval_start``), ``excess_idx`` (gross − equal-weight universe return over the same entry → exit
    window) and ``net_<rt>`` columns."""
    t = t.copy()
    if t.empty:
        for c in ("gross", "excess", "excess_idx", *[f"net_{rt:g}" for rt in fees]):
            t[c] = pd.Series(dtype=float)
        return t
    t["gross"] = gross_return(t)
    drift = {
        s: float(d.loc[d.index >= eval_start, "close"].pct_change().mean() * 100.0) for s, d in dense.items()
    }
    t["excess"] = t["gross"] - t["symbol"].map(drift) * t["bars_held"]
    closes = pd.DataFrame({s: d["close"] for s, d in dense.items()})
    idx = closes.index
    i0 = np.clip(idx.searchsorted(t["ts"] - BAR), 0, len(idx) - 1)  # signal bar (its close ≈ entry price)
    i1 = np.clip(idx.searchsorted(t["exit_ts"] - BAR), 0, len(idx) - 1)  # exit bar
    arr = closes.to_numpy(float)
    t["excess_idx"] = t["gross"] - np.nanmean(arr[i1] / arr[i0] - 1.0, axis=1) * 100.0
    for rt in fees:
        t[f"net_{rt:g}"] = net_return(t, rt / 2, rt / 2)
    return t


def fee_ok(t: pd.DataFrame, cfg: Any, rt: float) -> pd.Series:
    return t["expected_move_pct"] >= float(cfg.strategy.fee_multiple) * rt


def window_edges(start: pd.Timestamp, end: pd.Timestamp, days: float) -> list[pd.Timestamp]:
    edges = [start.floor("D")]
    while edges[-1] + pd.Timedelta(days=days) < end:
        edges.append(edges[-1] + pd.Timedelta(days=days))
    return [*edges, end]


def window_index(ts: pd.Series, edges: list[pd.Timestamp]) -> np.ndarray:
    inner = pd.DatetimeIndex(edges[1:]).as_unit("ns").asi8
    return np.searchsorted(inner, pd.DatetimeIndex(ts).as_unit("ns").asi8, side="right")


def walk_forward(
    t: pd.DataFrame, keys: list[str], n_windows: int, net: str, min_trades: int
) -> tuple[pd.DataFrame, list[str | None]]:
    """In window w trade the variant with the best cumulative ``net`` over windows < w."""
    picks: list[str | None] = [None]
    out = []
    for w in range(1, n_windows):
        past = t[t["window"] < w]
        score = {k: (float(g[net].sum()), len(g)) for k, g in past.groupby("variant")}
        ok = [k for k in keys if score.get(k, (0.0, 0))[1] >= min_trades]
        pick = max(ok, key=lambda k: score[k][0]) if ok else None
        picks.append(pick)
        if pick is not None:
            out.append(t[(t["window"] == w) & (t["variant"] == pick)])
    return (pd.concat(out) if out else t.iloc[0:0]), picks


def portfolio_drawdown(t: pd.DataFrame, net: str, fraction: float, max_concurrent: int) -> tuple[float, int]:
    """Max drawdown (%) of realised equity: each trade gets ``fraction`` × equity at entry while fewer
    than ``max_concurrent`` are open (otherwise it is skipped); P&L is booked at exit.
    Returns ``(max_dd_pct, trades_taken)``."""
    if t.empty:
        return 0.0, 0
    ev = []
    for i, r in enumerate(t.itertuples(index=False)):
        ev.append((r.ts, 1, i))
        ev.append((r.exit_ts, 0, i))  # exits first at equal timestamps
    ev.sort(key=lambda e: (e[0], e[1]))
    rets = t[net].to_numpy(float) / 100.0
    equity, peak, dd, taken = 1.0, 1.0, 0.0, 0
    alloc: dict[int, float] = {}
    for _, kind, i in ev:
        if kind == 1:
            if len(alloc) < max_concurrent:
                alloc[i] = fraction * equity
                taken += 1
        elif i in alloc:
            equity += alloc.pop(i) * rets[i]
            peak = max(peak, equity)
            dd = max(dd, 1.0 - equity / peak)
    return round(dd * 100.0, 3), taken


def _mean(x: pd.Series) -> float:
    return float(x.mean()) if len(x) else math.nan


def dev_evaluation(
    t: pd.DataFrame, rc: Mapping[str, Any], cfg: Any, eval_start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    """Per hypothesis and fee: walk-forward OOS stats; per variant: in-sample stats."""
    fees = [float(x) for x in rc["fee_rt_pct"]]
    edges = window_edges(eval_start, end, float(rc["window_days"]))
    nw = len(edges) - 1
    oos_days = (edges[-1] - edges[1]).total_seconds() / 86400 if nw > 1 else math.nan
    t = t.copy()
    t["window"] = window_index(t["ts"], edges) if len(t) else []
    keys = {h: [v.key for v in variants(rc) if v.hypothesis == h] for h in HYPOTHESES}
    out: dict[str, Any] = {"edges": [str(e) for e in edges], "oos_days": oos_days, "hyp": {}, "variants": {}}
    for h in HYPOTHESES:
        th = t[t["hypothesis"] == h]
        res: dict[str, Any] = {}
        for rt in fees:
            col = f"net_{rt:g}"
            ok = th[fee_ok(th, cfg, rt)]
            oos, picks = walk_forward(ok, keys[h], nw, col, int(rc["selection_min_trades"]))
            per_w = oos.groupby("window")[col].sum()
            traded_w = [w for w in range(1, nw) if picks[w] is not None]
            res[f"{rt:g}"] = {
                "n": len(oos),
                "mean_net": _mean(oos[col]),
                "mean_gross": _mean(oos["gross"]),
                "excess": _mean(oos["excess"]),
                "excess_idx": _mean(oos["excess_idx"]),
                "win": _mean(oos[col] > 0) * 100 if len(oos) else math.nan,
                "windows_pos": int((per_w > 0).sum()),
                "windows_oos": nw - 1,
                "windows_traded": len(traded_w),
                "trades_per_day": len(oos) / oos_days if oos_days else math.nan,
                "picks": picks[1:],
            }
        out["hyp"][h] = res
        for k in keys[h]:
            tv = th[th["variant"] == k]
            out["variants"][k] = {
                "n": len(tv),
                "gross": _mean(tv["gross"]),
                "excess": _mean(tv["excess"]),
                "excess_idx": _mean(tv["excess_idx"]),
                "hold_h": _mean(tv["bars_held"]) / 4 if len(tv) else math.nan,
                **{f"net_{rt:g}": _mean(tv[fee_ok(tv, cfg, rt)][f"net_{rt:g}"]) for rt in fees},
                **{f"n_{rt:g}": int(fee_ok(tv, cfg, rt).sum()) for rt in fees},
            }
    return out


def dev_picks(t: pd.DataFrame, rc: Mapping[str, Any], cfg: Any) -> dict[str, dict[str, str | None]]:
    """Hold-out variant per hypothesis and fee: best cumulative net over the whole DEV period."""
    out: dict[str, dict[str, str | None]] = {}
    for h in HYPOTHESES:
        th = t[t["hypothesis"] == h]
        out[h] = {}
        for rt in rc["fee_rt_pct"]:
            ok = th[fee_ok(th, cfg, float(rt))]
            s = ok.groupby("variant")[f"net_{float(rt):g}"].sum()
            out[h][f"{float(rt):g}"] = str(s.idxmax()) if len(s) else None
    return out


def holdout_evaluation(
    t: pd.DataFrame,
    picks: Mapping[str, Mapping[str, str | None]],
    rc: Mapping[str, Any],
    cfg: Any,
    days: float,
) -> dict[str, Any]:
    pf = rc["portfolio"]
    out: dict[str, Any] = {}
    for h in HYPOTHESES:
        out[h] = {}
        for rt in rc["fee_rt_pct"]:
            r = f"{float(rt):g}"
            k = picks[h][r]
            x = t[(t["variant"] == k)] if k else t.iloc[0:0]
            x = x[fee_ok(x, cfg, float(rt))]
            dd, taken = portfolio_drawdown(
                x, f"net_{r}", float(pf["position_fraction"]), int(pf["max_concurrent"])
            )
            out[h][r] = {
                "variant": k,
                "n": len(x),
                "mean_net": _mean(x[f"net_{r}"]),
                "cum_net": float(x[f"net_{r}"].sum()) if len(x) else 0.0,
                "mean_gross": _mean(x["gross"]),
                "excess": _mean(x["excess"]),
                "excess_idx": _mean(x["excess_idx"]),
                "win": _mean(x[f"net_{r}"] > 0) * 100 if len(x) else math.nan,
                "trades_per_day": len(x) / days if days else math.nan,
                "equity_dd": dd,
                "portfolio_trades": taken,
            }
    return out


def verdict(dev: Mapping[str, Any], ho: Mapping[str, Any] | None, rc: Mapping[str, Any]) -> dict[str, Any]:
    """The six pre-registered criteria per hypothesis at ``pass_fee_rt_pct``."""
    r = f"{float(rc['pass_fee_rt_pct']):g}"
    c = rc["pass"]
    out = {}
    for h in HYPOTHESES:
        d = dev["hyp"][h][r]
        win_share = d["windows_pos"] / d["windows_oos"] if d["windows_oos"] else 0.0
        checks = {
            "oos_mean_net": (d["mean_net"], _gt(d["mean_net"], c["oos_mean_net_pct"])),
            "windows_positive": (win_share, win_share >= float(c["oos_windows_positive"])),
            "drift_adj_gross": (d["excess"], _gt(d["excess"], c["drift_adj_gross_pct"])),
            "trades_per_day": (d["trades_per_day"], _ge(d["trades_per_day"], c["min_trades_per_day"])),
        }
        if ho is not None:
            x = ho[h][r]
            checks["holdout_mean_net"] = (x["mean_net"], _gt(x["mean_net"], c["holdout_mean_net_pct"]))
            checks["holdout_equity_dd"] = (
                x["equity_dd"],
                x["n"] > 0 and x["equity_dd"] < float(rc["portfolio"]["max_drawdown_pct"]),
            )
        out[h] = {"checks": checks, "pass": all(ok for _, ok in checks.values()) and ho is not None}
    return out


def _gt(v: float, thr: Any) -> bool:
    return bool(math.isfinite(v) and v > float(thr))


def _ge(v: float, thr: Any) -> bool:
    return bool(math.isfinite(v) and v >= float(thr))


def to_json(obj: Any) -> str:
    def conv(x: Any) -> Any:
        if isinstance(x, float) and not math.isfinite(x):
            return None
        if isinstance(x, np.generic):
            return conv(x.item())
        if isinstance(x, dict):
            return {str(k): conv(v) for k, v in x.items()}
        if isinstance(x, list | tuple):
            return [conv(v) for v in x]
        return x

    return json.dumps(conv(obj), indent=1, default=str)
