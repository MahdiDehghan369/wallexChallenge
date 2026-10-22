"""Backtest engine (T-901).

Runs **the same Orchestrator and DecisionPipeline** as paper/live with ``SimClock`` +
``HistoricalMarketData`` + ``BacktestBroker`` + ``InMemoryStore``. Logs are written in the live format
to ``reports/backtest/{run_id}/logs/``.

LLM modes: ``off`` (reviewer / analyst use deterministic fallbacks, ``status=SKIPPED``), ``cached``
(LLM cache only — no network; misses fall back), ``live_sampled`` (a deterministic ``sample_rate``
fraction of candidates goes to the real LLM through the cache, the rest falls back).
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from siavash.backtest.broker import BacktestBroker
from siavash.backtest.market_data import HistoricalMarketData
from siavash.config import AppConfig, config_hash
from siavash.core.ids import new_ulid
from siavash.core.models import Candidate, MLScore, PortfolioSnapshot, RegimeAssessment, TradeReview
from siavash.core.types import Side
from siavash.engine.clock import SimClock
from siavash.engine.factory import assemble, make_pipeline
from siavash.engine.orchestrator import Orchestrator
from siavash.engine.store import InMemoryStore
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.logging import get_logger
from siavash.reporting.metrics import metrics_from_logs
from siavash.runtime import get_runtime, init_runtime

log = get_logger(__name__)


@dataclass
class BacktestResult:
    run_id: str
    out_dir: Path
    start: datetime
    end: datetime
    universe: list[str]
    metrics: dict[str, Any]
    trades: pd.DataFrame
    llm_mode: str
    data_source: str
    extra: dict[str, Any] = field(default_factory=dict)


def backtest_config(cfg: AppConfig, logs_dir: Path) -> AppConfig:
    """``cfg`` in backtest mode with backtest loop timings and per-run log paths."""
    loop = cfg.base.loop.model_copy(update=dict(cfg.base.bt.loop_overrides))
    execution = cfg.base.execution.model_copy(update=dict(cfg.base.bt.execution_overrides))
    paths = cfg.base.paths.model_copy(update={"logs": str(logs_dir)})
    base = cfg.base.model_copy(
        update={"mode": "backtest", "loop": loop, "execution": execution, "paths": paths}
    )
    return cfg.model_copy(update={"base": base})


class SampledReviewer:
    """Deterministically routes ``rate`` of candidates to the LLM reviewer, the rest to the fallback."""

    def __init__(self, llm_reviewer: Any, fallback_reviewer: Any, rate: float) -> None:
        self.llm = llm_reviewer
        self.fallback = fallback_reviewer
        self.rate = rate

    def picked(self, c: Candidate) -> bool:
        h = hashlib.sha256(f"{c.symbol}|{c.ts.isoformat()}|{c.strategy.value}".encode()).digest()
        return int.from_bytes(h[:4], "big") / 2**32 < self.rate

    async def review(
        self, c: Candidate, f: Any, ml: MLScore, r: RegimeAssessment, p: PortfolioSnapshot
    ) -> TradeReview:
        return await (self.llm if self.picked(c) else self.fallback).review(c, f, ml, r, p)


def pick_universe(md: HistoricalMarketData, guard: SymbolGuard, max_markets: int) -> list[str]:
    """Tradable TMN markets with data, ranked by average quote volume (close × volume)."""
    from siavash.core.types import Timeframe

    scores = []
    for (sym, tf), s in md.series.items():
        if tf is not Timeframe.M15 or not guard.is_tradable(sym, Side.BUY)[0] or not len(s.closes):
            continue
        volumes = np.array([c.volume for c in s.candles])
        scores.append((float((s.closes * volumes).mean()), sym))
    return [sym for _, sym in sorted(scores, reverse=True)[:max_markets]]


async def run_backtest_async(
    cfg: AppConfig,
    md_factory: Any,
    start: datetime | None = None,
    end: datetime | None = None,
    llm_mode: str = "off",
    sample_rate: float = 0.2,
    out_root: str | Path = "reports/backtest",
    config_dir: str | Path = "config",
    universe: list[str] | None = None,
    data_source: str = "processed",
    run_id: str | None = None,
    warmup_days: float | None = None,
) -> BacktestResult:
    """Run one backtest. ``md_factory(clock) -> HistoricalMarketData``."""
    run_id = run_id or new_ulid()
    out = Path(out_root) / run_id
    logs = out / "logs"
    bcfg = backtest_config(cfg, logs)
    prev_rt = get_runtime()
    init_runtime(mode="backtest", config_hash=config_hash(bcfg), run_id=run_id)

    probe = SimClock(datetime(2000, 1, 1, tzinfo=UTC))
    md: HistoricalMarketData = md_factory(probe)
    data_start, data_end = md.time_range()
    warm = bcfg.base.bt.warmup_days if warmup_days is None else warmup_days
    start = start or data_start + timedelta(days=warm)
    end = min(end or data_end, data_end)
    if start >= end:
        raise ValueError(f"empty backtest window: {start} >= {end} (data {data_start} .. {data_end})")
    clock = SimClock(start + timedelta(seconds=5))
    md.clock = clock

    registry = MarketRegistry(md.list_markets, clock)
    await registry.load()
    guard = SymbolGuard.from_config(bcfg, registry, config_dir)
    broker = BacktestBroker.from_config(bcfg, md, guard, registry, clock)
    uni = universe or pick_universe(md, guard, bcfg.strategy.universe.max_markets)
    app = assemble(
        bcfg, "backtest", clock, md, broker, registry, guard, InMemoryStore(), logs, out, universe=uni
    )
    orch = Orchestrator(app)
    pipe_mode = {"off": "off", "cached": "replay", "live_sampled": "live"}[llm_mode]
    pipeline = make_pipeline(app, orch, bcfg, llm_mode=pipe_mode)
    if llm_mode == "live_sampled":
        from siavash.agents.reviewer import LLMReviewer

        pipeline.reviewer = SampledReviewer(pipeline.reviewer, LLMReviewer(bcfg, None), sample_rate)  # type: ignore[attr-defined]
    orch.pipeline = pipeline
    log.info(
        "backtest_start",
        run_id=run_id,
        start=start.isoformat(),
        end=end.isoformat(),
        universe=uni,
        llm_mode=llm_mode,
    )

    task = asyncio.ensure_future(orch.run())
    await clock.run_until(end)
    await orch.shutdown()
    await asyncio.wait_for(task, timeout=60)
    metrics, trades = metrics_from_logs(logs, float(bcfg.risk.capital_base_tmn))
    init_runtime(mode=prev_rt.mode, config_hash=prev_rt.config_hash)
    log.info(
        "backtest_done", run_id=run_id, net_return_pct=metrics["net_return_pct"], trades=metrics["n_trades"]
    )
    return BacktestResult(
        run_id, out, start, end, uni, metrics, trades, llm_mode, data_source, {"config": bcfg, "md": md}
    )


def run_backtest(
    cfg: AppConfig,
    start: datetime | None = None,
    end: datetime | None = None,
    llm_mode: str = "off",
    **kw: Any,
) -> BacktestResult:
    """Synchronous wrapper; pass ``md_factory`` (see :func:`synthetic_md_factory` / :func:`processed_md_factory`)."""
    return asyncio.run(run_backtest_async(cfg, start=start, end=end, llm_mode=llm_mode, **kw))


def synthetic_md_factory(cfg: AppConfig, days: int, seed: int = 42) -> Any:
    from siavash.data.synthetic import make_synthetic_market

    frames = make_synthetic_market(seed=seed, days=days)

    def factory(clock: Any) -> HistoricalMarketData:
        return HistoricalMarketData.from_15m(frames, clock, spread_pct_fn=cfg.base.bt.spread_for)

    return factory


def processed_md_factory(cfg: AppConfig, processed_dir: str | Path, symbols: list[str] | None = None) -> Any:
    def factory(clock: Any) -> HistoricalMarketData:
        return HistoricalMarketData.from_processed_dir(
            processed_dir, clock, symbols, spread_pct_fn=cfg.base.bt.spread_for
        )

    return factory
