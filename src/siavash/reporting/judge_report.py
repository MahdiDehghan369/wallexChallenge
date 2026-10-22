"""Judge metrics report (T-1201): ``siavash report --logs logs/ --out reports/live/``.

All T-903 metrics plus a "Judge indicators" section with exactly the titles the judges use:
Number of Trades, Trading Volume (TMN), Average Trade Volume, Net Return % (official formula on the
5,000,000 TMN capital base), Max Drawdown %, trades per challenge day, API error rate, latency p50/p95
and the role of AI (candidates, rejections by ML / LLM / Risk, APPROVE %, fallbacks). Equity and
drawdown charts.

API statistics come from the ``http_request`` events in ``logs/system.log*``; latency from
``logs/latency.jsonl``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.backtest.report import write_charts
from siavash.reporting import latency
from siavash.reporting.metrics import build_trades, compute_metrics, load_decisions, load_equity, load_fills

OFFICIAL_CAPITAL_TMN = 5_000_000


def api_stats(log_dir: Path) -> dict[str, Any]:
    ms: list[float] = []
    errors = total = 0
    for f in sorted(log_dir.glob("system.log*")):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"http_request"' not in line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") != "http_request":
                continue
            total += 1
            status = rec.get("status")
            if status is None or int(status) >= 400:
                errors += 1
            if rec.get("ms") is not None:
                ms.append(float(rec["ms"]))
    s = pd.Series(ms, dtype=float)
    return {
        "requests": total,
        "errors": errors,
        "error_rate_pct": round(errors / total * 100, 3) if total else None,
        "http_p50_ms": round(float(s.quantile(0.5)), 1) if len(s) else None,
        "http_p95_ms": round(float(s.quantile(0.95)), 1) if len(s) else None,
    }


def trades_per_day(fills: pd.DataFrame, trades: pd.DataFrame, tz: str) -> pd.DataFrame:
    """Per challenge day (Tehran): fills, closed round trips and traded volume."""
    cols = ["day", "fills", "closed_trades", "volume_tmn"]
    if fills.empty:
        return pd.DataFrame(columns=cols)
    f = fills.assign(day=fills["ts"].dt.tz_convert(tz).dt.date)
    out = f.groupby("day").agg(fills=("ts", "size"), volume_tmn=("notional_tmn", "sum"))
    if not trades.empty:
        closed = trades.assign(day=pd.to_datetime(trades["exit_ts"], utc=True).dt.tz_convert(tz).dt.date)
        out["closed_trades"] = closed.groupby("day").size()
    out["closed_trades"] = out.get("closed_trades", 0)
    out = out.fillna(0).reset_index()
    out["closed_trades"] = out["closed_trades"].astype(int)
    return out[cols]


def ai_stats(decisions: pd.DataFrame) -> dict[str, Any]:
    if decisions.empty:
        return {"candidates": 0}
    c = decisions[decisions["type"] == "ENTRY_CANDIDATE"]
    rej = c[c["outcome_action"] == "REJECTED"]["rejected_at"].value_counts()
    reviewed = c[c["llm_status"] == "ok"]
    return {
        "candidates": len(c),
        "orders_placed": int((c["outcome_action"] == "ORDER_PLACED").sum()),
        "rejected_by_fee_filter": int(rej.get("FEE_FILTER", 0)),
        "rejected_by_ml": int(rej.get("ML", 0) + rej.get("EV", 0)),
        "rejected_by_llm": int(rej.get("LLM", 0)),
        "rejected_by_risk": int(rej.get("RISK", 0)),
        "llm_reviewed": len(reviewed),
        "llm_approve_pct": round(float((reviewed["llm_verdict"] == "APPROVE").mean() * 100), 2)
        if len(reviewed)
        else None,
        "llm_fallbacks": int(c["llm_status"].isin(["fallback", "error"]).sum()),
        "ml_scored": int((c["ml_status"] == "ok").sum()),
    }


def _f(v: Any, nd: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float):
        return "∞" if math.isinf(v) else f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def build_judge_report(
    log_dir: str | Path,
    out_dir: str | Path,
    tz: str = "Asia/Tehran",
    mode: str = "live",
    frames: Any = None,
    cfg: Any = None,
) -> dict[str, Any]:
    """``frames`` (``{symbol: {tf: candles}}``) + ``cfg`` enable the AI counterfactual section (T-1203)."""
    logs, out = Path(log_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fills, equity, decisions = (
        load_fills(logs / "fills.csv"),
        load_equity(logs / "equity.csv"),
        load_decisions(logs / "decisions.jsonl"),
    )
    m = compute_metrics(fills, equity, decisions, OFFICIAL_CAPITAL_TMN)
    trades = build_trades(fills, decisions)
    per_day = trades_per_day(fills, trades, tz)
    api = api_stats(logs)
    lat = latency.summarize(latency.load(logs / "latency.jsonl"))
    ai = ai_stats(decisions)
    n_orders_trades = int(m["n_fills"])
    judge = {
        "Number of Trades": m["n_trades"],
        "Number of Fills": n_orders_trades,
        "Trading Volume (TMN)": m["total_volume_tmn"],
        "Average Trade Volume": m["avg_trade_volume_tmn"],
        "Net Return %": m["net_return_pct"],
        "Max Drawdown %": m["max_drawdown_pct"],
        "API error rate %": api["error_rate_pct"],
    }
    trades.to_csv(out / "trades.csv", index=False)
    per_day.to_csv(out / "trades_per_day.csv", index=False)
    payload = {
        "mode": mode,
        "judge": judge,
        "metrics": m,
        "api": api,
        "latency": lat,
        "ai": ai,
        "per_day": per_day.astype({"day": str}).to_dict(orient="records"),
    }
    (out / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    write_charts(equity, out, OFFICIAL_CAPITAL_TMN)

    lines = [
        f"# Siavash — judge report ({mode})",
        "",
        f"- period: {equity['ts'].min() if not equity.empty else '—'} → {equity['ts'].max() if not equity.empty else '—'}",
        f"- capital base: {OFFICIAL_CAPITAL_TMN:,} TMN · final equity: {_f(m['final_equity_tmn'])} TMN",
        "",
        "## Judge indicators",
        "",
        "| indicator | value |",
        "|---|---:|",
        f"| Number of Trades | {_f(m['n_trades'])} (closed round trips; {n_orders_trades} fills) |",
        f"| Trading Volume (TMN) | {_f(m['total_volume_tmn'])} |",
        f"| Average Trade Volume | {_f(m['avg_trade_volume_tmn'])} TMN |",
        f"| Net Return % | {_f(m['net_return_pct'], 4)} % = (final equity − 5,000,000) / 5,000,000 × 100 |",
        f"| Max Drawdown % | {_f(m['max_drawdown_pct'], 4)} % |",
        f"| API error rate | {_f(api['error_rate_pct'], 3)} % ({api['errors']} of {api['requests']} requests) |",
        f"| HTTP latency p50 / p95 | {_f(api['http_p50_ms'], 1)} / {_f(api['http_p95_ms'], 1)} ms |",
        "",
        "### Trades per challenge day (Tehran)",
        "",
        "| day | fills | closed trades | volume (TMN) |",
        "|---|---:|---:|---:|",
        *[
            f"| {r.day} | {r.fills} | {r.closed_trades} | {_f(float(r.volume_tmn))} |"
            for r in per_day.itertuples(index=False)
        ],
        "",
        "### Decision latency",
        "",
        "| stage | n | p50 ms | p95 ms | max ms |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {k} | {v['n']} | {_f(v['p50'], 1)} | {_f(v['p95'], 1)} | {_f(v['max'], 1)} |"
            for k, v in lat.items()
        ],
        "",
        "### Role of AI",
        "",
        "| measure | value |",
        "|---|---:|",
        *[f"| {k} | {_f(v)} |" for k, v in ai.items()],
        "",
        "## Performance",
        "",
        "| metric | value |",
        "|---|---:|",
        *[
            f"| {k} | {_f(m.get(k), 4)} |"
            for k in (
                "sharpe_annual",
                "sortino_annual",
                "win_rate",
                "profit_factor",
                "expectancy_tmn",
                "avg_win_tmn",
                "avg_loss_tmn",
                "trades_per_day",
                "total_fees_tmn",
                "avg_exposure_pct",
                "max_drawdown_duration_h",
            )
        ],
        "",
        "![equity](equity.png)",
        "",
        "![drawdown](drawdown.png)",
        "",
        "## Breakdown by strategy",
        "",
        "| strategy | trades | win rate | PnL (TMN) |",
        "|---|---:|---:|---:|",
        *[
            f"| {k} | {s['n_trades']} | {_f(s['win_rate'])} | {_f(s['pnl_tmn'])} |"
            for k, s in m["by_strategy"].items()
        ],
        "",
    ]
    if frames is not None and cfg is not None:
        from siavash.reporting.counterfactual import counterfactual_markdown, evaluate_rejections, summarize

        cf = summarize(evaluate_rejections(logs / "decisions.jsonl", frames, cfg))
        payload["counterfactual"] = cf
        lines.append(counterfactual_markdown(cf))
        (out / "metrics.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    return payload
