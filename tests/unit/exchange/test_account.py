"""T-105: WallexAccount (respx + fixtures)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.exchange import endpoints as ep
from siavash.exchange.account import OrderIdMap, WallexAccount
from siavash.exchange.client import WallexHttp
from siavash.exchange.errors import BadRequest, BelowMinimum
from siavash.exchange.markets import MarketRegistry
from tests.unit.exchange.conftest import load

BASE = "https://wallex.test"
COID = "SIAV-01JBXYZABCDEFGHJKMNPQRSTVW-E1"


async def _nosleep(_: float) -> None:
    return None


def _req(**kw: object) -> OrderRequest:
    base: dict[str, object] = dict(
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("0.0001369"),
        price=Decimal("7349000000.7"),
        client_order_id=COID,
        decision_id="01JBXYZABCDEFGHJKMNPQRSTVW",
        purpose="entry",
    )
    base.update(kw)
    return OrderRequest(**base)  # type: ignore[arg-type]


@pytest.fixture
def account() -> WallexAccount:
    http = WallexHttp(BASE, "k", public_rate=(1000, 1000), private_rate=(1000, 1000), sleep_fn=_nosleep)
    reg = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
    return WallexAccount(http, reg, OrderIdMap(":memory:"))


@respx.mock(base_url=BASE)
async def test_balances(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(200, json=load("balances")))
    b = await account.get_balances()
    assert b["TMN"].free == Decimal("4000000")


@respx.mock(base_url=BASE)
async def test_place_order_rounds_and_maps(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    route = respx_mock.post(ep.Paths.ORDERS).mock(return_value=httpx.Response(200, json=load("order")))
    order = await account.place_order(_req())
    sent = json.loads(route.calls.last.request.content)
    assert sent["quantity"] == "0.000136"  # rounded down to step
    assert sent["price"] == "7349000000"  # BUY rounded down to tick
    assert sent["client_id"] == COID and sent["symbol"] == "BTCTMN"
    assert order.client_order_id == COID and order.status is OrderStatus.PARTIALLY_FILLED
    assert account.id_map.order_id_for(COID) == "987654321"
    assert account.id_map.client_id_for("987654321") == COID


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_below_minimum_rejected_before_http(
    respx_mock: respx.MockRouter, account: WallexAccount
) -> None:
    route = respx_mock.post(ep.Paths.ORDERS).mock(return_value=httpx.Response(200, json=load("order")))
    with pytest.raises(BelowMinimum):
        await account.place_order(_req(qty=Decimal("0.00001")))
    with pytest.raises(BadRequest):
        await account.place_order(_req(decision_id=""))
    with pytest.raises(BadRequest):
        await account.place_order(_req(price=None))
    assert route.call_count == 0


@respx.mock(base_url=BASE)
async def test_get_and_cancel_via_id_map(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    account.id_map.put(COID, "987654321", "BTC/TMN")
    respx_mock.get("/v1/account/orders/987654321").mock(return_value=httpx.Response(200, json=load("order")))
    o = await account.get_order("BTC/TMN", COID)
    assert o is not None and o.order_id == "987654321"
    canceled = dict(load("order"))
    canceled["result"] = {**canceled["result"], "status": "CANCELED"}
    route = respx_mock.delete(ep.Paths.ORDERS).mock(return_value=httpx.Response(200, json=canceled))
    c = await account.cancel_order("BTC/TMN", client_order_id=COID)
    assert c.status is OrderStatus.CANCELED
    assert json.loads(route.calls.last.request.content) == {"orderId": "987654321"}


@respx.mock(base_url=BASE)
async def test_get_order_fallback_open_orders(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.Paths.OPEN_ORDERS).mock(return_value=httpx.Response(200, json=load("open_orders")))
    o = await account.get_order("BTC/TMN", COID)
    assert o is not None and o.order_id == "987654321"
    assert await account.get_order("BTC/TMN", "SIAV-unknown-E1") is None
    opens = await account.get_open_orders()
    assert len(opens) == 2


@respx.mock(base_url=BASE)
async def test_my_trades_since(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.Paths.MY_TRADES).mock(return_value=httpx.Response(200, json=load("my_trades")))
    fills = await account.get_my_trades(datetime(2026, 11, 5, 9, 16, tzinfo=UTC))
    assert [f.fill_id for f in fills] == ["t-1002"]


def test_id_map_persists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "state" / "id_map.sqlite"
    m = OrderIdMap(p)
    m.put("SIAV-a-E1", None, "BTC/TMN")
    m.put("SIAV-a-E1", "42", "BTC/TMN")
    m.put("SIAV-a-E1", None, "BTC/TMN")  # does not erase the known order id
    m.close()
    m2 = OrderIdMap(p)
    assert m2.order_id_for("SIAV-a-E1") == "42" and m2.known_client_ids() == {"SIAV-a-E1"}
