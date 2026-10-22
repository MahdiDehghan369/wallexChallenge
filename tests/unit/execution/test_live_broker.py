"""T-806: LiveBroker (respx fake exchange) and FakeBroker both pass the Broker contract."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx

from siavash.core.models import OrderRequest
from siavash.core.types import OrderType, Side
from siavash.engine.decision_log import OrdersLogger
from siavash.exchange import endpoints as ep
from siavash.exchange.account import OrderIdMap, WallexAccount
from siavash.exchange.client import WallexHttp
from siavash.exchange.errors import BadRequest
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.execution.live_broker import LiveBroker
from tests.fakes.broker import FakeBroker
from tests.fakes.wallex_server import FakeWallexServer
from tests.unit.exchange.conftest import DENYLIST, load
from tests.unit.execution.broker_contract import BrokerHarness, run_broker_contract

BASE = "https://wallex.test"
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
PRICE, QTY = Decimal("7349000000"), Decimal("0.0002")


async def _nosleep(_: float) -> None:
    return None


def _guard() -> SymbolGuard:
    return SymbolGuard(DENYLIST, REG)


def _live(server: FakeWallexServer, logger: OrdersLogger | None = None) -> LiveBroker:
    http = WallexHttp(BASE, "k", public_rate=(1000, 1000), private_rate=(1000, 1000), sleep_fn=_nosleep)
    return LiveBroker(WallexAccount(http, REG, OrderIdMap(":memory:"), _guard()), logger)


async def test_live_broker_contract() -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        holder: dict[str, FakeWallexServer] = {}
        router.route().mock(side_effect=lambda r: holder["s"].handler(r))

        async def factory() -> BrokerHarness:
            server = FakeWallexServer({"TMN": Decimal("5000000")})
            holder["s"] = server
            broker = _live(server)

            async def fill(coid: str, price: Decimal, qty: Decimal) -> None:
                oid = broker.account.id_map.order_id_for(coid)
                assert oid is not None
                server.fill(oid, price, qty)

            return BrokerHarness(broker, fill, PRICE, QTY)

        await run_broker_contract(factory)


async def test_fake_broker_contract() -> None:
    async def factory() -> BrokerHarness:
        broker = FakeBroker({"TMN": Decimal("5000000")}, fee_pct="0.35", guard=_guard())

        async def fill(coid: str, price: Decimal, qty: Decimal) -> None:
            broker.simulate_fill(coid, price, qty)

        return BrokerHarness(broker, fill, PRICE, QTY)

    await run_broker_contract(factory)


async def test_orders_logged_without_secrets(tmp_path: Path) -> None:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        server = FakeWallexServer({"TMN": Decimal("5000000")})
        router.route().mock(side_effect=server.handler)
        broker = _live(server, OrdersLogger(tmp_path / "orders.jsonl"))
        req = OrderRequest(
            symbol="BTC/TMN",
            side=Side.BUY,
            type=OrderType.LIMIT,
            qty=QTY,
            price=PRICE,
            client_order_id="SIAV-L1-E1",
            decision_id="L1",
            purpose="entry",
        )
        await broker.place_order(req)
        await broker.place_order(req)  # duplicate -> no second POST
        await broker.cancel_order("BTC/TMN", "SIAV-L1-E1")
        assert server.posts == 1
        with pytest.raises(BadRequest):
            await broker.place_order(
                req.model_copy(update={"decision_id": "", "client_order_id": "SIAV-L2-E1"})
            )
    kinds = [json.loads(x)["kind"] for x in (tmp_path / "orders.jsonl").read_text().splitlines()]
    assert kinds == ["request", "response", "duplicate", "cancel_request", "cancel_response"]
    assert "X-API-Key" not in (tmp_path / "orders.jsonl").read_text()
