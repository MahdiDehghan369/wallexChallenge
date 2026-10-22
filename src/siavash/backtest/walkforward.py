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

import math
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
from siavash.signals.fee_filter import min_expected_move_pct

log = get_logger(__name__)

Z_ENTRY_GRID = (1.5, 2.0, 2.5, 3.0)
SL_ATR_GRID = (1.0, 1.5, 2.0)


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


async def _engine(
    cfg: Any,
    frames: Any,
    start: datetime,
    end: datetime,
    out: Path,
    run_id: str,
    universe: list[str] | None,
    llm_mode: str = "off",
) -> dict[str, Any]:
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
) -> pd.DataFrame:
    rows, s, k = [], start, 0
    while s + timedelta(days=window_days) <= end and (max_windows is None or k < max_windows):
        e = s + timedelta(days=window_days)
        rows.append({"window": k, **await _engine(cfg, frames, s, e, out, f"wf_window_{k}", universe)})
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
        rows.append({"variant": name, **await _engine(c, frames, start, end, out, rid, universe, llm)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------- 2. sensitivity (candidate level)


def rule_filter(ds: pd.DataFrame, cfg: Any) -> pd.DataFrame:
    """Apply what the live pipeline applies before ML: regime → enabled strategies, then the fee filter."""
    if ds.empty:
        return ds
    allowed = cfg.strategy.regime.enabled_strategies
    keep = ds.apply(lambda r: r["strategy"] in allowed.get(r["regime"], []), axis=1)
    return ds[keep & (ds["expected_move_pct"] >= min_expected_move_pct(cfg))]


def candidate_metrics(ds: pd.DataFrame, start: datetime, end: datetime) -> dict[str, Any]:
    days = max((end - start).total_seconds() / 86400, 1e-9)
    t = ds[(ds["ts"] >= pd.Timestamp(start)) & (ds["ts"] < pd.Timestamp(end))]
    return evaluate(t, days)


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
            ds = rule_filter(build_dataset(frames, c, universe), c)
            rows.append({"z_entry": z, "sl_atr": sl, **candidate_metrics(ds, start, end)})
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
        ds[["ts", "symbol", "strategy", "regime", "expected_move_pct"]], on=["ts", "symbol", "strategy"]
    )
    oof["p_cal"] = cross_calibrated(oof)
    base = rule_filter(oof, cfg)
    s = max(pd.Timestamp(start), oof["ts"].min())
    rows = [{"theta": "no ML", **candidate_metrics(base, s, end)}]
    for th in (round(theta - 0.05, 3), theta, round(theta + 0.05, 3)):
        rows.append({"theta": th, **candidate_metrics(base[base["p_cal"] >= th], s, end)})
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
        better = best_t["score"] > no_ml["score"] and best_t["cum_ret_pct"] > 0
        out.append(
            f"- ML: best θ={best_t['theta']} → cum {best_t['cum_ret_pct']:.2f} % vs {no_ml['cum_ret_pct']:.2f} % "
            f"without ML → {'consider enabling ML' if better else 'keep `ml.enabled: false`'}."
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
    heatmap(rep.sensitivity, "max_dd_pct", out / "sensitivity_dd.png", "max drawdown % (candidates)")
    s, e = rep.period
    fees = cfg.risk.fees
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
        "Same signals, regime filter, fee filter and triple-barrier outcome as live, but no portfolio limits; "
        "returns are summed per-trade net returns (%).",
        "",
        "![return](sensitivity_return.png) ![drawdown](sensitivity_dd.png)",
        "",
        *_md_table(
            rep.sensitivity,
            [
                "z_entry",
                "sl_atr",
                "n_trades",
                "trades_per_day",
                "cum_ret_pct",
                "mean_ret_pct",
                "max_dd_pct",
                "score",
            ],
        ),
        "### ML threshold",
        "",
        *_md_table(
            rep.theta_table,
            ["theta", "n_trades", "trades_per_day", "cum_ret_pct", "mean_ret_pct", "max_dd_pct", "score"],
        ),
        "## 3. Version comparison (engine, same period)",
        "",
        *_md_table(
            rep.comparison,
            ["variant", "net_return_pct", "max_drawdown_pct", "n_trades", "candidates", "placed", "fees_tmn"],
        ),
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
) -> WalkForwardReport:
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
        cfg, frames, start, data_end, window_days, out / "runs", universe, 1 if quick else None
    )
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
        cfg, frames, start, cmp_end, out / "runs", universe, model_version, models_root
    )
    if not model_version:
        notes.append("no trained model given: version comparison shows rule-only only")
    return WalkForwardReport(
        windows, sens, theta, comparison, benchmarks(frames, start, data_end), (start, data_end), notes
    )
