"""Judge replay (T-1305): ``siavash replay --logs <dir> [--from --to]``.

Re-runs the engine on the recorded data (``data/live`` + history for warm-up, or given frames) with the
recorded LLM answers (cache in **replay** mode: no network, a miss falls back) and the model version found
in the log, then matches the reproduced ``ENTRY_CANDIDATE`` records with the originals on
``(symbol, strategy, signal ts)``: share of identical ``outcome_action`` and ``rejected_at``.
``reports/replay.md`` lists every difference with its likely cause.

The universe and start time come from the first ``UNIVERSE_UPDATE`` record of the log (the backtest
engine writes one at start, live mode on every universe refresh; replay keeps the first one fixed).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.backtest.walkforward import cfg_set
from siavash.config import config_hash
from siavash.reporting.consistency import KEY, STAGE_HINTS, load_candidates

START_OFFSET = timedelta(seconds=5)  # the engine clock starts 5 s after the window start


@dataclass
class ReplayResult:
    n_original: int
    n_replayed: int
    n_matched: int
    outcome_match_pct: float | None
    stage_match_pct: float | None
    diffs: pd.DataFrame
    report: Path


def log_context(decisions_path: Path) -> dict[str, Any]:
    """First universe / its timestamp, model version and config hash recorded in the log."""
    ctx: dict[str, Any] = {"universe": None, "start": None, "model_version": None, "config_hash": None}
    for line in decisions_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        ctx["config_hash"] = ctx["config_hash"] or d.get("config_hash")
        if ctx["start"] is None:
            ctx["start"] = pd.Timestamp(d["ts"]).to_pydatetime()
        if d.get("type") == "UNIVERSE_UPDATE" and ctx["universe"] is None:
            ctx["universe"] = list((d.get("extra") or {}).get("selected") or [])
            ctx["start"] = pd.Timestamp(d["ts"]).to_pydatetime()
        mv = (d.get("ml") or {}).get("model_version")
        if mv and ctx["model_version"] is None:
            ctx["model_version"] = mv
    return ctx


def match(original: pd.DataFrame, replayed: pd.DataFrame) -> tuple[pd.DataFrame, float | None, float | None]:
    m = original.merge(replayed, on=KEY, how="outer", suffixes=("_orig", "_replay"), indicator=True)
    both = m[m["_merge"] == "both"]
    if both.empty:
        return m, None, None
    same_outcome = both["outcome_orig"] == both["outcome_replay"]
    same_stage = both["rejected_at_orig"].fillna("") == both["rejected_at_replay"].fillna("")
    # unmatched rows count as mismatches against the original
    n = len(original)
    return m, round(float(same_outcome.sum()) / n * 100, 2), round(float(same_stage.sum()) / n * 100, 2)


def likely_cause(row: pd.Series) -> str:
    if row["_merge"] == "left_only":
        return "candidate not reproduced — candle data differs (missing / late recorded candles) or universe changed"
    if row["_merge"] == "right_only":
        return "extra candidate in replay — recorded data has candles the live run did not see in time"
    stage = row.get("rejected_at_orig") or row.get("rejected_at_replay")
    if stage in STAGE_HINTS:
        return STAGE_HINTS[str(stage)]
    return "real fill / order timing differs from the simulated fill, so the portfolio state diverged"


def write_report(
    res_rows: pd.DataFrame,
    out: Path,
    n_orig: int,
    n_rep: int,
    outcome: float | None,
    stage: float | None,
    ctx: dict[str, Any],
    current_hash: str,
) -> Path:
    diffs = res_rows[
        (res_rows["_merge"] != "both")
        | (res_rows["outcome_orig"] != res_rows["outcome_replay"])
        | (res_rows["rejected_at_orig"].fillna("") != res_rows["rejected_at_replay"].fillna(""))
    ]
    lines = [
        "# Replay",
        "",
        f"- original candidates: {n_orig} · replayed: {n_rep}",
        f"- identical `outcome_action`: **{outcome} %** · identical `rejected_at`: **{stage} %**",
        f"- universe: {', '.join(ctx['universe'] or []) or '(from candidates)'} · model: {ctx['model_version'] or 'none'}",
        f"- config hash in log: `{ctx['config_hash']}` · current: `{current_hash}`"
        + (
            " (same)"
            if ctx["config_hash"] == current_hash
            else " (differs — replay uses the current config files)"
        ),
        "",
        "## Differences",
        "",
    ]
    if diffs.empty:
        lines.append("- none")
    else:
        lines += [
            "| symbol | strategy | signal ts | original | replay | likely cause |",
            "|---|---|---|---|---|---|",
        ]
        for _, r in diffs.iterrows():
            o = f"{r.get('outcome_orig')}/{r.get('rejected_at_orig')}" if r["_merge"] != "right_only" else "—"
            p = (
                f"{r.get('outcome_replay')}/{r.get('rejected_at_replay')}"
                if r["_merge"] != "left_only"
                else "—"
            )
            lines.append(
                f"| {r['symbol']} | {r['strategy']} | {r['signal_ts']} | {o} | {p} | {likely_cause(r)} |"
            )
    path = out / "replay.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([*lines, ""]), encoding="utf-8")
    return path


async def replay(
    cfg: Any,
    logs_dir: str | Path,
    frames: Any,
    out: str | Path = "reports",
    start: datetime | None = None,
    end: datetime | None = None,
    run_id: str = "replay",
) -> ReplayResult:
    from siavash.backtest.engine import run_backtest_async
    from siavash.backtest.market_data import HistoricalMarketData

    logs, out = Path(logs_dir), Path(out)
    ctx = log_context(logs / "decisions.jsonl")
    original = load_candidates(logs / "decisions.jsonl")
    if original.empty:
        raise ValueError(f"no ENTRY_CANDIDATE records in {logs}")
    start = start or (ctx["start"] - START_OFFSET)
    end = end or (original["signal_ts"].max().to_pydatetime() + timedelta(minutes=16))
    original = original[
        (original["signal_ts"] >= pd.Timestamp(start)) & (original["signal_ts"] < pd.Timestamp(end))
    ]
    if ctx["model_version"]:
        cfg = cfg_set(
            cfg_set(cfg, "strategy.ml.enabled", True), "strategy.ml.model_version", ctx["model_version"]
        )
    universe = ctx["universe"] or sorted(original["symbol"].unique())
    root = out / "replay_runs"
    rlogs = root / run_id / "logs"
    if rlogs.parent.exists():
        shutil.rmtree(rlogs.parent)
    if (logs / "llm" / "cache.sqlite").exists():
        (rlogs / "llm").mkdir(parents=True, exist_ok=True)
        shutil.copy2(logs / "llm" / "cache.sqlite", rlogs / "llm" / "cache.sqlite")
    res = await run_backtest_async(
        cfg,
        lambda clock: HistoricalMarketData(frames, clock, spread_pct_fn=cfg.base.bt.spread_for),
        start=start,
        end=end,
        llm_mode="cached",
        out_root=root,
        universe=universe,
        data_source="replay",
        run_id=run_id,
    )
    replayed = load_candidates(rlogs / "decisions.jsonl")
    rows, outcome, stage = match(original, replayed)
    path = write_report(
        rows, out, len(original), len(replayed), outcome, stage, ctx, config_hash(res.extra["config"])
    )
    n_matched = int((rows["_merge"] == "both").sum())
    return ReplayResult(len(original), len(replayed), n_matched, outcome, stage, rows, path)
