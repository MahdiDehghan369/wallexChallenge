"""T-901: backtest engine on 30 days of synthetic data — completes, trades, deterministic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from siavash.backtest.engine import BacktestResult, run_backtest_async, synthetic_md_factory
from siavash.config import AppConfig, load_config
from siavash.engine.clock import SimClock

DAYS = 30
WINDOW_DAYS = 5  # trade the last days only; the full 30 days of data provide warm-up history


def _cfg() -> AppConfig:
    cfg = load_config("config")
    # the pessimistic default fees make the fee filter reject every synthetic candidate;
    # lower *costs* (not risk caps) so the test exercises the full trade path
    fees = cfg.risk.fees.model_copy(update={"maker_pct": 0.05, "taker_pct": 0.1})
    return cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"fees": fees})})


async def _run(cfg: AppConfig, out: Path, run_id: str) -> BacktestResult:
    factory = synthetic_md_factory(cfg, DAYS, seed=7)
    _, data_end = factory(SimClock(datetime(2000, 1, 1, tzinfo=UTC))).time_range()
    return await run_backtest_async(
        cfg,
        factory,
        start=data_end - timedelta(days=WINDOW_DAYS),
        end=data_end,
        llm_mode="off",
        out_root=out,
        universe=["BTC/TMN", "ETH/TMN"],
        data_source="synthetic",
        run_id=run_id,
    )


def _comparable(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in df.columns if not c.endswith("id")]].reset_index(drop=True)


async def test_backtest_trades_and_is_deterministic(tmp_path: Path) -> None:
    cfg = _cfg()
    r1 = await _run(cfg, tmp_path, "run1")
    r2 = await _run(cfg, tmp_path, "run2")

    assert (r1.out_dir / "logs").is_dir()
    assert r1.metrics["pipeline"]["candidates"] > 0
    assert r1.metrics["n_trades"] > 0
    keys = ["n_trades", "net_return_pct", "pnl_tmn", "max_drawdown_pct", "total_fees_tmn", "pipeline"]
    assert {k: r1.metrics[k] for k in keys} == {k: r2.metrics[k] for k in keys}
    assert _comparable(r1.trades).equals(_comparable(r2.trades))


async def test_empty_window_rejected(tmp_path: Path) -> None:
    cfg = _cfg()
    factory = synthetic_md_factory(cfg, 3, seed=1)
    with pytest.raises(ValueError, match="empty backtest window"):
        await run_backtest_async(
            cfg, factory, llm_mode="off", out_root=tmp_path, universe=["BTC/TMN"], warmup_days=10
        )
