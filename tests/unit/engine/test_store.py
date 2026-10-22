"""T-1101: SqliteStore persistence (and InMemoryStore parity)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.core.models import Order, Position
from siavash.core.types import BreakerState, OrderStatus, OrderType, PositionStatus, Side, StrategyId
from siavash.engine.store import InMemoryStore, SqliteStore

T0 = datetime(2026, 1, 1, tzinfo=UTC)
D = Decimal


def _pos() -> Position:
    return Position(
        position_id="P1",
        symbol="BTC/TMN",
        strategy=StrategyId.S3_IMPLIED_DEV,
        status=PositionStatus.OPEN,
        entry_decision_id="D1",
        qty=D("0.001"),
        avg_entry=D("7000000000"),
        stop_loss=D("6900000000"),
        take_profit=D("7100000000"),
        time_stop_at=T0 + timedelta(hours=3),
        order_client_ids=["SIAV-D1-E1"],
    )


def _order() -> Order:
    return Order(
        order_id="9",
        client_order_id="SIAV-D1-E1",
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        price=D("7000000000"),
        qty=D("0.001"),
        status=OrderStatus.NEW,
        created_at=T0,
        updated_at=T0,
    )


async def _fill(store: SqliteStore | InMemoryStore) -> None:
    await store.save_position(_pos())
    await store.save_order(_order(), {"decision_id": "D1", "position_id": "P1"})
    await store.put_id("SIAV-D1-E1", None, "BTC/TMN")
    await store.put_id("SIAV-D1-E1", "9", "BTC/TMN")
    await store.put_id("SIAV-D1-E1", None, "BTC/TMN")
    await store.set_breaker(BreakerState.NO_NEW_ENTRIES, "daily loss", T0)
    await store.kv_set("peak_equity", D("5100000"))
    await store.kv_set("cooldowns", {"BTC/TMN": T0.isoformat()})
    for i in range(3):
        await store.add_equity(T0 + timedelta(minutes=5 * i), D("5000000") + i)


async def _check(store: SqliteStore | InMemoryStore) -> None:
    (p,) = await store.load_positions()
    assert p == _pos()
    ((o, meta),) = await store.load_orders()
    assert o == _order() and meta["position_id"] == "P1"
    assert await store.get_order_id("SIAV-D1-E1") == "9"
    assert await store.get_breaker() == (BreakerState.NO_NEW_ENTRIES, "daily loss", T0)
    assert D(await store.kv_get("peak_equity")) == D("5100000")
    assert (await store.kv_get("cooldowns"))["BTC/TMN"] == T0.isoformat()
    assert await store.kv_get("missing", 7) == 7
    series = await store.equity_series(T0 + timedelta(minutes=5))
    assert [e for _, e in series] == [D("5000001"), D("5000002")]


async def test_sqlite_write_close_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state" / "state.sqlite"
    s = SqliteStore(path)
    await s.open()
    await _fill(s)
    await s.close()
    s2 = SqliteStore(path)
    await _check(s2)  # opens lazily
    await s2.close()


@pytest.mark.parametrize("factory", [InMemoryStore])
async def test_in_memory_same_interface(factory: type[InMemoryStore]) -> None:
    s = factory()
    await _fill(s)
    await _check(s)
