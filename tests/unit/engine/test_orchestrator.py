"""T-601: Orchestrator end-to-end on synthetic data (SimClock + fake broker + StubPipeline)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from siavash.config import load_config
from siavash.core.models import DecisionRecord, Order
from siavash.core.types import PositionStatus, Timeframe
from siavash.engine.clock import SimClock
from siavash.engine.factory import assemble
from siavash.engine.orchestrator import Orchestrator
from siavash.engine.store import InMemoryStore
from siavash.exchange import endpoints as ep
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from tests.fakes.broker import FakeBroker
from tests.fakes.candle_book import CandleBookMarketData
from tests.fakes.pipeline import StubPipeline
from tests.fakes.synthetic import SYN_START, make_synthetic_market, resample_ohlcv, to_candles
from tests.unit.exchange.conftest import DENYLIST, load

ROOT = Path(__file__).resolve().parents[3]
CFG = load_config(ROOT / "config", env_file=None)
D = Decimal


class FillOnPoll(FakeBroker):
    """Resting limit orders fill at their limit price on the first status poll."""

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        o = self.orders.get(client_order_id)
        if o is not None and not o.status.is_terminal:
            self.simulate_fill(client_order_id)
        return self.orders.get(client_order_id)


def _market(clock: SimClock) -> CandleBookMarketData:
    m = make_synthetic_market(seed=12, days=10)
    candles = {}
    for sym in ("BTC/TMN", "USDT/TMN", "BTC/USDT"):
        df = m[sym]
        candles[(sym, Timeframe.M15)] = to_candles(df, sym, "15m")
        candles[(sym, Timeframe.H1)] = to_candles(resample_ohlcv(df, "1h"), sym, "1h")
        candles[(sym, Timeframe.H4)] = to_candles(resample_ohlcv(df, "4h"), sym, "4h")
    reg_markets = ep.parse_markets(load("markets"))
    return CandleBookMarketData(markets=reg_markets, candles=candles, clock=clock)


async def test_two_simulated_days(tmp_path: Path) -> None:
    start = SYN_START + timedelta(days=6, seconds=5)
    clock = SimClock(start)
    md = _market(clock)
    registry = MarketRegistry(md.list_markets, clock)
    await registry.load()
    guard = SymbolGuard(DENYLIST, registry)
    broker = FillOnPoll({"TMN": D("5000000")}, fee_pct="0.35", clock=clock, guard=guard, market_data=md)
    app = assemble(
        CFG,
        "backtest",
        clock,
        md,
        broker,
        registry,
        guard,
        InMemoryStore(),
        tmp_path / "logs",
        tmp_path / "reports",
        universe=["BTC/TMN"],
    )
    orch = Orchestrator(app)
    stub = StubPipeline(orch)
    orch.pipeline = stub

    task = asyncio.ensure_future(orch.run())
    await clock.run_until(start + timedelta(days=2))
    await orch.shutdown()
    await asyncio.wait_for(task, timeout=10)

    closed = [p for p in app.positions.positions.values() if p.status is PositionStatus.CLOSED]
    assert stub.entries >= 1 and len(closed) >= 1 and stub.exits >= 1
    assert broker.placed and all(r.decision_id for r in broker.placed)
    assert {r.purpose for r in broker.placed} >= {"entry"}
    assert clock.now() >= start + timedelta(days=2) - timedelta(minutes=1)
    lines = (tmp_path / "logs" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [DecisionRecord.model_validate_json(x) for x in lines]
    assert any(r.extra.get("kind") == "startup" for r in recs)
    assert (tmp_path / "logs" / "fills.csv").exists() and (tmp_path / "logs" / "equity.csv").exists()
    # clean shutdown: no resting entry orders, every job stopped
    assert all(o.side.value != "buy" for o in await broker.get_open_orders())
    assert all(t.done() for t in orch._jobs)
    for p in closed:
        assert p.closed_at is not None and p.exit_reason is not None
