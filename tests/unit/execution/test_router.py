"""T-801: OrderRouter with FakeBroker + FakeClock."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.config import AppConfig, load_config
from siavash.core.models import Order, OrderBook, OrderBookLevel, Position
from siavash.core.types import ExitReason, OrderStatus, PositionStatus, Side, StrategyId
from siavash.exchange import endpoints as ep
from siavash.exchange.markets import MarketRegistry
from siavash.execution.router import OrderRouter
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import load

D = Decimal
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
ROOT = Path(__file__).resolve().parents[3]

Policy = Callable[[Order, int], tuple[Decimal, Decimal] | None]


class AutoFillBroker(FakeBroker):
    """Fills resting orders according to ``policy(order, poll_no) -> (price, qty) | None``."""

    def __init__(self, policy: Policy, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kw)
        self.policy = policy
        self.polls: dict[str, int] = {}

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        o = self.orders.get(client_order_id)
        if o is not None and not o.status.is_terminal:
            n = self.polls[client_order_id] = self.polls.get(client_order_id, 0) + 1
            r = self.policy(o, n)
            if r is not None:
                self.simulate_fill(client_order_id, *r)
        return self.orders.get(client_order_id)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(ROOT / "config", env_file=None)


def book(bid: str = "7349000000", ask: str = "7352000000") -> OrderBook:
    clock = FakeClock()
    return OrderBook(
        symbol="BTC/TMN",
        ts=clock.now(),
        bids=[OrderBookLevel(price=D(bid), qty=D("1"))],
        asks=[OrderBookLevel(price=D(ask), qty=D("1"))],
    )


def position(clock: FakeClock) -> Position:
    return Position(
        position_id="P1",
        symbol="BTC/TMN",
        strategy=StrategyId.S1_TREND_BREAKOUT,
        status=PositionStatus.PENDING_ENTRY,
        entry_decision_id="DEC1",
        stop_loss=D("7200000000"),
        take_profit=D("7500000000"),
        time_stop_at=clock.now() + timedelta(hours=12),
    )


async def _valid() -> bool:
    return True


async def _invalid() -> bool:
    return False


def _router(
    cfg: AppConfig, policy: Policy, balances: dict[str, str] | None = None, book_fn=None
) -> tuple[OrderRouter, AutoFillBroker, FakeClock]:  # type: ignore[no-untyped-def]
    clock = FakeClock()
    broker = AutoFillBroker(
        policy,
        balances={k: D(v) for k, v in (balances or {"TMN": "5000000"}).items()},
        fee_pct="0.35",
        clock=clock,
    )
    return OrderRouter(broker, REG, clock, cfg, book_fn=book_fn), broker, clock


async def test_maker_fill(cfg: AppConfig) -> None:
    router, broker, clock = _router(cfg, lambda o, n: (o.price, o.qty) if n == 2 else None)  # type: ignore[arg-type,return-value]
    orders = await router.enter(position(clock), D("0.0003"), book(), _valid)
    assert len(orders) == 1 and orders[0].status is OrderStatus.FILLED
    assert orders[0].price == D("7349000001")  # one tick better than the bid
    assert orders[0].client_order_id == "SIAV-DEC1-E1"
    assert broker.placed[0].decision_id == "DEC1"


async def test_aggressive_fallback(cfg: AppConfig) -> None:
    def policy(o: Order, n: int) -> tuple[Decimal, Decimal] | None:
        return (o.price, o.qty) if o.client_order_id.endswith("E2") else None  # type: ignore[return-value]

    router, broker, clock = _router(cfg, policy)
    t0 = clock.now()
    orders = await router.enter(position(clock), D("0.0003"), book(), _valid)
    assert [o.status for o in orders] == [OrderStatus.CANCELED, OrderStatus.FILLED]
    assert orders[1].price == D("7363028000")  # 7352000000 × 1.0015, rounded down
    assert clock.now() - t0 >= timedelta(seconds=90)
    assert (await broker.get_balances())["TMN"].locked == 0


async def test_partial_then_signal_invalid(cfg: AppConfig) -> None:
    router, broker, clock = _router(cfg, lambda o, n: (o.price, D("0.0001")) if n == 1 else None)  # type: ignore[arg-type,return-value]
    orders = await router.enter(position(clock), D("0.0003"), book(), _invalid)
    assert (
        len(orders) == 1 and orders[0].status is OrderStatus.CANCELED and orders[0].filled_qty == D("0.0001")
    )
    assert len(broker.placed) == 1


async def test_exit_three_attempts(cfg: AppConfig) -> None:
    books = iter([book("7300000000", "7301000000"), book("7290000000", "7291000000")])

    async def book_fn(_: str) -> OrderBook:
        return next(books)

    def policy(o: Order, n: int) -> tuple[Decimal, Decimal] | None:
        return (o.price, o.qty) if o.client_order_id.endswith("X3") else None  # type: ignore[return-value]

    router, _, clock = _router(cfg, policy, {"TMN": "0", "BTC": "0.001"}, book_fn)
    p = position(clock)
    orders = await router.exit(
        p, D("0.0003"), ExitReason.STOP_LOSS, book("7310000000", "7311000000"), decision_id="EXIT1"
    )
    assert [o.client_order_id for o in orders] == ["SIAV-EXIT1-X1", "SIAV-EXIT1-X2", "SIAV-EXIT1-X3"]
    assert [o.status for o in orders] == [OrderStatus.CANCELED, OrderStatus.CANCELED, OrderStatus.FILLED]
    assert orders[0].price == D("7288070000")  # 7310000000 × 0.997, rounded up for SELL
    assert orders[2].price == D("7268130000")
    assert all(o.side is Side.SELL for o in orders)


async def test_take_profit_passive_then_fallback(cfg: AppConfig) -> None:
    def policy(o: Order, n: int) -> tuple[Decimal, Decimal] | None:
        return (o.price, o.qty) if o.client_order_id.endswith("X2") else None  # type: ignore[return-value]

    router, _, clock = _router(cfg, policy, {"TMN": "0", "BTC": "0.001"})
    orders = await router.exit(position(clock), D("0.0002"), ExitReason.TAKE_PROFIT, book(), decision_id="TP")
    assert orders[0].price == D("7500000000")  # max(best ask, take_profit)
    assert orders[-1].status is OrderStatus.FILLED and len(orders) == 2


async def test_tp1_leg_and_min_notional_skip(cfg: AppConfig) -> None:
    router, broker, clock = _router(cfg, lambda o, n: (o.price, o.qty), {"TMN": "0", "BTC": "0.001"})  # type: ignore[arg-type,return-value]
    orders = await router.exit(
        position(clock),
        D("0.0002"),
        ExitReason.TP1_PARTIAL,
        book(),
        decision_id="T1",
        target_price=D("7400000000"),
    )
    assert orders[0].client_order_id == "SIAV-T1-T1" and broker.placed[0].purpose == "tp1"
    assert (
        await router.exit(position(clock), D("0.000001"), ExitReason.STOP_LOSS, book(), decision_id="tiny")
        == []
    )
