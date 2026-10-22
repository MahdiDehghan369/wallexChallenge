"""Performance metrics (T-903): the same computation for backtest, shadow and live logs.

Round-trip trades are rebuilt from ``fills.csv`` grouped by ``position_id``; returns use the official
challenge formula ``(final_equity − capital_base) / capital_base``; risk metrics come from the
equity series (hourly returns for Sharpe / Sortino). Breakdowns per strategy / market / regime and
pipeline statistics (candidates and rejections per stage) come from ``decisions.jsonl``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 24 * 365


# ---------------------------------------------------------------------------- loaders


def load_fills(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(
            columns=[
                "ts",
                "symbol",
                "side",
                "price",
                "qty",
                "notional_tmn",
                "fee",
                "fee_asset",
                "position_id",
                "strategy",
            ]
        )
    df = pd.read_csv(p, dtype={"position_id": str, "decision_id": str, "strategy": str})
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for c in ("price", "qty", "notional_tmn", "fee"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load_equity(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame(columns=["ts", "equity_tmn"])
    df = pd.read_csv(p)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    for c in df.columns:
        if c != "ts":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("ts").reset_index(drop=True)


def load_decisions(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            regime = d.get("regime") or {}
            rows.append(
                {
                    "decision_id": d["decision_id"],
                    "ts": d["ts"],
                    "type": d["type"],
                    "symbol": d.get("symbol"),
                    "strategy": d.get("strategy"),
                    "outcome_action": d.get("outcome_action"),
                    "rejected_at": d.get("rejected_at"),
                    "reject_code": d.get("reject_code"),
                    "position_id": d.get("position_id"),
                    "exit_reason": d.get("exit_reason"),
                    "regime": regime.get("regime"),
                    "llm_status": (d.get("llm_review") or {}).get("status"),
                    "llm_verdict": (d.get("llm_review") or {}).get("verdict"),
                    "ml_status": (d.get("ml") or {}).get("status"),
                }
            )
    df = pd.DataFrame(
        rows,
        columns=[
            "decision_id",
            "ts",
            "type",
            "symbol",
            "strategy",
            "outcome_action",
            "rejected_at",
            "reject_code",
            "position_id",
            "exit_reason",
            "regime",
            "llm_status",
            "llm_verdict",
            "ml_status",
        ],
    )
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    return df


# ---------------------------------------------------------------------------- trades


def fee_in_tmn(row: pd.Series) -> float:
    fee = float(row["fee"])
    asset = str(row.get("fee_asset") or "TMN")
    if asset == "TMN":
        return fee
    return fee * float(row["price"]) if asset == str(row["symbol"]).split("/")[0] else fee


def build_trades(fills: pd.DataFrame, decisions: pd.DataFrame | None = None) -> pd.DataFrame:
    """Closed round trips per ``position_id`` with net PnL (all fees included)."""
    cols = [
        "position_id",
        "symbol",
        "strategy",
        "regime",
        "entry_ts",
        "exit_ts",
        "buy_tmn",
        "sell_tmn",
        "fees_tmn",
        "pnl_tmn",
        "ret_pct",
        "exit_reason",
        "hold_hours",
    ]
    if fills.empty:
        return pd.DataFrame(columns=cols)
    f = fills.dropna(subset=["position_id"]).copy()
    f = f[f["position_id"].astype(str).str.len() > 0]
    if f.empty:
        return pd.DataFrame(columns=cols)
    f["fee_tmn"] = f.apply(fee_in_tmn, axis=1)
    regime_by_pos: dict[str, Any] = {}
    exit_by_pos: dict[str, Any] = {}
    if decisions is not None and not decisions.empty:
        ent = decisions[(decisions["type"] == "ENTRY_CANDIDATE") & decisions["position_id"].notna()]
        regime_by_pos = dict(zip(ent["position_id"], ent["regime"], strict=False))
        ex = decisions[(decisions["type"] == "EXIT") & decisions["position_id"].notna()]
        exit_by_pos = dict(zip(ex["position_id"], ex["exit_reason"], strict=False))
    rows = []
    for pid, g in f.groupby("position_id"):
        buys, sells = g[g["side"] == "buy"], g[g["side"] == "sell"]
        bq, sq = buys["qty"].sum(), sells["qty"].sum()
        if bq <= 0 or sq <= 0 or sq < bq * 0.98:  # still open (or only partially closed)
            continue
        buy_tmn, sell_tmn, fees = buys["notional_tmn"].sum(), sells["notional_tmn"].sum(), g["fee_tmn"].sum()
        pnl = sell_tmn - buy_tmn - fees
        entry_ts, exit_ts = buys["ts"].min(), sells["ts"].max()
        rows.append(
            {
                "position_id": pid,
                "symbol": g["symbol"].iloc[0],
                "strategy": (g["strategy"].dropna().iloc[0] if g["strategy"].notna().any() else None),
                "regime": regime_by_pos.get(pid),
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "buy_tmn": buy_tmn,
                "sell_tmn": sell_tmn,
                "fees_tmn": fees,
                "pnl_tmn": pnl,
                "ret_pct": pnl / buy_tmn * 100 if buy_tmn else 0.0,
                "exit_reason": exit_by_pos.get(pid),
                "hold_hours": (exit_ts - entry_ts).total_seconds() / 3600,
            }
        )
    return (
        pd.DataFrame(rows, columns=cols).sort_values("exit_ts").reset_index(drop=True)
        if rows
        else pd.DataFrame(columns=cols)
    )


def _trade_stats(t: pd.DataFrame) -> dict[str, Any]:
    if t.empty:
        return {"n_trades": 0, "win_rate": None, "pnl_tmn": 0.0, "avg_ret_pct": None, "profit_factor": None}
    wins, losses = t[t["pnl_tmn"] > 0]["pnl_tmn"], t[t["pnl_tmn"] <= 0]["pnl_tmn"]
    gl = -losses.sum()
    return {
        "n_trades": len(t),
        "win_rate": round(len(wins) / len(t), 4),
        "pnl_tmn": round(float(t["pnl_tmn"].sum()), 2),
        "avg_ret_pct": round(float(t["ret_pct"].mean()), 4),
        "profit_factor": round(float(wins.sum() / gl), 4) if gl > 0 else (math.inf if len(wins) else None),
    }


def _drawdown(eq: pd.Series, ts: pd.Series) -> tuple[float, float]:
    """Max drawdown % from running peak and its longest duration in hours."""
    if eq.empty:
        return 0.0, 0.0
    peak = eq.cummax()
    dd = (peak - eq) / peak * 100
    max_dd = float(dd.max())
    longest, start = 0.0, None
    for t, v in zip(ts, dd, strict=True):
        if v > 0 and start is None:
            start = t
        elif v == 0 and start is not None:
            longest = max(longest, (t - start).total_seconds() / 3600)
            start = None
    if start is not None:
        longest = max(longest, (ts.iloc[-1] - start).total_seconds() / 3600)
    return max_dd, longest


def _ratios(equity: pd.DataFrame) -> dict[str, float | None]:
    if len(equity) < 3:
        return {"sharpe_hourly": None, "sortino_hourly": None, "sharpe_annual": None, "sortino_annual": None}
    hourly = equity.set_index("ts")["equity_tmn"].resample("1h").last().ffill()
    r = hourly.pct_change().dropna()
    if len(r) < 2 or r.std(ddof=0) == 0:
        return {"sharpe_hourly": None, "sortino_hourly": None, "sharpe_annual": None, "sortino_annual": None}
    sharpe = float(r.mean() / r.std(ddof=0))
    downside = r[r < 0]
    dstd = float(np.sqrt((downside**2).mean())) if len(downside) else 0.0
    sortino = float(r.mean() / dstd) if dstd > 0 else None
    k = math.sqrt(HOURS_PER_YEAR)
    return {
        "sharpe_hourly": round(sharpe, 6),
        "sortino_hourly": round(sortino, 6) if sortino is not None else None,
        "sharpe_annual": round(sharpe * k, 4),
        "sortino_annual": round(sortino * k, 4) if sortino is not None else None,
    }


def pipeline_stats(decisions: pd.DataFrame) -> dict[str, Any]:
    if decisions.empty:
        return {"candidates": 0, "placed": 0, "rejected_by_stage": {}, "llm_fallbacks": 0}
    c = decisions[decisions["type"] == "ENTRY_CANDIDATE"]
    rej = c[c["outcome_action"] == "REJECTED"]
    reviewed = c[c["llm_verdict"].notna()]
    return {
        "candidates": len(c),
        "placed": int((c["outcome_action"] == "ORDER_PLACED").sum()),
        "rejected_by_stage": {str(k): int(v) for k, v in rej["rejected_at"].value_counts().items()},
        "reject_codes": {str(k): int(v) for k, v in rej["reject_code"].value_counts().items()},
        "llm_reviewed": len(reviewed),
        "llm_approve_pct": round(float((reviewed["llm_verdict"] == "APPROVE").mean() * 100), 2)
        if len(reviewed)
        else None,
        "llm_fallbacks": int(c["llm_status"].isin(["fallback", "skipped"]).sum()),
    }


def compute_metrics(
    fills: pd.DataFrame, equity: pd.DataFrame, decisions: pd.DataFrame, capital_base: float = 5_000_000
) -> dict[str, Any]:
    """All headline metrics + breakdowns + pipeline statistics (JSON-serialisable)."""
    trades = build_trades(fills, decisions)
    final = (
        float(equity["equity_tmn"].iloc[-1])
        if not equity.empty
        else capital_base + float(trades["pnl_tmn"].sum() if not trades.empty else 0.0)
    )
    max_dd, dd_hours = _drawdown(equity["equity_tmn"], equity["ts"]) if not equity.empty else (0.0, 0.0)
    if not equity.empty:
        span_days = max((equity["ts"].iloc[-1] - equity["ts"].iloc[0]).total_seconds() / 86400, 1e-9)
    elif not trades.empty:
        span_days = max((trades["exit_ts"].max() - trades["entry_ts"].min()).total_seconds() / 86400, 1e-9)
    else:
        span_days = 0.0
    stats = _trade_stats(trades)
    stats["trades_pnl_tmn"] = stats.pop(
        "pnl_tmn"
    )  # sum of closed round trips (equity-based pnl_tmn stays top-level)
    pnl = trades["pnl_tmn"].astype(float) if not trades.empty else pd.Series(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    volume = float(fills["notional_tmn"].sum()) if not fills.empty else 0.0
    fees = float(fills.apply(fee_in_tmn, axis=1).sum()) if not fills.empty else 0.0
    exposure = None
    if not equity.empty and "exposure_tmn" in equity.columns:
        exposure = round(float((equity["exposure_tmn"] / equity["equity_tmn"]).mean() * 100), 3)

    def by(col: str) -> dict[str, Any]:
        if trades.empty:
            return {}
        return {str(k): _trade_stats(g) for k, g in trades.groupby(trades[col].fillna("unknown"))}

    return {
        "capital_base_tmn": capital_base,
        "final_equity_tmn": round(final, 2),
        "net_return_pct": round((final - capital_base) / capital_base * 100, 4),
        "pnl_tmn": round(final - capital_base, 2),
        "max_drawdown_pct": round(max_dd, 4),
        "max_drawdown_duration_h": round(dd_hours, 2),
        **_ratios(equity),
        **stats,
        "avg_win_tmn": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss_tmn": round(float(losses.mean()), 2) if len(losses) else None,
        "expectancy_tmn": round(float(trades["pnl_tmn"].mean()), 2) if not trades.empty else None,
        "trades_per_day": round(len(trades) / span_days, 3) if span_days else None,
        "n_fills": len(fills),
        "total_volume_tmn": round(volume, 2),
        "avg_trade_volume_tmn": round(volume / len(trades), 2) if len(trades) else None,
        "avg_exposure_pct": exposure,
        "total_fees_tmn": round(fees, 2),
        "days": round(span_days, 3),
        "by_strategy": by("strategy"),
        "by_symbol": by("symbol"),
        "by_regime": by("regime"),
        "pipeline": pipeline_stats(decisions),
    }


def metrics_from_logs(
    log_dir: str | Path, capital_base: float = 5_000_000
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Convenience: ``(metrics, trades)`` from a ``logs/`` directory."""
    d = Path(log_dir)
    fills, equity, decisions = (
        load_fills(d / "fills.csv"),
        load_equity(d / "equity.csv"),
        load_decisions(d / "decisions.jsonl"),
    )
    return compute_metrics(fills, equity, decisions, capital_base), build_trades(fills, decisions)
