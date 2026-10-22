"""T-804: reconciliation scenarios."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from siavash.core.models import Candidate, Fill, OrderRequest, RiskDecision
from siavash.core.types import OrderStatus, OrderType, Side, StrategyId
from siavash.engine.decision_log import MemoryDecisionLogger
from siavash.engine.positions import PositionManager
from siavash.engine.store import InMemoryStore
from siavash.exchange import endpoints as ep
from siavash.exchange.markets import MarketRegistry
from siavash.execution.reconcile import reconcile
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import load

D = Decimal
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))


def _req(coid: str, side: Side = Side.BUY, qty: str = "0.0002") -> OrderRequest:
    return OrderRequest(
        symbol="BTC/TMN",
        side=side,
        type=OrderType.LIMIT,
        qty=D(qty),
        price=D("7000000000"),
        client_order_id=coid,
        decision_id="D",
        purpose="entry",
    )


async def _setup(
    balances: dict[str, str],
) -> tuple[FakeBroker, InMemoryStore, PositionManager, FakeClock, MemoryDecisionLogger, list[dict[str, Any]]]:
    clock = FakeClock()
    broker = FakeBroker({k: D(v) for k, v in balances.items()}, clock=clock)
    return broker, InMemoryStore(), PositionManager(InMemoryStore(), clock), clock, MemoryDecisionLogger(), []


async def test_open_orders_ours_and_foreign() -> None:
    broker, store, pm, clock, logger, alerts = await _setup({"TMN": "10000000"})
    await broker.place_order(_req("SIAV-A-E1"))  # ours, unknown to state
    await broker.place_order(_req("manual-123"))  # not from the bot
    rep = await reconcile(
        broker, store, pm, REG, clock, logger, alert=lambda k, d: alerts.append({"kind": k, **d})
    )
    assert rep.adopted == ["SIAV-A-E1"] and rep.foreign == ["manual-123"]
    assert [o.client_order_id for o, _ in await store.load_orders()] == ["SIAV-A-E1"]
    assert alerts[0]["kind"] == "ORDER_NOT_FROM_BOT"
    assert (await broker.get_order("BTC/TMN", "manual-123")).status is OrderStatus.NEW  # type: ignore[union-attr]  # untouched
    assert all(r.outcome_action == "INFO" and r.extra["kind"] == "reconcile" for r in logger.records)
    assert (await reconcile(broker, store, pm, REG, clock)).adopted == []  # idempotent


async def test_state_open_but_exchange_terminal() -> None:
    broker, store, pm, clock, logger, _ = await _setup({"TMN": "10000000"})
    o = await broker.place_order(_req("SIAV-B-E1"))
    await store.save_order(o)
    broker.simulate_fill("SIAV-B-E1")
    rep = await reconcile(broker, store, pm, REG, clock, logger)
    assert [x.status for x in rep.updated] == [OrderStatus.FILLED]
    ((saved, _),) = await store.load_orders()
    assert saved.status is OrderStatus.FILLED


async def _open_position(pm: PositionManager, clock: FakeClock, qty: str) -> str:
    c = Candidate(
        candidate_id="c",
        ts=clock.now(),
        symbol="BTC/TMN",
        strategy=StrategyId.S1_TREND_BREAKOUT,
        entry_ref_price=7e9,
        stop_loss=6.9e9,
        take_profit=7.2e9,
        time_stop_minutes=60,
        expected_move_pct=2.8,
        conditions={},
    )
    p = await pm.create(c, RiskDecision(approved=True, size_tmn=D("1"), qty=D(qty), equity_tmn=D("1")), "D")
    f = Fill(
        fill_id="f",
        order_id="1",
        client_order_id="SIAV-D-E1",
        symbol="BTC/TMN",
        side=Side.BUY,
        price=D("7e9"),
        qty=D(qty),
        fee=D(0),
        fee_asset="TMN",
        ts=clock.now(),
    )
    await pm.apply_entry_fill(p.position_id, f, D(0))
    return p.position_id


async def test_balance_mismatch_reduce_and_orphan() -> None:
    broker, store, pm, clock, logger, _ = await _setup({"TMN": "0", "BTC": "0.0001", "ETH": "0.05"})
    pid = await _open_position(pm, clock, "0.0003")  # holds more than the balance
    rep = await reconcile(broker, store, pm, REG, clock, logger)
    assert rep.reduced == {pid: D("0.0001")} and pm.get(pid).qty == D("0.0001")
    assert rep.orphans == {"ETH": D("0.05")}  # coin without a position
    assert await store.kv_get("orphan_balances") == {"ETH": "0.05"}


async def test_small_difference_tolerated() -> None:
    broker, store, pm, clock, _, _ = await _setup({"TMN": "0", "BTC": "0.0003"})
    await _open_position(pm, clock, "0.0002999999")  # within max(min_qty, 0.5 %)
    rep = await reconcile(broker, store, pm, REG, clock)
    assert rep.clean
