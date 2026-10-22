"""T-802: idempotent submission."""

from __future__ import annotations

from decimal import Decimal

import pytest

from siavash.core.models import Order, OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.exchange.errors import NetworkError
from siavash.execution.safe_submit import SafeSubmitter, next_attempt, safe_place
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock

D = Decimal


def _req(coid: str = "SIAV-D1-E1") -> OrderRequest:
    return OrderRequest(
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=D("0.001"),
        price=D("100"),
        client_order_id=coid,
        decision_id="D1",
        purpose="entry",
    )


class TimeoutAfterAccept(FakeBroker):
    """The exchange accepts the order, but the response is lost (network timeout)."""

    def __init__(self, visible_after_lookups: int = 1, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.calls = 0
        self.lookups = 0
        self.visible_after = visible_after_lookups

    async def place_order(self, req: OrderRequest) -> Order:
        self.calls += 1
        await super().place_order(req)
        raise NetworkError("read timeout")

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        self.lookups += 1
        if self.lookups < self.visible_after:
            return None
        return await super().get_order(symbol, client_order_id)


class LostOrder(FakeBroker):
    """First submission never reaches the exchange; the second works."""

    def __init__(self, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.calls = 0

    async def place_order(self, req: OrderRequest) -> Order:
        self.calls += 1
        if self.calls == 1:
            raise NetworkError("connect timeout")
        return await super().place_order(req)


def test_next_attempt() -> None:
    assert next_attempt("SIAV-01ABC-E1") == "SIAV-01ABC-E2"
    assert next_attempt("SIAV-01ABC-X9") == "SIAV-01ABC-X10"
    with pytest.raises(ValueError):
        next_attempt("manual")


async def test_timeout_but_order_exists_no_duplicate() -> None:
    clock = FakeClock()
    b = TimeoutAfterAccept(visible_after_lookups=2, balances={"TMN": D("1000")}, clock=clock)
    o = await safe_place(b, _req(), clock, lookup_attempts=3, lookup_interval_s=2)
    assert b.calls == 1 and len(b.placed) == 1
    assert o.client_order_id == "SIAV-D1-E1" and o.status is OrderStatus.NEW
    assert len(await b.get_open_orders()) == 1


async def test_lost_order_is_resent_with_new_attempt() -> None:
    clock = FakeClock()
    b = LostOrder(balances={"TMN": D("1000")}, clock=clock)
    t0 = clock.now()
    o = await SafeSubmitter(clock, 3, 2)(b, _req())
    assert o.client_order_id == "SIAV-D1-E2" and b.calls == 2
    assert (clock.now() - t0).total_seconds() == 6  # three lookups, 2 s apart
    assert [x.client_order_id for x in await b.get_open_orders()] == ["SIAV-D1-E2"]


async def test_found_via_fills_when_already_filled() -> None:
    clock = FakeClock()

    class FilledThenTimeout(FakeBroker):
        async def place_order(self, req: OrderRequest) -> Order:
            await super().place_order(req)
            self.simulate_fill(req.client_order_id)
            raise NetworkError("timeout")

        async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
            return None  # exchange query by client id not supported

    b = FilledThenTimeout(balances={"TMN": D("1000")}, clock=clock)
    o = await safe_place(b, _req(), clock)
    assert o.status is OrderStatus.FILLED and o.filled_qty == D("0.001") and len(b.placed) == 1


async def test_gives_up() -> None:
    clock = FakeClock()

    class AlwaysDown(FakeBroker):
        async def place_order(self, req: OrderRequest) -> Order:
            raise NetworkError("down")

    with pytest.raises(NetworkError):
        await safe_place(AlwaysDown(clock=clock), _req(), clock)


async def test_other_errors_propagate() -> None:
    clock = FakeClock()
    b = FakeBroker(balances={"TMN": D("0")}, clock=clock)
    with pytest.raises(Exception, match="need"):
        await safe_place(b, _req(), clock)
