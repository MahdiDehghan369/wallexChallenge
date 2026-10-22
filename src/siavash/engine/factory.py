"""Build the engine for a mode (T-601).

LIVE → ``LiveBroker`` + ``WallexMarketData``; PAPER → ``PaperBroker`` + ``WallexMarketData``;
BACKTEST / REPLAY → ``BacktestBroker`` + ``HistoricalMarketData`` (injected by the backtest engine).
Decision components (signals, scorer, regime, reviewer, risk) are identical in every mode: the
decision pipeline is created by ``pipeline_factory(app, orchestrator)``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from siavash.core.interfaces import Broker, Clock, MarketData
from siavash.data.live import LiveDataService
from siavash.engine.clock import SystemClock
from siavash.engine.decision_log import FillsCsvLogger, JsonlDecisionLogger, OrdersLogger
from siavash.engine.orchestrator import App, Orchestrator, PipelineLike
from siavash.engine.positions import PositionManager
from siavash.engine.store import InMemoryStore, SqliteStore
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.exchange.rate_limit import ErrorCounter
from siavash.execution.router import OrderRouter
from siavash.execution.safe_submit import SafeSubmitter
from siavash.execution.tracker import OrderTracker
from siavash.ops.alerts import Alerts
from siavash.reporting.equity import EquityTracker
from siavash.reporting.latency import LatencyRecorder
from siavash.risk.breakers import BreakerController
from siavash.risk.endgame import EndgameLiquidator, assert_endgame_configured, endgame_phase
from siavash.risk.limits import CooldownTracker, EntryCounter

PipelineFactory = Callable[[App, Orchestrator], PipelineLike]


def assemble(
    cfg: Any,
    mode: str,
    clock: Clock,
    market_data: MarketData,
    broker: Broker,
    registry: MarketRegistry,
    guard: Any,
    store: Any,
    logs_dir: str | Path,
    reports_dir: str | Path,
    error_counter: ErrorCounter | None = None,
    live_data: Any = None,
    universe: list[str] | None = None,
    heartbeat_path: Path | None = None,
) -> App:
    """Wire the mode-independent components around a given broker / market data."""
    logs = Path(logs_dir)
    error_counter = error_counter or ErrorCounter(time_fn=lambda: clock.now().timestamp())
    decision_logger = JsonlDecisionLogger(logs / "decisions.jsonl")
    if live_data is None:
        live_data = LiveDataService.from_config(cfg, market_data, clock, universe or [])
        if mode in ("backtest", "replay"):
            live_data.store_dir = None
    positions = PositionManager(store, clock)
    tracker = OrderTracker(
        broker,
        clock,
        FillsCsvLogger(logs / "fills.csv"),
        price_fn=None,
        poll_seconds=cfg.base.execution.order_poll_seconds,
    )

    async def book_fn(symbol: str) -> Any:
        return await market_data.get_orderbook(symbol)

    router = OrderRouter(
        broker, registry, clock, cfg, book_fn=book_fn, submit=SafeSubmitter.from_config(cfg, clock)
    )
    equity = EquityTracker(
        broker, EquityTracker.bid_fn_from_live(live_data, market_data), store, clock, cfg, logs / "equity.csv"
    )
    alerts = Alerts.from_config(cfg) if mode in ("paper", "live") else None
    breakers = BreakerController(store, cfg, clock, alert=alerts, decision_logger=decision_logger)
    endgame = EndgameLiquidator(
        broker,
        router,
        registry,
        clock,
        cfg,
        book_fn,
        positions=positions,
        decision_logger=decision_logger,
        report_path=Path(reports_dir) / "endgame.md",
        value_fn=equity.value_of,
    )
    return App(
        cfg=cfg,
        mode=mode,
        clock=clock,
        market_data=market_data,
        broker=broker,
        registry=registry,
        guard=guard,
        live_data=live_data,
        store=store,
        positions=positions,
        tracker=tracker,
        router=router,
        equity=equity,
        breakers=breakers,
        endgame=endgame,
        cooldowns=CooldownTracker(cfg),
        entries=EntryCounter(),
        decision_logger=decision_logger,
        error_counter=error_counter,
        universe=list(universe or []),
        heartbeat_path=heartbeat_path,
        latency=LatencyRecorder(logs / "latency.jsonl"),
        alerts=alerts,
    )


def build_app(
    cfg: Any,
    mode: str | None = None,
    *,
    config_dir: str | Path = "config",
    clock: Clock | None = None,
    market_data: MarketData | None = None,
    broker: Broker | None = None,
    store: Any = None,
    universe: list[str] | None = None,
) -> App:
    """Create the ``App`` for ``mode`` (default ``cfg.mode``)."""
    mode = mode or cfg.mode
    paths = cfg.base.paths
    if mode in ("backtest", "replay"):
        if market_data is None or broker is None or clock is None:
            raise ValueError(f"{mode}: market_data, broker and clock must be injected by the backtest engine")
        registry = MarketRegistry(market_data.list_markets, clock)
        guard = SymbolGuard.from_config(cfg, registry, config_dir)
        return assemble(
            cfg,
            mode,
            clock,
            market_data,
            broker,
            registry,
            guard,
            store or InMemoryStore(),
            paths.logs,
            paths.reports,
            universe=universe,
        )

    from siavash.exchange.client import WallexHttp
    from siavash.exchange.market_data import WallexMarketData

    clock = clock or SystemClock()
    if mode == "live":
        assert_endgame_configured(cfg)
    error_counter = ErrorCounter()
    http = WallexHttp.from_config(cfg, error_counter)
    md = market_data or WallexMarketData(http, clock, ticker_cache_s=cfg.base.exchange.ticker_cache_s)
    registry = MarketRegistry(md.list_markets, clock, cfg.strategy.universe.refresh_hours)
    guard = SymbolGuard.from_config(cfg, registry, config_dir)
    store = store or SqliteStore(Path(paths.state) / "state.sqlite")
    live_data = LiveDataService.from_config(cfg, md, clock, universe or [])
    if broker is None:
        if mode == "live":
            from siavash.exchange.account import OrderIdMap, WallexAccount
            from siavash.execution.live_broker import LiveBroker

            account = WallexAccount(
                http, registry, OrderIdMap(Path(paths.state) / "id_map.sqlite"), guard, clock
            )
            broker = LiveBroker(account, OrdersLogger(Path(paths.logs) / "orders.jsonl", clock))
        elif mode == "paper":
            from siavash.execution.paper_broker import PaperBroker

            broker = PaperBroker.from_config(cfg, md, guard, registry, clock, live_data)
        else:
            raise ValueError(f"unknown mode {mode!r}")
    return assemble(
        cfg,
        mode,
        clock,
        md,
        broker,
        registry,
        guard,
        store,
        paths.logs,
        paths.reports,
        error_counter,
        live_data,
        universe,
        Path(paths.state) / "heartbeat",
    )


def strategy_stats_fn(app: App) -> Callable[[str, int], dict[str, Any]]:
    """Recent performance of one strategy from closed positions (for agents' tools / prompts)."""
    from datetime import timedelta

    def fn(strategy: str, hours: int) -> dict[str, Any]:
        since = app.clock.now() - timedelta(hours=hours)
        closed = [p for p in app.positions.closed_since(since) if p.strategy.value == strategy]
        pnl = [float(p.realized_pnl_tmn) for p in closed]
        return {
            "strategy": strategy,
            "hours": hours,
            "trades": len(closed),
            "win_rate": round(sum(1 for x in pnl if x > 0) / len(pnl), 3) if pnl else None,
            "pnl_tmn": round(sum(pnl), 0),
        }

    return fn


def recent_trades_fn(app: App) -> Callable[[str, int], list[dict[str, Any]]]:
    def fn(strategy: str, n: int) -> list[dict[str, Any]]:
        closed = sorted(
            (
                p
                for p in app.positions.positions.values()
                if p.strategy.value == strategy and p.closed_at is not None
            ),
            key=lambda p: p.closed_at,  # type: ignore[arg-type,return-value]
        )[-n:]
        return [
            {
                "symbol": p.symbol,
                "pnl_tmn": float(p.realized_pnl_tmn),
                "exit": p.exit_reason.value if p.exit_reason else None,
            }
            for p in closed
        ]

    return fn


def make_pipeline(app: App, orch: Orchestrator, cfg: Any, llm_mode: str | None = None) -> PipelineLike:
    """Production decision pipeline: signals + ML scorer + cached LLM reviewer / analyst + risk.

    ``llm_mode``: ``None`` → ``cfg.llm.cache_mode``; ``"off"`` → no LLM (deterministic fallbacks).
    """
    from siavash.agents.analyst import LLMRegimeProvider
    from siavash.agents.cache import CachedLLMClient, LLMCache
    from siavash.agents.llm import LLMClient
    from siavash.agents.reviewer import LLMReviewer
    from siavash.agents.tools import ToolRegistry, select_backend
    from siavash.engine.pipeline import DecisionPipeline

    logs = Path(cfg.base.paths.logs)
    llm: Any = None
    if llm_mode != "off":
        mode: Any = llm_mode or cfg.llm.cache_mode
        client = LLMClient.from_config(cfg)
        llm = CachedLLMClient(
            client if mode != "replay" else None, LLMCache(logs / "llm" / "cache.sqlite", mode), cfg.llm.model
        )
    tools: Any = None
    if llm is not None:
        backend = select_backend(cfg)
        if backend == "mcp":
            from siavash.exchange.mcp_tools import McpToolRegistry, mcp_server_config

            server = mcp_server_config()
            assert server is not None
            tools = McpToolRegistry(
                server,
                app.market_data,
                None,
                strategy_stats_fn(app),
                logs / "llm" / "tool_calls.jsonl",
                app.clock.now,
            )
        else:
            tools = ToolRegistry(
                app.market_data,
                None,
                strategy_stats_fn(app),
                logs / "llm" / "tool_calls.jsonl",
                app.clock.now,
            )

    def perf(hours: int) -> dict[str, Any]:
        f = strategy_stats_fn(app)
        return {s: f(s, hours) for s in cfg.strategy.strategies.enabled}

    scorer = None
    if cfg.strategy.ml.enabled:
        try:
            from siavash.ml.scorer import LgbmScorer  # type: ignore[import-not-found]

            scorer = LgbmScorer.from_config(cfg)
        except Exception:
            scorer = None
    reviewer = LLMReviewer(cfg, llm, tools, recent_trades_fn(app))
    analyst = LLMRegimeProvider(cfg, llm, tools, perf, app.decision_logger, logs / "regime.jsonl", app.clock)
    return DecisionPipeline(
        orch,
        scorer=scorer,
        reviewer=reviewer,
        regime_provider=analyst,
        dynamic_universe=app.mode not in ("backtest", "replay"),
    )


__all__ = ["PipelineFactory", "assemble", "build_app", "endgame_phase", "make_pipeline"]
