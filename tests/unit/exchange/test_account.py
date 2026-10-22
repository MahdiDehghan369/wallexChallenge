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
from siavash.exchange.errors import BadRequest, BelowMinimum, SymbolNotAllowed
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from tests.unit.exchange.conftest import DENYLIST, load

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
    guard = SymbolGuard(DENYLIST, reg, allowed_quotes=["TMN"], allow_usdt_liquidation=True)
    return WallexAccount(http, reg, OrderIdMap(":memory:"), guard)


@respx.mock(base_url=BASE, assert_all_called=False)
async def test_guard_blocks_before_http(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    route = respx_mock.post(ep.Paths.ORDERS).mock(return_value=httpx.Response(200, json=load("order")))
    for sym in ("TON/TMN", "NEAR/TMN", "BTC/USDT", "XRP/TMN"):
        with pytest.raises(SymbolNotAllowed):
            await account.place_order(_req(symbol=sym, price=Decimal("500000"), qty=Decimal("1")))
    with pytest.raises(SymbolNotAllowed):
        await account.place_order(_req(symbol="USDT/TMN", price=Decimal("102500"), qty=Decimal("10")))
    assert route.call_count == 0


@respx.mock(base_url=BASE)
async def test_balances(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(200, json=load("balances")))
    b = await account.get_balances()
    assert b["TMN"].free == Decimal("4000000")


@respx.mock(base_url=BASE)
async def test_place_order_rounds_and_maps(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    route = respx_mock.post(ep.Paths.ORDERS).mock(return_value=httpx.Response(201, json=load("order")))
    order = await account.place_order(_req())
    sent = json.loads(route.calls.last.request.content)
    assert sent["quantity"] == "0.000136"  # rounded down to step
    assert sent["price"] == "7349000000"  # BUY rounded down to tick
    assert sent["client_id"] == COID and sent["symbol"] == "BTCTMN"
    assert sent["side"] == "BUY" and sent["type"] == "LIMIT"
    assert order.client_order_id == COID and order.status is OrderStatus.PARTIALLY_FILLED
    assert COID in account.id_map.known_client_ids()


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
async def test_get_and_cancel_by_client_id(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.order_path(COID)).mock(return_value=httpx.Response(200, json=load("order")))
    o = await account.get_order("BTC/TMN", COID)
    assert o is not None and o.client_order_id == COID and o.order_id == COID
    canceled = dict(load("order"))
    canceled["result"] = {**canceled["result"], "status": "CANCELED", "active": False}
    route = respx_mock.delete(ep.order_path(COID)).mock(return_value=httpx.Response(200, json=canceled))
    c = await account.cancel_order("BTC/TMN", client_order_id=COID)
    assert c.status is OrderStatus.CANCELED and route.call_count == 1
    assert route.calls.last.request.headers[ep.AUTH_HEADER] == "k"


@respx.mock(base_url=BASE)
async def test_get_order_unknown_and_open_orders(
    respx_mock: respx.MockRouter, account: WallexAccount
) -> None:
    respx_mock.get(ep.order_path("SIAV-unknown-E1")).mock(
        return_value=httpx.Response(
            404, json={"success": False, "code": 404, "message": "Order not found", "result": {}}
        )
    )
    respx_mock.get(ep.Paths.OPEN_ORDERS).mock(return_value=httpx.Response(200, json=load("open_orders")))
    assert await account.get_order("BTC/TMN", "SIAV-unknown-E1") is None
    opens = await account.get_open_orders()
    assert len(opens) == 2 and opens[0].client_order_id == COID


@respx.mock(base_url=BASE)
async def test_my_trades_from_order_fills(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    history = respx_mock.get(ep.Paths.ORDERS).mock(
        return_value=httpx.Response(200, json=load("orders_history"))
    )
    respx_mock.get(ep.Paths.OPEN_ORDERS).mock(return_value=httpx.Response(200, json=load("open_orders")))
    sell_cid = "SIAV-01JBXYZABCDEFGHJKMNPQRSTVW-X1"
    detail = {
        "success": True,
        "result": {
            "clientOrderId": sell_cid,
            "symbol": "BTCTMN",
            "type": "MARKET",
            "side": "SELL",
            "origQty": "0.000136",
            "executedQty": "0.000136",
            "status": "FILLED",
            "active": False,
            "fills": [
                {
                    "price": "7400000000",
                    "quantity": "0.000136",
                    "fee": "3522",
                    "feeAsset": "TMN",
                    "isBuyer": False,
                    "timestamp": "2026-11-05T11:00:01Z",
                }
            ],
        },
    }
    respx_mock.get(ep.order_path(sell_cid)).mock(return_value=httpx.Response(200, json=detail))
    since = datetime(2026, 11, 5, 9, 16, tzinfo=UTC)
    fills = await account.get_my_trades(since)
    assert [f.qty for f in fills] == [Decimal("0.000086"), Decimal("0.000136")]
    assert fills[1].side is Side.SELL and fills[1].fee_asset == "TMN"
    params = history.calls.last.request.url.params
    assert params["from"] == "2026-11-04" and params["per_page"] == "100" and params["page"] == "1"
    again = await account.get_my_trades(since)  # dedupe is stable across calls
    assert [f.fill_id for f in again] == [f.fill_id for f in fills]


@respx.mock(base_url=BASE)
async def test_my_trades_pages_until_short_page(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    one = load("orders_history")["result"]["orders"][0]
    full = {"success": True, "result": {"orders": [one] * ep.ORDERS_PER_PAGE}}
    pages = iter(
        [
            httpx.Response(200, json=full),
            httpx.Response(200, json={"success": True, "result": {"orders": []}}),
        ]
    )
    history = respx_mock.get(ep.Paths.ORDERS).mock(side_effect=lambda req: next(pages))
    respx_mock.get(ep.Paths.OPEN_ORDERS).mock(
        return_value=httpx.Response(200, json={"success": True, "result": {}})
    )
    fills = await account.get_my_trades(datetime(2026, 11, 5, tzinfo=UTC))
    assert history.call_count == 2 and len(fills) == 2  # 100 copies of the same order -> 2 unique fills


@respx.mock(base_url=BASE)
async def test_fees(respx_mock: respx.MockRouter, account: WallexAccount) -> None:
    respx_mock.get(ep.Paths.FEE).mock(return_value=httpx.Response(200, json=load("fee")))
    assert (await account.get_fees())["BTC/TMN"] == (Decimal("0.2"), Decimal("0.2"))


def test_id_map_persists(tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "state" / "id_map.sqlite"
    m = OrderIdMap(p)
    m.put("SIAV-a-E1", None, "BTC/TMN")
    m.put("SIAV-a-E1", "42", "BTC/TMN")
    m.put("SIAV-a-E1", None, "BTC/TMN")  # does not erase the known order id
    m.close()
    m2 = OrderIdMap(p)
    assert m2.order_id_for("SIAV-a-E1") == "42" and m2.known_client_ids() == {"SIAV-a-E1"}
