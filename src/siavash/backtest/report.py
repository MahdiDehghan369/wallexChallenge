"""Backtest report (T-905).

Writes into ``reports/backtest/{run_id}/``: ``REPORT.md``, ``equity.png``, ``drawdown.png``,
``trades.csv``, ``metrics.json``, ``config_used.yaml`` and ``runtime.json`` (git_sha, config_hash,
model_version, prompt_versions, data range, data hash). Charts are rendered headless (matplotlib Agg).
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml

from siavash.agents.prompts import load_manifest
from siavash.reporting.metrics import load_equity

REPORT_FILES = (
    "REPORT.md",
    "equity.png",
    "drawdown.png",
    "trades.csv",
    "metrics.json",
    "config_used.yaml",
    "runtime.json",
)


def data_hash(md: Any) -> str:
    """sha256 over every (symbol, timeframe, candle open times, closes) the backtest could see."""
    h = hashlib.sha256()
    for (sym, tf), s in sorted(md.series.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
        h.update(f"{sym}|{tf.value}|".encode())
        h.update(s.open_s.tobytes())
        h.update(s.closes.tobytes())
    return "sha256:" + h.hexdigest()


def _json_default(x: Any) -> Any:
    if isinstance(x, float) and (math.isinf(x) or math.isnan(x)):
        return str(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return str(x)


def _clean(obj: Any) -> Any:
    """inf/nan are not valid JSON: write them as strings."""
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    return obj


def _fmt(v: Any, nd: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return "∞" if math.isinf(v) else f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _table(rows: dict[str, dict[str, Any]], title: str) -> list[str]:
    out = [f"### {title}", ""]
    if not rows:
        return [*out, "_no closed trades_", ""]
    out += [
        "| key | trades | win rate | PnL (TMN) | avg ret % | profit factor |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for k, s in sorted(rows.items(), key=lambda kv: -(kv[1].get("pnl_tmn") or 0)):
        wr = s.get("win_rate")
        out.append(
            f"| {k} | {s['n_trades']} | {_fmt(wr * 100 if wr is not None else None, 1)}% | {_fmt(s.get('pnl_tmn'))} "
            f"| {_fmt(s.get('avg_ret_pct'), 3)} | {_fmt(s.get('profit_factor'))} |"
        )
    return [*out, ""]


def _charts(equity: pd.DataFrame, out: Path, capital: float) -> None:
    for name, ylabel in (("equity", "equity (TMN)"), ("drawdown", "drawdown (%)")):
        fig, ax = plt.subplots(figsize=(10, 4), dpi=110)
        if equity.empty:
            ax.text(0.5, 0.5, "no equity data", ha="center", va="center", transform=ax.transAxes)
        elif name == "equity":
            ax.plot(equity["ts"], equity["equity_tmn"], color="#2563eb", linewidth=1.2)
            ax.axhline(capital, color="#9ca3af", linewidth=0.8, linestyle="--")
        else:
            eq = equity["equity_tmn"].astype(float)
            dd = (eq.cummax() - eq) / eq.cummax() * 100
            ax.fill_between(equity["ts"], -dd, 0, color="#dc2626", alpha=0.35, linewidth=0)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(out / f"{name}.png")
        plt.close(fig)


def write_report(res: Any) -> Path:
    """Write every report file for a ``BacktestResult``; returns the path of ``REPORT.md``."""
    out = Path(res.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = res.extra.get("config")
    md = res.extra.get("md")
    m = res.metrics
    capital = float(m.get("capital_base_tmn", 0))

    trades: pd.DataFrame = res.trades
    trades.to_csv(out / "trades.csv", index=False)
    (out / "metrics.json").write_text(
        json.dumps(_clean(m), indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8"
    )
    if cfg is not None:
        (out / "config_used.yaml").write_text(
            yaml.safe_dump(cfg.public_dump(), allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    else:
        (out / "config_used.yaml").write_text("# config not available\n", encoding="utf-8")

    data_start, data_end = md.time_range() if md is not None else (None, None)
    runtime = {
        **(res.extra.get("runtime") or {}),
        "model_version": res.extra.get("model_version"),
        "prompt_versions": {k: v.get("sha256") for k, v in load_manifest().items()},
        "llm_mode": res.llm_mode,
        "data_source": res.data_source,
        "data_range": {"start": data_start, "end": data_end},
        "backtest_range": {"start": res.start, "end": res.end},
        "data_hash": data_hash(md) if md is not None else None,
        "universe": res.universe,
    }
    (out / "runtime.json").write_text(
        json.dumps(runtime, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8"
    )

    equity = load_equity(out / "logs" / "equity.csv")
    _charts(equity, out, capital)

    p = m.get("pipeline", {})
    wr = m.get("win_rate")
    lines = [
        f"# Backtest {res.run_id}",
        "",
        f"- period: **{res.start:%Y-%m-%d %H:%M} → {res.end:%Y-%m-%d %H:%M} UTC** ({_fmt(m.get('days'), 1)} days)",
        f"- data: `{res.data_source}` · universe: {', '.join(res.universe)}",
        f"- LLM mode: `{res.llm_mode}` · git `{runtime.get('git_sha')}` · config `{runtime.get('config_hash')}`",
        "",
        "## Headline",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| net return | {_fmt(m.get('net_return_pct'), 3)} % |",
        f"| PnL | {_fmt(m.get('pnl_tmn'))} TMN |",
        f"| final equity | {_fmt(m.get('final_equity_tmn'))} TMN |",
        f"| max drawdown | {_fmt(m.get('max_drawdown_pct'), 3)} % ({_fmt(m.get('max_drawdown_duration_h'), 1)} h) |",
        f"| Sharpe (annualised, hourly) | {_fmt(m.get('sharpe_annual'))} |",
        f"| Sortino (annualised, hourly) | {_fmt(m.get('sortino_annual'))} |",
        f"| closed trades | {_fmt(m.get('n_trades'))} ({_fmt(m.get('trades_per_day'), 2)}/day) |",
        f"| win rate | {_fmt(wr * 100 if wr is not None else None, 1)} % |",
        f"| profit factor | {_fmt(m.get('profit_factor'))} |",
        f"| expectancy | {_fmt(m.get('expectancy_tmn'))} TMN |",
        f"| fees | {_fmt(m.get('total_fees_tmn'))} TMN |",
        f"| volume | {_fmt(m.get('total_volume_tmn'))} TMN |",
        f"| avg exposure | {_fmt(m.get('avg_exposure_pct'), 2)} % |",
        "",
        "![equity](equity.png)",
        "",
        "![drawdown](drawdown.png)",
        "",
        "## Breakdown",
        "",
        *_table(m.get("by_strategy", {}), "By strategy"),
        *_table(m.get("by_symbol", {}), "By symbol"),
        *_table(m.get("by_regime", {}), "By regime"),
    ]
    if not trades.empty and "exit_reason" in trades.columns:
        reasons = trades["exit_reason"].fillna("unknown").value_counts()
        lines += ["### By exit reason", "", "| reason | trades |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in reasons.items()]
        lines.append("")
    lines += [
        "## Decision pipeline",
        "",
        f"- candidates: {p.get('candidates', 0)} · orders placed: {p.get('placed', 0)}",
        f"- LLM reviewed: {p.get('llm_reviewed', 0)} (approve {_fmt(p.get('llm_approve_pct'), 1)} %) · "
        f"fallback/skipped: {p.get('llm_fallbacks', 0)}",
        "",
        "| rejected at | count |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in (p.get("rejected_by_stage") or {}).items()],
        "",
        "| reject code | count |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in (p.get("reject_codes") or {}).items()],
        "",
        "## Reproducibility",
        "",
        f"- data range: {data_start} → {data_end}; data hash `{runtime['data_hash']}`",
        f"- ML model: `{runtime['model_version']}` · prompts: "
        + ", ".join(f"`{k}`" for k in runtime["prompt_versions"]),
        "- files: " + ", ".join(f"`{f}`" for f in REPORT_FILES if f != "REPORT.md") + ", `logs/`",
        "",
    ]
    report = out / "REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report
