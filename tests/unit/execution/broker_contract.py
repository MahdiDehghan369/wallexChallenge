"""Shared Broker contract (T-806). Every Broker implementation must pass ``run_broker_contract``.

A harness provides the broker plus a way to make a resting LIMIT order fill (``fill``), because how
fills happen differs per broker (fake exchange, paper trade feed, backtest candles).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from siavash.core.interfaces import Broker
from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side

BASE_SYMBOL = "BTC/TMN"
BANNED_SYMBOL = "TON/TMN"


@dataclass
class BrokerHarness:
    broker: Broker
    fill: Callable[[str, Decimal, Decimal], Awaitable[None]]  # (client_order_id, price, qty)
    price: Decimal  # a valid limit price for BASE_SYMBOL
    qty: Decimal  # a valid qty (above minimums)


def req(
    coid: str,
    h: BrokerHarness,
    symbol: str = BASE_SYMBOL,
    decision_id: str = "D-contract",
    side: Side = Side.BUY,
) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=side,
        type=OrderType.LIMIT,
        qty=h.qty,
        price=h.price,
        client_order_id=coid,
        decision_id=decision_id,
        purpose="entry",
    )


async def run_broker_contract(factory: Callable[[], Awaitable[BrokerHarness]]) -> None:
    """Place/cancel/query, idempotency, banned market, missing decision_id, balances and fees after a fill."""
    # 1) place, query, open orders
    h = await factory()
    b = h.broker
    since = datetime(2000, 1, 1, tzinfo=UTC)
    o = await b.place_order(req("SIAV-C1-E1", h))
    assert o.client_order_id == "SIAV-C1-E1" and o.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)
    got = await b.get_order(BASE_SYMBOL, "SIAV-C1-E1")
    assert got is not None and got.client_order_id == "SIAV-C1-E1"
    assert [x.client_order_id for x in await b.get_open_orders(BASE_SYMBOL)] == ["SIAV-C1-E1"]

    # 2) idempotency: same client id -> same order, still exactly one open order
    o2 = await b.place_order(req("SIAV-C1-E1", h))
    assert o2.order_id == o.order_id
    assert len(await b.get_open_orders()) == 1

    # 3) cancel
    c = await b.cancel_order(BASE_SYMBOL, "SIAV-C1-E1")
    assert c.status is OrderStatus.CANCELED
    assert await b.get_open_orders() == []

    # 4) banned market and 5) missing decision_id are rejected and create nothing
    with pytest.raises(Exception):  # noqa: B017 - each broker raises its own error type
        await b.place_order(req("SIAV-C2-E1", h, symbol=BANNED_SYMBOL))
    with pytest.raises(Exception):  # noqa: B017
        await b.place_order(req("SIAV-C3-E1", h, decision_id=""))
    assert await b.get_open_orders() == []
    assert await b.get_order(BANNED_SYMBOL, "SIAV-C2-E1") is None

    # 6) fill -> balances and fees
    h = await factory()
    b = h.broker
    before = await b.get_balances()
    quote_before = before["TMN"].total
    base_before = before["BTC"].total if "BTC" in before else Decimal(0)
    await b.place_order(req("SIAV-C4-E1", h))
    await h.fill("SIAV-C4-E1", h.price, h.qty)
    order = await b.get_order(BASE_SYMBOL, "SIAV-C4-E1")
    assert order is not None and order.status is OrderStatus.FILLED and order.filled_qty == h.qty
    fills = [f for f in await b.get_fills(since) if f.client_order_id == "SIAV-C4-E1"]
    assert len(fills) == 1 and fills[0].qty == h.qty and fills[0].fee > 0
    after = await b.get_balances()
    assert after["BTC"].total - base_before == h.qty
    fee_quote = fills[0].fee if fills[0].fee_asset == "TMN" else fills[0].fee * fills[0].price
    assert quote_before - after["TMN"].total == pytest.approx(h.qty * h.price + fee_quote)
    assert await b.get_open_orders() == []
