"""T-1001: PaperBroker."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.core.models import OrderBook, OrderBookLevel, OrderRequest, PublicTrade
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.exchange import endpoints as ep
from siavash.exchange.errors import InsufficientBalance, SymbolNotAllowed
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.execution.paper_broker import PaperBroker
from tests.fakes.clock import FakeClock
from tests.fakes.market_data import InMemoryMarketData
from tests.unit.exchange.conftest import DENYLIST, load
from tests.unit.execution.broker_contract import BrokerHarness, run_broker_contract

REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
D = Decimal


def _lv(p: str, q: str) -> OrderBookLevel:
    return OrderBookLevel(price=D(p), qty=D(q))


def _setup(
    tmp_path: Path, balances: dict[str, str] | None = None, name: str = "paper.sqlite"
) -> tuple[PaperBroker, InMemoryMarketData, FakeClock]:
    clock = FakeClock()
    md = InMemoryMarketData(clock=clock)
    md.set_book(
        OrderBook(
            symbol="BTC/TMN",
            ts=clock.now(),
            bids=[_lv("7348000000", "0.001"), _lv("7347000000", "0.002")],
            asks=[_lv("7351000000", "0.0001"), _lv("7352000000", "0.0002"), _lv("7360000000", "1")],
        )
    )
    broker = PaperBroker(
        md,
        SymbolGuard(DENYLIST, REG),
        REG,
        clock,
        "0.1",
        "0.2",
        {k: D(v) for k, v in (balances or {"TMN": "5000000"}).items()},
        tmp_path / name,
    )
    return broker, md, clock


def _req(
    coid: str,
    side: Side = Side.BUY,
    typ: OrderType = OrderType.LIMIT,
    qty: str = "0.0002",
    price: str | None = "7349000000",
) -> OrderRequest:
    return OrderRequest(
        symbol="BTC/TMN",
        side=side,
        type=typ,
        qty=D(qty),
        price=D(price) if price else None,
        client_order_id=coid,
        decision_id="D1",
        purpose="entry",
    )


def _trade(md: InMemoryMarketData, clock: FakeClock, price: str, qty: str) -> None:
    clock.advance(1)
    md.add_trades(
        "BTC/TMN", [PublicTrade(symbol="BTC/TMN", ts=clock.now(), price=D(price), qty=D(qty), side=Side.SELL)]
    )


async def test_contract(tmp_path: Path) -> None:
    n = 0

    async def factory() -> BrokerHarness:
        nonlocal n
        n += 1
        broker, md, clock = _setup(tmp_path, name=f"c{n}.sqlite")

        async def fill(coid: str, price: Decimal, qty: Decimal) -> None:
            _trade(md, clock, str(price - 1), str(qty))
            await broker.check_open_orders()

        return BrokerHarness(broker, fill, D("7349000000"), D("0.0002"))

    await run_broker_contract(factory)


async def test_market_buy_walks_book(tmp_path: Path) -> None:
    broker, _, _ = _setup(tmp_path)
    o = await broker.place_order(_req("SIAV-M-E1", typ=OrderType.MARKET, price=None, qty="0.0003"))
    assert o.status is OrderStatus.FILLED
    vwap = (D("7351000000") * D("0.0001") + D("7352000000") * D("0.0002")) / D("0.0003")
    assert o.avg_fill_price == vwap
    fills = await broker.get_fills(o.created_at)
    assert all(not f.is_maker for f in fills) and fills[0].fee == D("7351000000") * D("0.0001") * D("0.002")
    bal = await broker.get_balances()
    cost = sum(f.price * f.qty + f.fee for f in fills)
    assert bal["TMN"].free == D("5000000") - cost and bal["BTC"].free == D("0.0003")


async def test_limit_crossing_fills_as_taker(tmp_path: Path) -> None:
    broker, _, _ = _setup(tmp_path)
    o = await broker.place_order(_req("SIAV-X-E1", qty="0.0002", price="7351500000"))
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_qty == D(
        "0.0001"
    )  # only the 7351 level crosses
    assert o.avg_fill_price == D("7351000000")


async def test_trades_before_placement_ignored_and_partial(tmp_path: Path) -> None:
    broker, md, clock = _setup(tmp_path)
    _trade(md, clock, "7340000000", "1")  # before placement
    await broker.place_order(_req("SIAV-T-E1"))
    await broker.check_open_orders()
    assert (await broker.get_order("BTC/TMN", "SIAV-T-E1")).filled_qty == 0  # type: ignore[union-attr]
    _trade(md, clock, "7349000000", "1")  # equal price: no fill (must trade *below*)
    _trade(md, clock, "7348500000", "0.00005")  # partial
    await broker.check_open_orders()
    o = await broker.get_order("BTC/TMN", "SIAV-T-E1")
    assert o is not None and o.filled_qty == D("0.00005") and o.status is OrderStatus.PARTIALLY_FILLED
    f = (await broker.get_fills(o.created_at))[-1]
    assert f.is_maker and f.price == D("7349000000") and f.fee == D("7349000000") * D("0.00005") * D("0.001")


async def test_sell_limit_and_guard_and_funds(tmp_path: Path) -> None:
    broker, md, clock = _setup(tmp_path, {"TMN": "1000", "BTC": "0.001"})
    with pytest.raises(InsufficientBalance):
        await broker.place_order(_req("SIAV-F-E1"))
    with pytest.raises(SymbolNotAllowed):
        await broker.place_order(_req("SIAV-F-E2").model_copy(update={"symbol": "TON/TMN"}))
    await broker.place_order(_req("SIAV-S-X1", side=Side.SELL, qty="0.0005", price="7355000000"))
    assert (await broker.get_balances())["BTC"].locked == D("0.0005")
    _trade(md, clock, "7356000000", "1")
    await broker.check_open_orders()
    o = await broker.get_order("BTC/TMN", "SIAV-S-X1")
    assert o is not None and o.status is OrderStatus.FILLED
    bal = await broker.get_balances()
    assert bal["BTC"].total == D("0.0005") and bal["TMN"].free > D("1000")


async def test_state_survives_restart(tmp_path: Path) -> None:
    broker, md, clock = _setup(tmp_path)
    await broker.place_order(_req("SIAV-R-E1"))
    _trade(md, clock, "7340000000", "0.0001")
    await broker.check_open_orders()
    broker.close()
    clock.advance(timedelta(minutes=5).total_seconds())
    b2 = PaperBroker(
        md, SymbolGuard(DENYLIST, REG), REG, clock, "0.1", "0.2", None, tmp_path / "paper.sqlite"
    )
    o = await b2.get_order("BTC/TMN", "SIAV-R-E1")
    assert o is not None and o.filled_qty == D("0.0001")
    assert len(await b2.get_fills(o.created_at)) == 1
    assert (await b2.get_balances())["BTC"].free == D("0.0001")
    # the already-consumed trade is not filled twice
    await b2.check_open_orders()
    assert (await b2.get_order("BTC/TMN", "SIAV-R-E1")).filled_qty == D("0.0001")  # type: ignore[union-attr]
