"""Live Wallex API tests (T-108). HUMAN ONLY: ``make test-live`` (excluded from ``make test``).

Read-only: public market data always; balances / fees / open orders only when ``WALLEX_API_KEY`` is
set. No order is ever placed here (use ``scripts/smoke_test.py --place-test-order`` for that).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.market_data import WallexMarketData

pytestmark = pytest.mark.live
SYMBOL = "BTC/TMN"


@pytest.fixture
async def http():  # type: ignore[no-untyped-def]
    client = WallexHttp.from_config(load_config("config"))
    yield client
    await client.aclose()


def _has_key() -> bool:
    return load_config("config").secrets.wallex_api_key is not None


async def test_markets_and_registry_fields(http: WallexHttp) -> None:
    markets = {m.symbol: m for m in ep.parse_markets(await http.get(ep.Paths.MARKETS))}
    btc = markets[SYMBOL]
    assert btc.is_spot and btc.qty_step > 0 and btc.price_tick > 0


async def test_ticker_book_candles_trades(http: WallexHttp) -> None:
    md = WallexMarketData(http, ticker_cache_s=0)
    t = await md.get_ticker(SYMBOL)
    assert t.last > 0 and t.bid is not None and t.ask is not None and t.bid < t.ask
    book = await md.get_orderbook(SYMBOL, depth=5)
    assert book.best_bid is not None and book.best_ask is not None
    now = datetime.now(UTC)
    c15 = await md.get_candles(SYMBOL, Timeframe.M15, now - timedelta(hours=6), now)
    assert len(c15) >= 4 and all(c.ts_open.minute % 15 == 0 for c in c15)
    h1 = await md.get_candles(SYMBOL, Timeframe.H1, now - timedelta(days=2), now)
    assert len(h1) >= 24
    assert len(await md.get_recent_trades(SYMBOL)) > 0


@pytest.mark.skipif(not _has_key(), reason="WALLEX_API_KEY not set")
async def test_private_read_only(http: WallexHttp) -> None:
    balances = ep.parse_balance(await http.get(ep.Paths.BALANCES, private=True))
    assert "TMN" in balances and balances["TMN"].free >= 0
    fees = ep.parse_fees(await http.get(ep.Paths.FEE, private=True))
    maker, taker = fees[SYMBOL]
    assert 0 <= maker < 1 and 0 <= taker < 1  # percent
    ep.parse_open_orders(await http.get(ep.Paths.OPEN_ORDERS, private=True))
