"""Counterfactual evaluation of the AI gates (T-1203).

For every ``ENTRY_CANDIDATE`` rejected at ``ML`` / ``EV`` / ``LLM`` the hypothetical outcome is computed
with the triple barrier (T-401) on the candles after the signal (recorded live data or processed
history): a rejected loser was a **correct rejection**, a rejected winner a **missed opportunity**.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.core.models import Candidate
from siavash.core.types import Timeframe
from siavash.ml.dataset import slippage_pct
from siavash.ml.labeling import label_candidate
from siavash.signals.fee_filter import round_trip_fee_pct

AI_STAGES = {"ML": "ML", "EV": "ML", "LLM": "LLM"}


def rejected_candidates(decisions_path: str | Path) -> list[tuple[str, Candidate]]:
    out: list[tuple[str, Candidate]] = []
    p = Path(decisions_path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        stage = d.get("rejected_at")
        if d.get("type") == "ENTRY_CANDIDATE" and stage in AI_STAGES and d.get("signal"):
            out.append((AI_STAGES[stage], Candidate.model_validate(d["signal"])))
    return out


def evaluate_rejections(
    decisions_path: str | Path, frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]], cfg: Any
) -> pd.DataFrame:
    """One row per AI rejection with the hypothetical outcome (rows without future data are skipped)."""
    fee = round_trip_fee_pct(cfg)
    rows = []
    for gate, c in rejected_candidates(decisions_path):
        m15 = frames.get(c.symbol, {}).get(Timeframe.M15)
        if m15 is None or m15.empty:
            continue
        future = m15[m15["ts_open"] >= pd.Timestamp(c.ts)].dropna(subset=["close"])
        need = -(-c.time_stop_minutes // 15)
        if len(future) < need:
            continue
        label, ret, reason, held = label_candidate(c, future.iloc[:need], fee, slippage_pct(cfg, c.symbol))
        rows.append(
            {
                "gate": gate,
                "ts": c.ts,
                "symbol": c.symbol,
                "strategy": c.strategy.value,
                "label": label,
                "ret_pct": ret,
                "exit_reason": reason,
                "bars_held": held,
            }
        )
    return pd.DataFrame(
        rows, columns=["gate", "ts", "symbol", "strategy", "label", "ret_pct", "exit_reason", "bars_held"]
    )


def summarize(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for gate in ("ML", "LLM"):
        g = df[df["gate"] == gate]
        losers, winners = g[g["ret_pct"] <= 0], g[g["ret_pct"] > 0]
        out[gate] = {
            "rejected": len(g),
            "correct_rejections": len(losers),
            "missed_opportunities": len(winners),
            "avoided_loss_pct": round(float(-losers["ret_pct"].sum()), 4),
            "missed_profit_pct": round(float(winners["ret_pct"].sum()), 4),
            "net_value_pct": round(float(-g["ret_pct"].sum()), 4),  # > 0: the gate saved money
        }
    return out


def one_liner(s: dict[str, dict[str, Any]]) -> str:
    parts = []
    for gate, v in s.items():
        if not v["rejected"]:
            parts.append(f"{gate} rejected no evaluable candidate")
            continue
        verdict = "saved" if v["net_value_pct"] > 0 else "cost"
        parts.append(
            f"{gate} rejected {v['rejected']} candidates: {v['correct_rejections']} would have lost, "
            f"{v['missed_opportunities']} would have won; net it {verdict} {abs(v['net_value_pct']):.2f} % (sum of per-trade returns)"
        )
    return "; ".join(parts) + "."


def counterfactual_markdown(s: dict[str, dict[str, Any]]) -> str:
    lines = [
        "## Counterfactual: role of AI (T-1203)",
        "",
        "Hypothetical triple-barrier outcome of every candidate rejected by the ML / LLM gates "
        "(same fees and slippage as the backtest).",
        "",
        "| gate | rejected | correct (would lose) | missed (would win) | avoided loss % | missed profit % | net value % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for gate, v in s.items():
        lines.append(
            f"| {gate} | {v['rejected']} | {v['correct_rejections']} | {v['missed_opportunities']} | "
            f"{v['avoided_loss_pct']:.2f} | {v['missed_profit_pct']:.2f} | {v['net_value_pct']:+.2f} |"
        )
    return "\n".join([*lines, "", f"**Summary:** {one_liner(s)}", ""])
