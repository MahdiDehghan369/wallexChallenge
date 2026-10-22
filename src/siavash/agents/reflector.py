"""DailyReflector agent (T-506): ``siavash reflect --date YYYY-MM-DD`` → ``reports/daily_YYYYMMDD.md``.

Reads the day's decisions / fills / equity (Tehran trading day), computes the T-1201 statistics (and the
AI counterfactual when candles are available), asks the LLM (``reflector_v1``) for a review and at most
three parameter suggestions. Suggestions are kept only for parameters listed in
``strategy.tunable_ranges`` and strictly inside their range; each is labelled "Suggestion — requires
human review". **Nothing is applied.** Without an LLM the report contains the statistics only.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel, Field

from siavash.agents.prompts import load_prompt
from siavash.agents.schemas import complete_with_repair
from siavash.logging import get_logger
from siavash.reporting.metrics import compute_metrics, load_decisions, load_equity, load_fills

log = get_logger(__name__)

PROMPT = "reflector_v1"
LABEL = "Suggestion — requires human review"
MAX_SUGGESTIONS = 3


class Suggestion(BaseModel):
    parameter: str
    current: float | None = None
    proposed: float
    reason: str = ""


class LLMReflectionOut(BaseModel):
    summary: str
    worked: list[str] = Field(default_factory=list)
    did_not_work: list[str] = Field(default_factory=list)
    ai_effect: str = ""
    suggestions: list[Suggestion] = Field(default_factory=list)


def current_value(cfg: Any, path: str) -> float | None:
    obj: Any = cfg
    for part in path.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    try:
        return float(obj)
    except (TypeError, ValueError):
        return None


def day_bounds(day: date, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = datetime.combine(day, time(0, 0), tzinfo=ZoneInfo(tz))
    return pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(start + timedelta(days=1)).tz_convert("UTC")


def _slice(df: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    return df if df.empty else df[(df["ts"] >= lo) & (df["ts"] < hi)]


def filter_suggestions(cfg: Any, items: list[Suggestion]) -> tuple[list[Suggestion], list[str]]:
    """Keep allowed, in-range suggestions (max 3); return ``(kept, rejection notes)``."""
    ranges = cfg.strategy.tunable_ranges
    kept: list[Suggestion] = []
    dropped: list[str] = []
    for s in items:
        if s.parameter not in ranges:
            dropped.append(f"{s.parameter}: not a tunable parameter")
            continue
        lo, hi = ranges[s.parameter]
        if not lo <= s.proposed <= hi:
            dropped.append(f"{s.parameter}={s.proposed}: outside allowed range [{lo}, {hi}]")
            continue
        if len(kept) >= MAX_SUGGESTIONS:
            dropped.append(f"{s.parameter}: more than {MAX_SUGGESTIONS} suggestions")
            continue
        kept.append(s.model_copy(update={"current": current_value(cfg, s.parameter)}))
    return kept, dropped


class DailyReflector:
    def __init__(
        self,
        cfg: Any,
        llm: Any = None,
        logs_dir: str | Path = "logs",
        frames: Any = None,
        reports_dir: str | Path = "reports",
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.logs = Path(logs_dir)
        self.frames = frames
        self.reports = Path(reports_dir)
        self.prompt_version, self.system = load_prompt(PROMPT)

    def build_input(self, day: date) -> dict[str, Any]:
        lo, hi = day_bounds(day, self.cfg.base.timezone)
        fills = _slice(load_fills(self.logs / "fills.csv"), lo, hi)
        equity = _slice(load_equity(self.logs / "equity.csv"), lo, hi)
        decisions = _slice(load_decisions(self.logs / "decisions.jsonl"), lo, hi)
        m = compute_metrics(fills, equity, decisions, float(self.cfg.risk.capital_base_tmn))
        stats = {
            k: m.get(k)
            for k in (
                "net_return_pct",
                "pnl_tmn",
                "max_drawdown_pct",
                "n_trades",
                "win_rate",
                "profit_factor",
                "total_fees_tmn",
                "total_volume_tmn",
            )
        }
        payload: dict[str, Any] = {
            "date": day.isoformat(),
            "stats": stats,
            "by_strategy": m.get("by_strategy", {}),
            "by_symbol": m.get("by_symbol", {}),
            "by_regime": m.get("by_regime", {}),
            "pipeline": m.get("pipeline", {}),
            "parameters": {
                p: {"current": current_value(self.cfg, p), "allowed": list(r)}
                for p, r in self.cfg.strategy.tunable_ranges.items()
            },
        }
        if self.frames is not None:
            from siavash.reporting.counterfactual import evaluate_rejections, summarize

            cf = evaluate_rejections(self.logs / "decisions.jsonl", self.frames, self.cfg)
            cf = (
                cf[(pd.to_datetime(cf["ts"], utc=True) >= lo) & (pd.to_datetime(cf["ts"], utc=True) < hi)]
                if len(cf)
                else cf
            )
            payload["counterfactual"] = summarize(cf)
        return json.loads(json.dumps(payload, default=str))

    async def reflect(self, day: date) -> Path:
        payload = self.build_input(day)
        out: LLMReflectionOut | None = None
        status = "skipped"
        dropped: list[str] = []
        if self.llm is not None and getattr(self.llm, "enabled", True):
            try:
                out, _ = await complete_with_repair(
                    self.llm, self.system, payload, LLMReflectionOut, self.prompt_version
                )
                status = "ok"
            except Exception as e:  # LLM down / budget / invalid: statistics-only report
                log.warning("reflector_fallback", error=str(e)[:200])
                status = "fallback"
        kept: list[Suggestion] = []
        if out is not None:
            kept, dropped = filter_suggestions(self.cfg, out.suggestions)
        path = self.reports / f"daily_{day:%Y%m%d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(payload, out, kept, dropped, status, self.prompt_version), encoding="utf-8")
        return path


def render(
    payload: dict[str, Any],
    out: LLMReflectionOut | None,
    kept: list[Suggestion],
    dropped: list[str],
    status: str,
    prompt_version: str,
) -> str:
    s = payload["stats"]
    lines = [
        f"# Daily reflection — {payload['date']} (Tehran)",
        "",
        f"_LLM status: {status} · prompt `{prompt_version}` · nothing in this report is applied automatically._",
        "",
        "## Numbers",
        "",
        "| metric | value |",
        "|---|---:|",
        *[f"| {k} | {v} |" for k, v in s.items()],
        "",
        f"- pipeline: {payload['pipeline']}",
    ]
    if "counterfactual" in payload:
        lines.append(f"- AI counterfactual: {payload['counterfactual']}")
    if out is not None:
        worked = [f"- {x}" for x in out.worked] or ["- —"]
        failed = [f"- {x}" for x in out.did_not_work] or ["- —"]
        lines += [
            "",
            "## Review",
            "",
            out.summary,
            "",
            "**Worked**",
            *worked,
            "",
            "**Did not work**",
            *failed,
            "",
            f"**AI effect:** {out.ai_effect or '—'}",
            "",
        ]
        lines += ["## Parameter suggestions", ""]
        if kept:
            lines += ["| parameter | current | proposed | reason | status |", "|---|---:|---:|---|---|"]
            lines += [f"| {k.parameter} | {k.current} | {k.proposed} | {k.reason} | {LABEL} |" for k in kept]
        else:
            lines.append("- none")
        if dropped:
            lines += [
                "",
                "Dropped (outside the allowed ranges in `strategy.tunable_ranges`):",
                *[f"- {d}" for d in dropped],
            ]
    return "\n".join([*lines, ""])
