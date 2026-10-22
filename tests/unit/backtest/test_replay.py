"""T-1305: logs of a synthetic backtest are replayed with ≥ 99 % identical decisions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from siavash.backtest.engine import run_backtest_async
from siavash.backtest.market_data import HistoricalMarketData, frames_from_15m
from siavash.backtest.replay import likely_cause, log_context, match, replay
from siavash.backtest.walkforward import with_fees
from siavash.config import load_config
from siavash.data.synthetic import make_synthetic_market
from siavash.engine.clock import SimClock
from siavash.reporting.consistency import load_candidates


async def test_replay_reproduces_backtest(tmp_path: Path) -> None:
    cfg = with_fees(load_config("config"), 0.05)  # cheap costs so that orders are actually placed
    frames = frames_from_15m(make_synthetic_market(seed=7, days=20))
    md = HistoricalMarketData(frames, SimClock(datetime(2000, 1, 1, tzinfo=UTC)))
    _, data_end = md.time_range()
    start, end = data_end - timedelta(days=3), data_end
    original = await run_backtest_async(
        cfg,
        lambda c: HistoricalMarketData(frames, c, spread_pct_fn=cfg.base.bt.spread_for),
        start=start,
        end=end,
        llm_mode="off",
        out_root=tmp_path / "orig",
        universe=["BTC/TMN", "ETH/TMN"],
        data_source="synthetic",
        run_id="orig",
    )
    logs = original.out_dir / "logs"
    ctx = log_context(logs / "decisions.jsonl")
    assert ctx["universe"] == ["BTC/TMN", "ETH/TMN"] and ctx["start"] is not None
    cands = load_candidates(logs / "decisions.jsonl")
    assert len(cands) > 0 and (cands["outcome"] == "ORDER_PLACED").any()

    res = await replay(cfg, logs, frames, out=tmp_path / "reports")
    assert res.n_original == len(cands)
    assert res.outcome_match_pct is not None and res.outcome_match_pct >= 99
    assert res.stage_match_pct is not None and res.stage_match_pct >= 99
    text = res.report.read_text(encoding="utf-8")
    assert "identical `outcome_action`" in text and "## Differences" in text


def test_match_and_causes() -> None:
    ts = pd.Timestamp("2026-01-01", tz="UTC")
    base = {
        "symbol": "BTC/TMN",
        "strategy": "S1_TREND_BREAKOUT",
        "position_id": None,
        "entry_ref_price": 1.0,
        "reject_code": None,
    }
    orig = pd.DataFrame(
        [
            {**base, "signal_ts": ts, "outcome": "ORDER_PLACED", "rejected_at": None},
            {**base, "signal_ts": ts + pd.Timedelta(minutes=15), "outcome": "REJECTED", "rejected_at": "LLM"},
            {
                **base,
                "signal_ts": ts + pd.Timedelta(minutes=30),
                "outcome": "REJECTED",
                "rejected_at": "RISK",
            },
        ]
    )
    rep = orig.copy()
    rep.loc[1, ["outcome", "rejected_at"]] = ["ORDER_PLACED", None]
    rep = rep.drop(index=2)
    rows, outcome, stage = match(orig, rep)
    assert outcome == stage == round(1 / 3 * 100, 2)
    causes = [likely_cause(r) for _, r in rows.iterrows()]
    assert any("LLM" in c for c in causes) and any("not reproduced" in c for c in causes)
