"""Backtest ↔ shadow ↔ live consistency (T-1003).

Input: a shadow / live ``logs/`` directory and the live data it recorded (``data/live/``). The same
interval is replayed with the backtest engine on that data (merged with downloaded history for indicator
warm-up) with ``llm_mode=cached`` using the shadow's LLM cache. Compared:

- candidates: share of identical ``(symbol, strategy, signal ts)``;
- decisions: share of identical outcomes (placed / rejected at the same stage) among common candidates;
- slippage: actual entry fill vs the signal reference price, against the backtest assumption;
- returns: net return % of both runs.

``reports/consistency.md`` explains the main differences automatically; ``reports/shadow/REPORT.md``
holds the T-903 metrics of the shadow run.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.core.types import Timeframe
from siavash.reporting.metrics import load_fills, metrics_from_logs

KEY = ["symbol", "strategy", "signal_ts"]
STAGE_HINTS = {
    "LLM": "LLM verdicts differ — usually cache misses in the replay (cached mode falls back) or a different prompt version",
    "ML": "ML scores differ — different model version or features computed on different candles",
    "RISK": "risk decisions differ — portfolio state (cash, open positions, cooldowns) diverged between the runs",
    "REGIME": "regime differs — market features (BTC / breadth) computed on different data",
    "FEE_FILTER": "fee filter differs — expected move or fee configuration differs",
    "SPREAD": "spread / book checks differ — the replay uses recorded books only where they exist",
    "EXECUTION": "execution differs — live fills / timeouts vs simulated fills",
}


# ---------------------------------------------------------------------------- loading


def load_candidates(path: str | Path) -> pd.DataFrame:
    """``ENTRY_CANDIDATE`` records of a ``decisions.jsonl`` with the signal timestamp as key."""
    rows = []
    p = Path(path)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("type") != "ENTRY_CANDIDATE" or not d.get("signal"):
                continue
            sig = d["signal"]
            rows.append(
                {
                    "symbol": d.get("symbol") or sig.get("symbol"),
                    "strategy": d.get("strategy") or sig.get("strategy"),
                    "signal_ts": pd.Timestamp(sig["ts"]).tz_convert("UTC")
                    if pd.Timestamp(sig["ts"]).tzinfo
                    else pd.Timestamp(sig["ts"], tz="UTC"),
                    "outcome": d.get("outcome_action"),
                    "rejected_at": d.get("rejected_at"),
                    "reject_code": d.get("reject_code"),
                    "position_id": d.get("position_id"),
                    "entry_ref_price": float(sig.get("entry_ref_price") or 0.0),
                }
            )
    cols = [*KEY, "outcome", "rejected_at", "reject_code", "position_id", "entry_ref_price"]
    return pd.DataFrame(rows, columns=cols).drop_duplicates(KEY, keep="last").reset_index(drop=True)


def load_live_frames(live_dir: str | Path) -> dict[str, dict[Timeframe, pd.DataFrame]]:
    """``data/live/{day}/candles_{tf}.jsonl`` → ``{symbol: {tf: OHLCV frame}}`` (de-duplicated)."""
    rows: dict[tuple[str, Timeframe], list[dict[str, Any]]] = {}
    for f in sorted(Path(live_dir).glob("*/candles_*.jsonl")):
        tf = Timeframe(f.stem.removeprefix("candles_"))
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c = json.loads(line)
                rows.setdefault((c["symbol"], tf), []).append(c)
    out: dict[str, dict[Timeframe, pd.DataFrame]] = {}
    for (sym, tf), rs in rows.items():
        df = pd.DataFrame(rs)
        df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
        cols = ["ts_open", "open", "high", "low", "close", "volume", "is_filled"]
        df = df.reindex(columns=cols).drop_duplicates("ts_open", keep="last").sort_values("ts_open")
        out.setdefault(sym, {})[tf] = df.reset_index(drop=True)
    return out


def merge_frames(
    history: dict[str, dict[Timeframe, pd.DataFrame]], live: dict[str, dict[Timeframe, pd.DataFrame]]
) -> dict[str, dict[Timeframe, pd.DataFrame]]:
    """History for warm-up + recorded live candles (live wins where both exist)."""
    out: dict[str, dict[Timeframe, pd.DataFrame]] = {s: dict(fr) for s, fr in history.items()}
    for sym, by_tf in live.items():
        for tf, df in by_tf.items():
            old = out.get(sym, {}).get(tf)
            merged = df if old is None else pd.concat([old, df]).drop_duplicates("ts_open", keep="last")
            out.setdefault(sym, {})[tf] = merged.sort_values("ts_open").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------- comparison


def _decision(outcome: Any, rejected_at: Any) -> str:
    if outcome == "ORDER_PLACED":
        return "PLACED"
    return f"REJECTED@{rejected_at if isinstance(rejected_at, str) else '?'}"


def compare_candidates(ref: pd.DataFrame, other: pd.DataFrame) -> dict[str, Any]:
    union = pd.merge(ref, other, on=KEY, how="outer", suffixes=("_ref", "_bt"), indicator=True)
    common = union[union["_merge"] == "both"].copy()
    n_ref, n_bt, n_common = len(ref), len(other), len(common)
    if n_common:
        common["dec_ref"] = [
            _decision(o, r) for o, r in zip(common["outcome_ref"], common["rejected_at_ref"], strict=True)
        ]
        common["dec_bt"] = [
            _decision(o, r) for o, r in zip(common["outcome_bt"], common["rejected_at_bt"], strict=True)
        ]
        same = common["dec_ref"] == common["dec_bt"]
        diff = common[~same]
        pairs = diff.groupby(["dec_ref", "dec_bt"]).size().sort_values(ascending=False)
    else:
        same, pairs = pd.Series(dtype=bool), pd.Series(dtype=int)
    only_ref = union[union["_merge"] == "left_only"]
    only_bt = union[union["_merge"] == "right_only"]
    return {
        "n_ref": n_ref,
        "n_bt": n_bt,
        "n_common": n_common,
        "pct_common_of_ref": round(n_common / n_ref * 100, 2) if n_ref else None,
        "pct_common_of_union": round(n_common / len(union) * 100, 2) if len(union) else None,
        "pct_same_decision": round(float(same.mean()) * 100, 2) if n_common else None,
        "decision_diffs": {f"{a} → {b}": int(v) for (a, b), v in pairs.items()},
        "only_ref_by_symbol": only_ref.groupby("symbol").size().to_dict(),
        "only_bt_by_symbol": only_bt.groupby("symbol").size().to_dict(),
    }


def entry_slippage_pct(candidates: pd.DataFrame, fills: pd.DataFrame) -> float | None:
    """Mean (entry fill / signal reference − 1) × 100 over placed candidates with buy fills."""
    placed = candidates[(candidates["outcome"] == "ORDER_PLACED") & candidates["position_id"].notna()]
    if placed.empty or fills.empty:
        return None
    buys = fills[fills["side"] == "buy"].copy()
    buys["notional"] = buys["price"] * buys["qty"]
    vwap = buys.groupby("position_id").agg(n=("notional", "sum"), q=("qty", "sum"))
    vwap["px"] = vwap["n"] / vwap["q"]
    m = placed.merge(vwap[["px"]], left_on="position_id", right_index=True)
    m = m[m["entry_ref_price"] > 0]
    return round(float(((m["px"] / m["entry_ref_price"] - 1) * 100).mean()), 4) if len(m) else None


def explain(
    cmp: dict[str, Any], slip_ref: float | None, slip_assumed: float, ret_ref: float, ret_bt: float
) -> list[str]:
    out = []
    pc = cmp["pct_common_of_ref"]
    if pc is None:
        out.append("the shadow/live run produced no candidates in this interval.")
    elif pc < 80:
        worst = sorted(cmp["only_ref_by_symbol"].items(), key=lambda kv: -kv[1])[:3]
        out.append(
            f"only {pc:.1f} % of live candidates were reproduced — the candle data differs (sparse / late candles, "
            f"universe changes, missing recorded data). Most missing: {', '.join(f'{s} ({n})' for s, n in worst) or '—'}."
        )
    else:
        out.append(
            f"{pc:.1f} % of live candidates were reproduced by the backtest: signal generation is consistent."
        )
    for pair, n in list(cmp["decision_diffs"].items())[:3]:
        stage = next((s for s in STAGE_HINTS if s in pair), None)
        out.append(f"{n}× {pair}: " + (STAGE_HINTS[stage] if stage else "decision path differs"))
    if slip_ref is not None:
        verdict = "worse than" if slip_ref > slip_assumed else "within"
        out.append(
            f"real entry slippage {slip_ref:.3f} % is {verdict} the backtest assumption {slip_assumed:.3f} %."
        )
    out.append(
        f"net return: live/shadow {ret_ref:.3f} % vs backtest {ret_bt:.3f} % (difference {ret_ref - ret_bt:+.3f} pp)."
    )
    return out


def metrics_markdown(m: dict[str, Any], title: str) -> str:
    keys = [
        "net_return_pct",
        "pnl_tmn",
        "max_drawdown_pct",
        "n_trades",
        "win_rate",
        "profit_factor",
        "trades_per_day",
        "total_volume_tmn",
        "total_fees_tmn",
        "days",
    ]
    lines = [f"# {title}", "", "| metric | value |", "|---|---:|"]
    lines += [f"| {k} | {m.get(k)} |" for k in keys]
    p = m.get("pipeline", {})
    lines += [
        "",
        "## Decision pipeline",
        "",
        f"- candidates {p.get('candidates', 0)}, placed {p.get('placed', 0)}",
        f"- rejected by stage: {p.get('rejected_by_stage', {})}",
        f"- LLM reviewed {p.get('llm_reviewed', 0)}, "
        f"approve {p.get('llm_approve_pct')} %, fallbacks {p.get('llm_fallbacks', 0)}",
        "",
    ]
    return "\n".join(lines)


def consistency_markdown(
    cmp: dict[str, Any],
    expl: list[str],
    slip_ref: float | None,
    slip_bt: float | None,
    slip_assumed: float,
    m_ref: dict[str, Any],
    m_bt: dict[str, Any],
    period: tuple[Any, Any],
) -> str:
    lines = [
        "# Backtest ↔ shadow/live consistency",
        "",
        f"- interval: {period[0]} → {period[1]}",
        "",
        "| measure | value |",
        "|---|---:|",
        f"| live/shadow candidates | {cmp['n_ref']} |",
        f"| backtest candidates | {cmp['n_bt']} |",
        f"| identical candidates (% of live) | {cmp['pct_common_of_ref']} |",
        f"| identical candidates (% of union) | {cmp['pct_common_of_union']} |",
        f"| identical decision (% of common) | {cmp['pct_same_decision']} |",
        f"| entry slippage live/shadow % | {slip_ref} |",
        f"| entry slippage backtest % | {slip_bt} |",
        f"| assumed slippage % (½ spread + extra) | {slip_assumed:.3f} |",
        f"| net return live/shadow % | {m_ref.get('net_return_pct')} |",
        f"| net return backtest % | {m_bt.get('net_return_pct')} |",
        "",
        "## Main differences (automatic)",
        "",
        *[f"- {e}" for e in expl],
        "",
        "## Decision differences",
        "",
        *([f"- {k}: {v}" for k, v in cmp["decision_diffs"].items()] or ["- none"]),
        "",
    ]
    return "\n".join(lines)


def assumed_slippage_pct(cfg: Any, symbols: list[str]) -> float:
    if not symbols:
        symbols = ["default"]
    return sum(cfg.base.bt.spread_for(s) / 2 + cfg.base.bt.extra_slippage_pct for s in symbols) / len(symbols)


async def run_consistency(
    cfg: Any, ref_logs: Path, live_dir: Path, history_dir: Path | None, out: Path, run_id: str = "consistency"
) -> Path:
    """Replay the shadow/live interval with the backtest and write both reports; returns consistency.md."""
    from siavash.backtest.engine import run_backtest_async
    from siavash.backtest.market_data import HistoricalMarketData, processed_frames

    ref_c = load_candidates(ref_logs / "decisions.jsonl")
    m_ref, _ = metrics_from_logs(ref_logs, float(cfg.risk.capital_base_tmn))
    (out / "shadow").mkdir(parents=True, exist_ok=True)
    (out / "shadow" / "REPORT.md").write_text(metrics_markdown(m_ref, "Shadow / live run"), encoding="utf-8")
    (out / "shadow" / "metrics.json").write_text(json.dumps(m_ref, indent=2, default=str), encoding="utf-8")
    if ref_c.empty:
        raise ValueError(f"no ENTRY_CANDIDATE records in {ref_logs}")
    start = ref_c["signal_ts"].min().to_pydatetime() - timedelta(minutes=1)
    end = ref_c["signal_ts"].max().to_pydatetime() + timedelta(minutes=16)
    frames = merge_frames(
        processed_frames(history_dir) if history_dir and history_dir.exists() else {},
        load_live_frames(live_dir),
    )
    bt_root = out / "consistency_runs"
    logs = bt_root / run_id / "logs"
    cache = ref_logs / "llm" / "cache.sqlite"
    if cache.exists():  # replay the shadow's LLM answers
        (logs / "llm").mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache, logs / "llm" / "cache.sqlite")
    universe = sorted(ref_c["symbol"].unique())
    res = await run_backtest_async(
        cfg,
        lambda clock: HistoricalMarketData(frames, clock, spread_pct_fn=cfg.base.bt.spread_for),
        start=start,
        end=end,
        llm_mode="cached",
        out_root=bt_root,
        universe=universe,
        data_source=f"live:{live_dir}",
        run_id=run_id,
    )
    bt_c = load_candidates(logs / "decisions.jsonl")
    cmp = compare_candidates(ref_c, bt_c)
    slip_ref = entry_slippage_pct(ref_c, load_fills(ref_logs / "fills.csv"))
    slip_bt = entry_slippage_pct(bt_c, load_fills(logs / "fills.csv"))
    slip_assumed = assumed_slippage_pct(cfg, universe)
    expl = explain(
        cmp, slip_ref, slip_assumed, float(m_ref["net_return_pct"]), float(res.metrics["net_return_pct"])
    )
    path = out / "consistency.md"
    path.write_text(
        consistency_markdown(cmp, expl, slip_ref, slip_bt, slip_assumed, m_ref, res.metrics, (start, end)),
        encoding="utf-8",
    )
    return path
