"""Walk-forward backtests, parameter sensitivity and version comparison (T-904).

1. **Windows** — the full backtest engine (same Orchestrator as live) on consecutive ``window_days``
   windows; return / drawdown / trades per window.
2. **Sensitivity** — ``s3.z_entry × s1.sl_atr`` and ``ml.threshold ∈ {θ−0.05, θ, θ+0.05}``. Running the
   full engine for every cell is too slow, so this uses the **candidate-level simulation**: the same
   signal code, regime filter (``strategy.regime.enabled_strategies``), fee filter and triple-barrier
   outcome (T-401) on every closed 15m candle, without portfolio limits (concurrency, cash).
   Heatmaps ``sensitivity_*.png``.
3. **Comparison** — rule-only vs +ML vs +ML+LLM (cached) with the full engine on the same period.
4. **Benchmarks** — buy & hold BTC/TMN and staying in cash.

The agent never changes config; the report ends with a "Recommendation" section for the human.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from siavash.backtest.engine import run_backtest_async
from siavash.backtest.market_data import HistoricalMarketData
from siavash.core.types import Timeframe
from siavash.logging import get_logger
from siavash.ml.dataset import build_dataset
from siavash.ml.threshold import cross_calibrated, evaluate
from siavash.ml.train import walk_forward
from siavash.reporting.metrics import metrics_from_logs
from siavash.signals.fee_filter import min_expected_move_pct

log = get_logger(__name__)

Z_ENTRY_GRID = (1.5, 2.0, 2.5, 3.0)
SL_ATR_GRID = (1.0, 1.5, 2.0)
SENS_STRATEGIES = ("S1", "S2", "S3")
PARAM_STRATEGY = {"z_entry": "S3", "sl_atr": "S1"}  # grid parameter -> the only strategy it changes
HOLDOUT_SUMMARY = "reports/diagnostics/SUMMARY.md"


def cfg_set(cfg: Any, path: str, value: Any) -> Any:
    """Copy of a (frozen) pydantic config with the dotted ``path`` replaced, e.g. ``strategy.s3.z_entry``."""
    head, _, rest = path.partition(".")
    if not rest:
        return cfg.model_copy(update={head: value})
    return cfg.model_copy(update={head: cfg_set(getattr(cfg, head), rest, value)})


def with_fees(cfg: Any, fee_pct: float | None) -> Any:
    """What-if: maker = taker = ``fee_pct`` (never written to config)."""
    if fee_pct is None:
        return cfg
    return cfg_set(cfg_set(cfg, "risk.fees.maker_pct", fee_pct), "risk.fees.taker_pct", fee_pct)


@dataclass
class WalkForwardReport:
    windows: pd.DataFrame
    sensitivity: pd.DataFrame
    theta_table: pd.DataFrame
    comparison: pd.DataFrame
    benchmarks: dict[str, float]
    period: tuple[datetime, datetime]
    notes: list[str] = field(default_factory=list)
    model_range: tuple[datetime, datetime] | None = None  # training data range of the +ML model


def model_data_range(
    cfg: Any, model_version: str | None, models_root: str | None
) -> tuple[datetime, datetime] | None:
    """``data_range`` from ``models/<version>/metadata.json`` (None when unknown)."""
    if not model_version:
        return None
    path = Path(models_root or cfg.base.paths.models) / model_version / "metadata.json"
    try:
        r = json.loads(path.read_text(encoding="utf-8"))["data_range"]
        return pd.Timestamp(r["start"]).to_pydatetime(), pd.Timestamp(r["end"]).to_pydatetime()
    except (OSError, KeyError, ValueError):
        return None


def in_sample(rep: WalkForwardReport) -> bool:
    """The +ML model was trained on data that overlaps the evaluated period."""
    if rep.model_range is None:
        return False
    s, e = rep.period
    return rep.model_range[0] < e and rep.model_range[1] > s


def md_factory(frames: dict[str, dict[Timeframe, pd.DataFrame]], cfg: Any) -> Any:
    def make(clock: Any) -> HistoricalMarketData:
        return HistoricalMarketData(frames, clock, spread_pct_fn=cfg.base.bt.spread_for)

    return make


def data_range(frames: dict[str, dict[Timeframe, pd.DataFrame]]) -> tuple[datetime, datetime]:
    m15 = [fr[Timeframe.M15] for fr in frames.values() if Timeframe.M15 in fr and len(fr[Timeframe.M15])]
    start = min(df["ts_open"].min() for df in m15)
    end = max(df["ts_open"].max() for df in m15) + timedelta(minutes=15)
    return pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime()


# ---------------------------------------------------------------------------- 1. windows / 3. comparison


def llm_status_counts(logs: Path) -> dict[str, int]:
    """Analyst (``REGIME_UPDATE``) and reviewer (``llm_review``) statuses of one run, plus LLM cache rows."""
    out: dict[str, int] = {}
    path = logs / "decisions.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                d = json.loads(line)
                if d.get("type") == "REGIME_UPDATE" and d.get("regime"):
                    k = f"analyst_{d['regime'].get('status') or 'none'}"
                    out[k] = out.get(k, 0) + 1
                if d.get("llm_review"):
                    k = f"reviewer_{d['llm_review'].get('status') or 'none'}"
                    out[k] = out.get(k, 0) + 1
    db = logs / "llm" / "cache.sqlite"
    if db.exists():
        con = sqlite3.connect(db)
        try:
            tables = [t[0] for t in con.execute("select name from sqlite_master where type='table'")]
            out["cache_rows"] = sum(con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables)
        finally:
            con.close()
    return out


async def _engine(
    cfg: Any,
    frames: Any,
    start: datetime,
    end: datetime,
    out: Path,
    run_id: str,
    universe: list[str] | None,
    llm_mode: str = "off",
    reuse: bool = False,
) -> dict[str, Any]:
    """One engine run; ``reuse`` reads the metrics of an existing ``out/run_id/logs`` instead of re-running."""
    logs = out / run_id / "logs"
    if reuse and (logs / "decisions.jsonl").exists():
        m, _ = metrics_from_logs(logs, float(cfg.risk.capital_base_tmn))
    else:
        res = await run_backtest_async(
            cfg,
            md_factory(frames, cfg),
            start=start,
            end=end,
            llm_mode=llm_mode,
            out_root=out,
            universe=universe,
            data_source="walkforward",
            run_id=run_id,
        )
        m = res.metrics
    return {
        "start": start,
        "end": end,
        "net_return_pct": m["net_return_pct"],
        "max_drawdown_pct": m["max_drawdown_pct"],
        "n_trades": m["n_trades"],
        "win_rate": m.get("win_rate"),
        "fees_tmn": m["total_fees_tmn"],
        "candidates": m["pipeline"].get("candidates", 0),
        "placed": m["pipeline"].get("placed", 0),
        "trades_per_day": round(m["n_trades"] / max((end - start).total_seconds() / 86400, 1e-9), 3),
        **llm_status_counts(logs),
    }


async def window_backtests(
    cfg: Any,
    frames: Any,
    start: datetime,
    end: datetime,
    window_days: float,
    out: Path,
    universe: list[str] | None,
    max_windows: int | None = None,
    reuse: bool = False,
) -> pd.DataFrame:
    rows, s, k = [], start, 0
    while s + timedelta(days=window_days) <= end and (max_windows is None or k < max_windows):
        e = s + timedelta(days=window_days)
        rows.append(
            {"window": k, **await _engine(cfg, frames, s, e, out, f"wf_window_{k}", universe, reuse=reuse)}
        )
        s, k = e, k + 1
    return pd.DataFrame(rows)


async def compare_variants(
    cfg: Any,
    frames: Any,
    start: datetime,
    end: datetime,
    out: Path,
    universe: list[str] | None,
    model_version: str | None,
    models_root: str | None,
    reuse: bool = False,
) -> pd.DataFrame:
    rule = cfg_set(cfg, "strategy.ml.enabled", False)
    variants: list[tuple[str, Any, str]] = [("rule-only", rule, "off")]
    if model_version:
        ml = cfg_set(cfg_set(cfg, "strategy.ml.enabled", True), "strategy.ml.model_version", model_version)
        if models_root:
            ml = cfg_set(ml, "base.paths.models", models_root)
        variants += [("+ML", ml, "off"), ("+ML+LLM (cached)", ml, "cached")]
    rows = []
    for name, c, llm in variants:
        rid = "wf_cmp_" + name.replace("+", "p").replace(" ", "_").replace("(", "").replace(")", "")
        rows.append({"variant": name, **await _engine(c, frames, start, end, out, rid, universe, llm, reuse)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- 2. sensitivity (candidate level)


def regime_filter(ds: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    if ds.empty:
        return ds
    allowed = cfg.strategy.regime.enabled_strategies
    return ds[ds.apply(lambda r: r["strategy"] in allowed.get(r["regime"], []), axis=1)]


def rule_filter(ds: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    """Apply what the live pipeline applies before ML: regime → enabled strategies, then the fee filter."""
    ds = regime_filter(ds, cfg)
    return ds if ds.empty else ds[ds["expected_move_pct"] >= min_expected_move_pct(cfg)]


def equity_drawdown(t: pd.DataFrame, fraction: float) -> float:
    """Max drawdown (%) of a compounded equity curve: each trade books ``fraction`` × its return at exit.

    Exit time = ``ts + bars_held × 15m`` when ``bars_held`` is known. Still no concurrency / cash limit.
    """
    if t.empty:
        return 0.0
    exit_ts = (
        t["ts"] + pd.to_timedelta(t["bars_held"].fillna(0) * 15, unit="min") if "bars_held" in t else t["ts"]
    )
    r = t.assign(_x=exit_ts).sort_values("_x")["ret_pct"].astype(float).to_numpy()
    eq = np.concatenate([[1.0], np.cumprod(1.0 + fraction * r / 100.0)])
    return round(float((1.0 - eq / np.maximum.accumulate(eq)).max() * 100), 4)


def candidate_metrics(ds: pd.DataFrame, start: datetime, end: datetime, cfg: Any) -> dict[str, Any]:
    """:func:`evaluate` with its summed-return drawdown renamed ``sum_dd_pct`` plus ``equity_dd_pct``."""
    days = max((end - start).total_seconds() / 86400, 1e-9)
    t = ds[(ds["ts"] >= pd.Timestamp(start)) & (ds["ts"] < pd.Timestamp(end))]
    m = evaluate(t, days)
    m["sum_dd_pct"] = m.pop("max_dd_pct")
    m["equity_dd_pct"] = equity_drawdown(t, float(cfg.risk.max_position_pct) / 100.0)
    return m


def _short(strategy: str) -> str:
    return strategy.split("_", 1)[0]


def strategy_counts(ds: pd.DataFrame, prefix: str = "n_") -> dict[str, int]:
    counts = ds["strategy"].map(_short).value_counts() if not ds.empty else pd.Series(dtype=int)
    return {f"{prefix}{s}": int(counts.get(s, 0)) for s in SENS_STRATEGIES}


def inert_params(row: dict[str, Any]) -> str:
    """Grid parameters that cannot change any trade in this cell (their strategy has 0 trades after filters)."""
    dead = [p for p, s in PARAM_STRATEGY.items() if row.get(f"n_{s}", 0) == 0]
    return ", ".join(dead) if dead else "—"


def structural_notes(cfg: Any) -> list[str]:
    """Parameters the fee filter makes irrelevant by construction, independent of the data."""
    need = min_expected_move_pct(cfg)
    s3 = cfg.strategy.s3
    out = []
    if float(s3.tp_pct) < need:
        out.append(
            f"**S3 can never pass the fee filter**: its take-profit is capped at `s3.tp_pct` = {s3.tp_pct} %, "
            f"below the required expected move of {need:.2f} % (fee_multiple {cfg.strategy.fee_multiple} × "
            f"round trip {need / float(cfg.strategy.fee_multiple):.2f} %). Every S3 candidate is removed, so "
            "`s3.z_entry` cannot affect any trade at these fees."
        )
    return out


def sensitivity_grid(
    cfg: Any,
    frames: Any,
    start: datetime,
    end: datetime,
    z_grid: tuple[float, ...] = Z_ENTRY_GRID,
    sl_grid: tuple[float, ...] = SL_ATR_GRID,
    universe: list[str] | None = None,
) -> pd.DataFrame:
    rows = []
    for z in z_grid:
        for sl in sl_grid:
            c = cfg_set(cfg_set(cfg, "strategy.s3.z_entry", z), "strategy.s1.sl_atr", sl)
            raw = build_dataset(frames, c, universe)
            raw = (
                raw[(raw["ts"] >= pd.Timestamp(start)) & (raw["ts"] < pd.Timestamp(end))] if len(raw) else raw
            )
            pre_fee = regime_filter(raw, c)
            ds = rule_filter(raw, c)
            row = {
                "z_entry": z,
                "sl_atr": sl,
                **candidate_metrics(ds, start, end, c),
                **strategy_counts(ds),
                **strategy_counts(pre_fee, "pre_fee_"),
            }
            row["inert"] = inert_params(row)
            rows.append(row)
    return pd.DataFrame(rows)


def theta_sensitivity(
    cfg: Any, frames: Any, start: datetime, end: datetime, universe: list[str] | None = None
) -> pd.DataFrame:
    """Walk-forward OOF probabilities (calibrated leave-one-fold-out) filtered at θ−0.05, θ, θ+0.05."""
    theta = float(cfg.strategy.ml.threshold)
    ds = build_dataset(frames, cfg, universe)
    wf = walk_forward(ds, cfg)
    if wf.oof.empty:
        return pd.DataFrame()
    oof = wf.oof.merge(
        ds[["ts", "symbol", "strategy", "regime", "expected_move_pct", "bars_held"]],
        on=["ts", "symbol", "strategy"],
    )
    oof["p_cal"] = cross_calibrated(oof)
    base = rule_filter(oof, cfg)
    s = max(pd.Timestamp(start), oof["ts"].min())
    rows = [{"theta": "no ML", **candidate_metrics(base, s, end, cfg)}]
    for th in (round(theta - 0.05, 3), theta, round(theta + 0.05, 3)):
        rows.append({"theta": th, **candidate_metrics(base[base["p_cal"] >= th], s, end, cfg)})
    return pd.DataFrame(rows)


def heatmap(df: pd.DataFrame, value: str, path: Path, title: str) -> None:
    if df.empty:
        return
    piv = df.pivot(index="z_entry", columns="sl_atr", values=value).astype(float)
    fig, ax = plt.subplots(figsize=(5.5, 4), dpi=110)
    finite = piv.to_numpy()[np.isfinite(piv.to_numpy())]
    lim = float(np.abs(finite).max()) if finite.size else 1.0
    im = ax.imshow(piv.to_numpy(), cmap="RdYlGn", vmin=-lim or -1, vmax=lim or 1, aspect="auto")
    ax.set_xticks(range(len(piv.columns)), [str(c) for c in piv.columns])
    ax.set_yticks(range(len(piv.index)), [str(i) for i in piv.index])
    ax.set_xlabel("s1.sl_atr")
    ax.set_ylabel("s3.z_entry")
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.iat[i, j]
            ax.text(j, i, "∞" if math.isinf(v) else f"{v:.2f}", ha="center", va="center", fontsize=8)
    ax.set_title(title)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------- 4. benchmarks


def benchmarks(frames: Any, start: datetime, end: datetime, btc: str = "BTC/TMN") -> dict[str, float]:
    out = {"cash": 0.0}
    if btc in frames:
        df = frames[btc][Timeframe.M15].dropna(subset=["close"])
        w = df[(df["ts_open"] >= pd.Timestamp(start)) & (df["ts_open"] < pd.Timestamp(end))]
        if len(w) >= 2:
            close = w["close"].astype(float)
            out["buy_hold_btc_return_pct"] = round(float(close.iloc[-1] / close.iloc[0] - 1) * 100, 4)
            out["buy_hold_btc_max_dd_pct"] = round(
                float(((close.cummax() - close) / close.cummax()).max() * 100), 4
            )
    return out


# ---------------------------------------------------------------------------- report


def _f(v: Any, nd: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float) and math.isinf(v):
        return "∞" if v > 0 else "−∞"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _md_table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    if df.empty:
        return ["_no data_", ""]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        d = r._asdict()
        cells = []
        for c in cols:
            v = d.get(c)
            cells.append(f"{pd.Timestamp(v):%Y-%m-%d}" if isinstance(v, datetime | pd.Timestamp) else _f(v))
        lines.append("| " + " | ".join(cells) + " |")
    return [*lines, ""]


def recommendation(rep: WalkForwardReport, cfg: Any) -> list[str]:
    out: list[str] = []
    s = rep.sensitivity
    if not s.empty:
        base = s[(s["z_entry"] == cfg.strategy.s3.z_entry) & (s["sl_atr"] == cfg.strategy.s1.sl_atr)]
        best = s.sort_values(["score", "cum_ret_pct"], ascending=False).iloc[0]
        positive = int((s["cum_ret_pct"] > 0).sum())
        out.append(
            f"- sensitivity: {positive}/{len(s)} parameter cells are net-profitable at candidate level; best cell "
            f"z_entry={best['z_entry']}, sl_atr={best['sl_atr']} (cum {best['cum_ret_pct']:.2f} %, score {_f(best['score'])})."
        )
        if not base.empty:
            b = base.iloc[0]
            out.append(
                f"- current config (z_entry={cfg.strategy.s3.z_entry}, sl_atr={cfg.strategy.s1.sl_atr}): "
                f"cum {b['cum_ret_pct']:.2f} %, {b['n_trades']} candidates."
            )
        inert = sorted({p for v in s["inert"] if v != "—" for p in v.split(", ")}) if "inert" in s else []
        for p in inert:
            n_dead = int(s["inert"].str.contains(p).sum())
            out.append(
                f"- `{p}` cannot affect any trade in {n_dead}/{len(s)} cells (its strategy "
                f"{PARAM_STRATEGY[p]} has 0 trades after the filters): identical rows along `{p}` are not "
                "evidence of robustness."
            )
        if positive == 0:
            out.append(
                "- **no parameter set is profitable after costs** — do not expect tuning to fix this; "
                "the cost assumptions (fees/spread) dominate. Re-run after the real fees are known."
            )
        elif positive < len(s) / 2:
            out.append(
                "- profitability is **not stable** across neighbouring parameters: prefer the current "
                "defaults unless a whole neighbourhood is consistently positive."
            )
    t = rep.theta_table
    if not t.empty and len(t) > 1:
        no_ml = t.iloc[0]
        best_t = t.iloc[1:].sort_values("score", ascending=False).iloc[0]
        why = (
            "θ and the model were fit on this same period, so this is not evidence for enabling ML"
            if in_sample(rep)
            else "θ comes from config; check that it was not selected on this period"
        )
        out.append(
            f"- ML{' (**IN-SAMPLE**)' if in_sample(rep) else ''}: best θ={best_t['theta']} → cum "
            f"{best_t['cum_ret_pct']:.2f} % vs {no_ml['cum_ret_pct']:.2f} % without ML. {why}. Decision source: "
            f"the hold-out in `{HOLDOUT_SUMMARY}` (keep `ml.enabled: false`)."
        )
    min_tpd = float(cfg.strategy.ml.min_trades_per_day)
    c = rep.comparison
    if not c.empty and "trades_per_day" in c:
        for r in c.itertuples(index=False):
            if r.trades_per_day < min_tpd:
                days = (rep.period[1] - rep.period[0]).total_seconds() / 86400
                out.append(
                    f"- activity: `{r.variant}` made {r.n_trades} trade(s) in {days:.0f} days "
                    f'({r.trades_per_day:.2f}/day < {min_tpd:g}/day) → **fails the "regular activity" '
                    "criterion** (plan: regular trading every 3 days, ~4–12 trades/day)."
                )
    if not rep.windows.empty:
        neg = int((rep.windows["net_return_pct"] < 0).sum())
        out.append(
            f"- windows: {neg}/{len(rep.windows)} engine windows lost money; "
            f"mean {rep.windows['net_return_pct'].mean():.3f} % per window."
        )
    bh = rep.benchmarks.get("buy_hold_btc_return_pct")
    if bh is not None:
        out.append(f"- benchmark: buy & hold BTC/TMN {bh:.2f} % over the period; cash 0 %.")
    return out or ["- not enough data for a recommendation."]


def write_walkforward_report(rep: WalkForwardReport, cfg: Any, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    heatmap(rep.sensitivity, "cum_ret_pct", out / "sensitivity_return.png", "cum net return % (candidates)")
    frac = float(cfg.risk.max_position_pct)
    heatmap(
        rep.sensitivity, "equity_dd_pct", out / "sensitivity_dd.png", f"equity max DD % ({frac:g} %/trade)"
    )
    s, e = rep.period
    fees = cfg.risk.fees
    ins = in_sample(rep)
    ml_tag = " — IN-SAMPLE" if ins else ""
    mr = rep.model_range
    ml_note = (
        [
            f"**IN-SAMPLE.** The model (trained on {mr[0]:%Y-%m-%d} → {mr[1]:%Y-%m-%d}) and "
            f"`ml.threshold` = {cfg.strategy.ml.threshold} (T-404) were fit on data that contains this whole "
            "period. The θ rows use walk-forward out-of-fold probabilities, but θ itself was chosen on them. "
            f"Do not use this table to decide on ML; the decision source is the hold-out in `{HOLDOUT_SUMMARY}`.",
            "",
        ]
        if ins and mr
        else []
    )
    llm_cols = [
        c for c in rep.comparison.columns if c.startswith(("analyst_", "reviewer_")) or c == "cache_rows"
    ]
    llm_lines: list[str] = []
    if llm_cols:
        llm_lines = [
            "LLM status counts per variant (`analyst_*` = REGIME_UPDATE records, `reviewer_*` = reviewed "
            "candidates, `cache_rows` = entries in the run's LLM cache; `ok` = an LLM answer was used):",
            "",
            *_md_table(
                rep.comparison.fillna({c: 0 for c in llm_cols}).astype({c: int for c in llm_cols}),
                ["variant", *sorted(llm_cols)],
            ),
        ]
        cached = rep.comparison[rep.comparison["variant"].str.contains("cached")]
        for r in cached.to_dict("records"):
            ok = int(r.get("analyst_ok", 0) or 0) + int(r.get("reviewer_ok", 0) or 0)
            if ok == 0:
                llm_lines += [
                    f"**`{r['variant']}` used no LLM answer**: the cache was "
                    f"{'empty' if not r.get('cache_rows') else 'never hit'} (cached mode never calls the "
                    "network, and each engine run opens its own new cache at `runs/<run_id>/logs/llm/cache.sqlite`, "
                    "so it starts empty unless a cache is copied in), so every analyst / reviewer call fell back "
                    "to the deterministic rules. It is "
                    "the same strategy as `+ML`; any difference is only the rule-based regime fallback, not the LLM.",
                    "",
                ]
    sens_cols = [
        "z_entry",
        "sl_atr",
        "n_trades",
        *[f"n_{x}" for x in SENS_STRATEGIES],
        "pre_fee_S3",
        "trades_per_day",
        "cum_ret_pct",
        "mean_ret_pct",
        "sum_dd_pct",
        "equity_dd_pct",
        "score",
        "inert",
    ]
    lines = [
        "# Walk-forward, sensitivity and version comparison (T-904)",
        "",
        f"- period: {s:%Y-%m-%d %H:%M} → {e:%Y-%m-%d %H:%M} UTC · fees maker/taker {fees.maker_pct}/{fees.taker_pct} % "
        f"· fee filter needs expected move ≥ {min_expected_move_pct(cfg):.2f} %",
        *[f"- {n}" for n in rep.notes],
        "",
        "## 1. Engine windows",
        "",
        *_md_table(
            rep.windows,
            [
                "window",
                "start",
                "end",
                "net_return_pct",
                "max_drawdown_pct",
                "n_trades",
                "candidates",
                "placed",
                "fees_tmn",
            ],
        ),
        "## 2. Sensitivity (candidate-level simulation)",
        "",
        "Same signals, regime filter, fee filter and triple-barrier outcome as live, but no portfolio limits "
        "(overlapping trades allowed). `cum_ret_pct` = sum of per-trade net returns (%).",
        "",
        "- `n_S1/n_S2/n_S3`: trades per strategy after the regime and fee filters; `pre_fee_S3`: S3 candidates "
        "before the fee filter. `s3.z_entry` only changes S3 and `s1.sl_atr` only changes S1.",
        "- `inert`: grid parameters that cannot affect any trade in that cell (their strategy has 0 trades).",
        "- `sum_dd_pct`: peak-to-trough of the *summed* per-trade returns, as if every trade used 100 % of "
        "capital with no position limit. **It is not an account drawdown** and is shown only for continuity "
        "with the score.",
        f"- `equity_dd_pct`: drawdown of a compounded equity curve where each trade uses `max_position_pct` = "
        f"{frac:g} % of equity, booked at its exit time (still no concurrency limit). The heatmap shows this one. "
        "For real account drawdowns see the engine windows in section 1.",
        *[f"- {n}" for n in structural_notes(cfg)],
        "",
        "![return](sensitivity_return.png) ![drawdown](sensitivity_dd.png)",
        "",
        *_md_table(rep.sensitivity, [c for c in sens_cols if c in rep.sensitivity]),
        f"### ML threshold{ml_tag}",
        "",
        *ml_note,
        *_md_table(
            rep.theta_table,
            [
                c
                for c in [
                    "theta",
                    "n_trades",
                    "trades_per_day",
                    "cum_ret_pct",
                    "mean_ret_pct",
                    "sum_dd_pct",
                    "equity_dd_pct",
                    "score",
                ]
                if c in rep.theta_table
            ],
        ),
        "## 3. Version comparison (engine, same period)",
        "",
        *(
            [
                f"The `+ML` variants are **IN-SAMPLE**: the model and `ml.threshold` were fit on data "
                f"({mr[0]:%Y-%m-%d} → {mr[1]:%Y-%m-%d}) that covers this period. Not a decision source; "
                f"see `{HOLDOUT_SUMMARY}`.",
                "",
            ]
            if ins and mr
            else []
        ),
        *_md_table(
            rep.comparison,
            [
                "variant",
                "net_return_pct",
                "max_drawdown_pct",
                "n_trades",
                "trades_per_day",
                "candidates",
                "placed",
                "fees_tmn",
            ],
        ),
        *llm_lines,
        "## 4. Benchmarks",
        "",
        *[f"- {k}: {_f(v)}" for k, v in rep.benchmarks.items()],
        "",
        "## Recommendation (for the human — config is not changed by the agent)",
        "",
        *recommendation(rep, cfg),
        "",
    ]
    path = out / "REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def run_walkforward(
    cfg: Any,
    frames: dict[str, dict[Timeframe, pd.DataFrame]],
    out: Path,
    window_days: float = 7,
    quick: bool = False,
    universe: list[str] | None = None,
    model_version: str | None = None,
    models_root: str | None = None,
    warmup_days: float | None = None,
    reuse_runs: bool = False,
) -> WalkForwardReport:
    """``reuse_runs``: read engine metrics from existing ``out/runs/*/logs`` instead of re-running them."""
    data_start, data_end = data_range(frames)
    warm = cfg.base.bt.warmup_days if warmup_days is None else warmup_days
    start = data_start + timedelta(days=warm)
    if start >= data_end:
        raise ValueError(f"not enough data after {warm} warm-up days ({data_start} .. {data_end})")
    notes = []
    z_grid, sl_grid = (Z_ENTRY_GRID, SL_ATR_GRID) if not quick else ((1.5, 2.5), (1.0, 2.0))
    if quick:
        notes.append("--quick: 1 engine window, 2×2 sensitivity grid")
    windows = await window_backtests(
        cfg, frames, start, data_end, window_days, out / "runs", universe, 1 if quick else None, reuse_runs
    )
    if reuse_runs:
        notes.append("engine runs re-read from existing `runs/*/logs` (not re-simulated)")
    sens = sensitivity_grid(cfg, frames, start, data_end, z_grid, sl_grid, universe)
    theta = theta_sensitivity(cfg, frames, start, data_end, universe)
    if theta.empty:
        notes.append(
            f"ML threshold: no walk-forward fold fits the data (train_days={cfg.strategy.ml.train_days} + "
            f"embargo + test_days={cfg.strategy.ml.test_days} needed)"
        )
    notes.append("universe: " + (", ".join(universe) if universe else "all TMN markets in the data"))
    cmp_end = min(data_end, start + timedelta(days=window_days)) if quick else data_end
    comparison = await compare_variants(
        cfg, frames, start, cmp_end, out / "runs", universe, model_version, models_root, reuse_runs
    )
    if not model_version:
        notes.append("no trained model given: version comparison shows rule-only only")
    return WalkForwardReport(
        windows,
        sens,
        theta,
        comparison,
        benchmarks(frames, start, data_end),
        (start, data_end),
        notes,
        model_data_range(cfg, model_version, models_root),
    )
