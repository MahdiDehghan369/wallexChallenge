"""T-803: OrderTracker."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from siavash.core.models import Fill, Order, OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.engine.decision_log import FillsCsvLogger
from siavash.execution.tracker import OrderTracker, TrackMeta
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock

D = Decimal


def _req(coid: str, symbol: str = "BTC/TMN", price: str = "100") -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=D("2"),
        price=D(price),
        client_order_id=coid,
        decision_id="D1",
        purpose="entry",
    )


async def test_fills_events_dedupe_and_csv(tmp_path: Path) -> None:
    clock = FakeClock()
    broker = FakeBroker({"TMN": D("10000")}, fee_pct="0.5", clock=clock)
    tracker = OrderTracker(broker, clock, FillsCsvLogger(tmp_path / "fills.csv"))
    got: list[tuple[Fill, Decimal]] = []
    terminal: list[Order] = []
    tracker.on_fill.append(lambda f, fee, meta: got.append((f, fee)))

    async def on_term(o: Order, meta: TrackMeta | None) -> None:
        terminal.append(o)

    tracker.on_order_terminal.append(on_term)
    o = await broker.place_order(_req("SIAV-D1-E1"))
    tracker.track(o, TrackMeta("D1", "P1", "S1_TREND_BREAKOUT"))
    clock.advance(1)
    broker.simulate_fill("SIAV-D1-E1", D("99"), D("1"))
    assert len(await tracker.poll_once()) == 1
    assert await tracker.poll_once() == []  # dedupe
    assert got[0][1] == D("99") * D("0.005") and terminal == []
    clock.advance(1)
    broker.simulate_fill("SIAV-D1-E1", D("99"), D("1"))
    await tracker.poll_once()
    assert len(got) == 2 and len(terminal) == 1 and terminal[0].status is OrderStatus.FILLED
    assert tracker.open_client_ids() == []
    with (tmp_path / "fills.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert [r["position_id"] for r in rows] == ["P1", "P1"] and rows[0]["notional_tmn"] == "99"
    assert rows[0]["strategy"] == "S1_TREND_BREAKOUT"


async def test_cancel_is_terminal_and_foreign_fills_ignored() -> None:
    clock = FakeClock()
    broker = FakeBroker({"TMN": D("10000")}, clock=clock)
    tracker = OrderTracker(broker, clock)
    terminal: list[Order] = []
    tracker.on_order_terminal.append(lambda o, m: terminal.append(o))
    o = await broker.place_order(_req("SIAV-D1-E1"))
    tracker.track(o)
    await broker.place_order(_req("manual-1"))  # not ours, not tracked
    broker.simulate_fill("manual-1")
    await broker.cancel_order("BTC/TMN", "SIAV-D1-E1")
    fills = await tracker.poll_once()
    assert fills == [] and terminal[0].status is OrderStatus.CANCELED


def test_fee_conversion() -> None:
    clock = FakeClock()
    tracker = OrderTracker(
        FakeBroker(clock=clock), clock, price_fn=lambda s: D("100000") if s == "USDT/TMN" else None
    )
    assert tracker.to_tmn(D("0.001"), "BTC", "BTC/TMN", D("7000000000")) == D("7000000")
    assert tracker.to_tmn(D("5"), "TMN", "BTC/TMN", D("7000000000")) == D("5")
    assert tracker.to_tmn(D("2"), "USDT", "BTC/USDT", D("70000")) == D("200000")
    assert tracker.to_tmn(D("0.0001"), "BTC", "BTC/USDT", D("70000")) == D("700000")
