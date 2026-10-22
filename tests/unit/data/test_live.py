"""T-203: LiveDataService with FakeClock + InMemoryMarketData."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from siavash.core.models import Candle, OrderBook, OrderBookLevel
from siavash.core.types import Timeframe
from siavash.data.live import LiveDataService
from tests.fakes.clock import FakeClock
from tests.fakes.market_data import InMemoryMarketData
from tests.fakes.synthetic import SYN_START, make_synthetic_market, to_candles


def _setup(
    start_offset: timedelta = timedelta(days=2), drop: set[int] | None = None
) -> tuple[FakeClock, InMemoryMarketData]:
    m = make_synthetic_market(seed=9, days=4)
    c15 = to_candles(m["BTC/TMN"], "BTC/TMN", "15m")
    if drop:
        c15 = [c for i, c in enumerate(c15) if i not in drop]
    clock = FakeClock(SYN_START + start_offset + timedelta(seconds=5))
    md = InMemoryMarketData(candles={("BTC/TMN", Timeframe.M15): c15}, clock=clock)
    return clock, md


async def test_four_consecutive_closes_no_duplicates(tmp_path: Path) -> None:
    clock, md = _setup()
    svc = LiveDataService(md, clock, ["BTC/TMN"], tfs=[Timeframe.M15], buffer_bars=100, store_dir=tmp_path)
    seen: list[datetime] = []
    svc.subscribe(lambda s, tf, c: seen.append(c.ts_open))
    await svc.bootstrap()
    assert len(svc.candles("BTC/TMN", Timeframe.M15)) == 100
    last_boot = svc.candles("BTC/TMN", Timeframe.M15)[-1].ts_open
    assert await svc.poll_candles() == 0  # nothing new yet
    for _ in range(4):
        clock.advance(15 * 60)
        assert await svc.poll_candles() == 1
        assert await svc.poll_candles() == 0  # repeated poll: no duplicate
    assert seen == [last_boot + timedelta(minutes=15 * (i + 1)) for i in range(4)]
    assert svc.events.qsize() == 4
    assert len(svc.candles("BTC/TMN", Timeframe.M15)) == 100  # ring buffer
    files = list(tmp_path.rglob("candles_15m.jsonl"))
    lines = [json.loads(x) for f in files for x in f.read_text().splitlines()]
    assert len(lines) == 4


async def test_async_callback_and_df() -> None:
    clock, md = _setup()
    svc = LiveDataService(md, clock, ["BTC/TMN"], tfs=[Timeframe.M15], buffer_bars=50)
    got: list[Candle] = []

    async def cb(sym: str, tf: Timeframe, c: Candle) -> None:
        await asyncio.sleep(0)
        got.append(c)

    svc.subscribe(cb)
    await svc.bootstrap()
    clock.advance(30 * 60)
    assert await svc.poll_candles() == 2 and len(got) == 2
    df = svc.candles_df("BTC/TMN", Timeframe.M15)
    assert len(df) == 50 and str(df["ts_open"].dt.tz) == "UTC"


async def test_missing_bucket_filled_only_after_later_candle() -> None:
    # bar index 2*96 + 1 (just after bootstrap end) is missing in the source
    clock, md = _setup(drop={2 * 96 + 1})
    svc = LiveDataService(md, clock, ["BTC/TMN"], tfs=[Timeframe.M15], buffer_bars=20)
    await svc.bootstrap()
    clock.advance(15 * 60)
    assert await svc.poll_candles() == 1
    clock.advance(15 * 60)
    assert await svc.poll_candles() == 0  # missing bucket: wait, do not invent it yet
    clock.advance(15 * 60)
    assert await svc.poll_candles() == 2  # later candle arrived -> filled gap + real candle
    buf = svc.candles("BTC/TMN", Timeframe.M15)
    assert buf[-2].is_filled and buf[-2].volume == 0 and buf[-2].close == buf[-3].close
    assert not buf[-1].is_filled


async def test_stale_detection_and_books() -> None:
    clock, md = _setup()
    md.set_book(
        OrderBook(
            symbol="BTC/TMN",
            ts=clock.now(),
            bids=[OrderBookLevel(price=Decimal("99"), qty=Decimal("1"))],
            asks=[OrderBookLevel(price=Decimal("101"), qty=Decimal("1"))],
        )
    )
    svc = LiveDataService(
        md, clock, ["BTC/TMN", "ETH/TMN"], tfs=[Timeframe.M15], buffer_bars=10, stale_after_s=180
    )
    assert svc.is_stale("BTC/TMN")
    await svc.bootstrap()
    await svc.poll_books()
    assert not svc.is_stale("BTC/TMN")
    assert (
        svc.get_book("BTC/TMN") is not None and svc.get_book("ETH/TMN") is None
    )  # ETH book missing -> error counted
    assert svc.errors >= 1
    clock.advance(181)
    assert svc.is_stale("BTC/TMN")
    assert svc.is_stale("ETH/TMN")


async def test_run_loop_stops() -> None:
    clock, md = _setup()
    svc = LiveDataService(
        md, clock, ["BTC/TMN"], tfs=[Timeframe.M15], buffer_bars=10, poll_seconds=20, book_poll_seconds=15
    )
    stop = asyncio.Event()
    n = 0

    def cb(*_: object) -> None:
        nonlocal n
        n += 1
        if n >= 3:
            stop.set()

    svc.subscribe(cb)
    await asyncio.wait_for(svc.run(stop), timeout=5)
    assert n == 3
    assert clock.now() >= SYN_START + timedelta(days=2, minutes=45)


def test_tz_aware_start() -> None:
    assert SYN_START.tzinfo is UTC
    assert datetime(2026, 1, 1, tzinfo=UTC) == SYN_START
