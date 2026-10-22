"""T-902: HistoricalMarketData + BacktestBroker (+ Broker contract)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from siavash.backtest.broker import BacktestBroker
from siavash.backtest.market_data import HistoricalMarketData
from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side, Timeframe
from siavash.engine.clock import SimClock
from siavash.exchange import endpoints as ep
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from tests.unit.exchange.conftest import DENYLIST, load
from tests.unit.execution.broker_contract import BrokerHarness, run_broker_contract

D = Decimal
T0 = datetime(2026, 3, 1, tzinfo=UTC)
P = D("7349000000")
MARKETS = ep.parse_markets(load("markets"))


def frame(rows: list[tuple[float, float, float, float]], start: datetime = T0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_open": [start + timedelta(minutes=15 * i) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1.0] * len(rows),
        }
    )


def setup(
    rows: list[tuple[float, float, float, float]], at_bar: int = 2
) -> tuple[BacktestBroker, HistoricalMarketData, SimClock]:
    clock = SimClock(T0 + timedelta(minutes=15 * at_bar))
    md = HistoricalMarketData(
        {"BTC/TMN": {Timeframe.M15: frame(rows)}}, clock, markets=MARKETS, spread_pct_fn=lambda s: 0.1
    )
    reg = MarketRegistry.from_markets(MARKETS)
    broker = BacktestBroker(
        md, SymbolGuard(DENYLIST, reg), reg, clock, "0.1", "0.2", 0.05, {"TMN": D("5000000")}
    )
    return broker, md, clock


def req(
    coid: str,
    price: Decimal | None,
    side: Side = Side.BUY,
    typ: OrderType = OrderType.LIMIT,
    qty: str = "0.0002",
) -> OrderRequest:
    return OrderRequest(
        symbol="BTC/TMN",
        side=side,
        type=typ,
        qty=D(qty),
        price=price,
        client_order_id=coid,
        decision_id="D",
        purpose="entry",
    )


FLAT = [(float(P) * 1.001, float(P) * 1.002, float(P) * 0.999, float(P) * 1.001)] * 20


async def test_contract() -> None:
    async def factory() -> BrokerHarness:
        broker, _, clock = setup(FLAT)

        async def fill(coid: str, price: Decimal, qty: Decimal) -> None:
            clock.advance_to(clock.now() + timedelta(minutes=16))
            await broker.get_order("BTC/TMN", coid)

        return BrokerHarness(broker, fill, P, D("0.0002"))

    await run_broker_contract(factory)


async def test_no_fill_in_placement_candle_then_fill_next() -> None:
    rows = (
        [(100.0, 101.0, 99.0, 100.0)] * 2 + [(100.0, 100.5, 95.0, 100.0)] + [(100.0, 100.5, 99.8, 100.0)] * 5
    )
    rows = [(r[0] * 7e7, r[1] * 7e7, r[2] * 7e7, r[3] * 7e7) for r in rows]
    broker, _, clock = setup(rows, at_bar=2)
    clock.advance_to(clock.now() + timedelta(minutes=5))  # mid-candle 2 (the dipping one)
    await broker.place_order(req("SIAV-A-E1", D("6930000000")))  # limit at 99 × 7e7
    clock.advance_to(
        T0 + timedelta(minutes=15 * 3, seconds=1)
    )  # candle 2 closed (it started before placement)
    o = await broker.get_order("BTC/TMN", "SIAV-A-E1")
    assert o is not None and o.status is OrderStatus.NEW
    assert (await broker.get_balances())["TMN"].locked > 0


async def test_aggressive_and_market_fill_at_open_with_slippage() -> None:
    rows = [(float(P), float(P) * 1.01, float(P) * 0.99, float(P))] * 4
    broker, md, _ = setup(rows, at_bar=2)
    ask = await md.get_orderbook("BTC/TMN")
    assert ask.best_ask is not None and ask.spread_pct == pytest.approx(0.1, abs=0.01)
    o = await broker.place_order(req("SIAV-B-E1", P * D("1.002")))  # crosses the ask
    assert o.status is OrderStatus.FILLED and o.avg_fill_price == pytest.approx(P * D("1.001"), rel=D("1e-6"))
    f = broker.fills[-1]
    assert not f.is_maker and f.fee == f.price * f.qty * D("0.002")
    m = await broker.place_order(req("SIAV-B-X1", None, side=Side.SELL, typ=OrderType.MARKET, qty="0.0002"))
    assert m.status is OrderStatus.FILLED and m.avg_fill_price == pytest.approx(P * D("0.999"), rel=D("1e-6"))


async def test_history_never_leaks_future() -> None:
    rows = [(float(i), float(i) + 1, float(i) - 1, float(i)) for i in range(100, 120)]
    _, md, clock = setup(rows, at_bar=5)
    got = await md.get_candles("BTC/TMN", Timeframe.M15, T0, T0 + timedelta(days=1))
    assert len(got) == 5 and got[-1].close == 104.0
    assert md.last_close("BTC/TMN") == 104.0 and md.current_open("BTC/TMN") == 105.0
    t = await md.get_ticker("BTC/TMN")
    assert t.last == D("104.0")
    clock.advance_to(clock.now() + timedelta(minutes=14))
    assert len(await md.get_candles("BTC/TMN", Timeframe.M15, T0, T0 + timedelta(days=1))) == 5
    hmd = HistoricalMarketData.from_15m({"BTC/TMN": frame(rows)}, clock)
    assert (await hmd.get_candles("BTC/TMN", Timeframe.H1, T0, T0 + timedelta(days=1)))[0].high == 104.0
