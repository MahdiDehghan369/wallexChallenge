"""T-104: WallexMarketData (respx + fixtures)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx

from siavash.core.types import Timeframe
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.market_data import WallexMarketData
from siavash.exchange.markets import UnknownMarket
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import load

BASE = "https://wallex.test"
T0 = datetime(2026, 11, 5, 0, 0, tzinfo=UTC)


async def _nosleep(_: float) -> None:
    return None


@pytest.fixture
def md() -> WallexMarketData:
    http = WallexHttp(BASE, "k", public_rate=(1000, 1000), sleep_fn=_nosleep)
    return WallexMarketData(http, FakeClock(T0), max_bars=4, ticker_cache_s=5)


def _udf(start: int, end: int, step: int) -> dict:
    ts = list(range(start, end + 1, step))  # UDF 'to' treated as inclusive -> overlaps between chunks
    return {
        "s": "ok",
        "t": ts,
        "o": [1] * len(ts),
        "h": [2] * len(ts),
        "l": [0.5] * len(ts),
        "c": [1.5] * len(ts),
        "v": [3] * len(ts),
    }


@respx.mock(base_url=BASE)
async def test_candles_paginate_three_chunks(respx_mock: respx.MockRouter, md: WallexMarketData) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        assert p["symbol"] == "BTCTMN" and p["resolution"] == "15"
        return httpx.Response(200, json=_udf(int(p["from"]), int(p["to"]), 900))

    route = respx_mock.get(ep.Paths.CANDLES).mock(side_effect=handler)
    end = T0 + timedelta(minutes=15 * 10)
    candles = await md.get_candles("BTC/TMN", Timeframe.M15, T0, end)
    assert route.call_count == 3  # 4 + 4 + 2 bars
    assert len(candles) == 10
    assert [c.ts_open for c in candles] == [T0 + timedelta(minutes=15 * i) for i in range(10)]
    assert all(c.symbol == "BTC/TMN" and c.timeframe is Timeframe.M15 for c in candles)


@respx.mock(base_url=BASE)
async def test_orderbook_trades_markets(respx_mock: respx.MockRouter, md: WallexMarketData) -> None:
    respx_mock.get(ep.Paths.DEPTH).mock(return_value=httpx.Response(200, json=load("depth")))
    respx_mock.get(ep.Paths.TRADES).mock(return_value=httpx.Response(200, json=load("trades")))
    mk = respx_mock.get(ep.Paths.MARKETS).mock(return_value=httpx.Response(200, json=load("markets")))
    book = await md.get_orderbook("BTC/TMN", depth=2)
    assert len(book.bids) == 2 and book.best_bid == Decimal("7349000000") and book.ts == T0
    trades = await md.get_recent_trades("BTC/TMN", limit=2)
    assert len(trades) == 2 and trades[-1].price == Decimal("7350000000")
    markets = await md.list_markets()
    assert any(m.symbol == "BTC/TMN" for m in markets)
    t1 = await md.get_ticker("BTC/TMN")
    t2 = await md.get_ticker("ETH/TMN")
    assert t1.last == Decimal("7350000000") and t2.symbol == "ETH/TMN"
    assert mk.call_count == 1  # ticker served from the 5 s cache
    md.clock.advance(6)  # type: ignore[union-attr]
    await md.get_ticker("BTC/TMN")
    assert mk.call_count == 2
    with pytest.raises(UnknownMarket):
        await md.get_ticker("DOGE/TMN")
