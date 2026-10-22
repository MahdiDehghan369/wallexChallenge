"""``MarketData`` implementation over the Wallex REST API (T-104)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle, MarketInfo, OrderBook, PublicTrade, Ticker
from siavash.core.symbols import to_exchange
from siavash.core.types import Timeframe
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.markets import UnknownMarket


class WallexMarketData(MarketData):
    """Public market data. ``get_candles`` paginates when the range exceeds ``max_bars`` per request."""

    def __init__(
        self,
        http: WallexHttp,
        clock: Clock | None = None,
        max_bars: int = ep.CANDLES_MAX_BARS,
        ticker_cache_s: float = 5.0,
    ) -> None:
        self.http = http
        self.clock = clock
        self.max_bars = max_bars
        self.ticker_cache_s = ticker_cache_s
        self._markets_cache: tuple[datetime, Any] | None = None

    def _now(self) -> datetime:
        return self.clock.now() if self.clock else datetime.now(UTC)

    async def _markets_raw(self, max_age_s: float) -> Any:
        now = self._now()
        if self._markets_cache and (now - self._markets_cache[0]).total_seconds() <= max_age_s:
            return self._markets_cache[1]
        raw = await self.http.get(ep.Paths.MARKETS)
        self._markets_cache = (now, raw)
        return raw

    async def list_markets(self) -> list[MarketInfo]:
        return ep.parse_markets(await self._markets_raw(0))

    async def get_ticker(self, symbol: str) -> Ticker:
        raw = await self._markets_raw(self.ticker_cache_s)
        root = ep._result(raw)[ep.F["markets_root"]]
        key = to_exchange(symbol)
        if key not in root:
            raise UnknownMarket(symbol)
        return ep.parse_ticker(key, root[key], self._now())

    async def get_tickers(self) -> dict[str, Ticker]:
        """All tickers from one markets call."""
        return ep.parse_tickers(await self._markets_raw(self.ticker_cache_s), self._now())

    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        """Candles with ``start <= ts_open < end``; chunked requests merged, de-duplicated and sorted."""
        span = timedelta(seconds=tf.seconds * self.max_bars)
        merged: dict[datetime, Candle] = {}
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + span, end)
            raw = await self.http.get(
                ep.Paths.CANDLES, params=ep.candles_params(symbol, tf, cursor, chunk_end)
            )
            for c in ep.parse_candles(raw, symbol, tf):
                if start <= c.ts_open < end:
                    merged[c.ts_open] = c
            cursor = chunk_end
        return [merged[k] for k in sorted(merged)]

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self.http.get(ep.Paths.DEPTH, params=ep.symbol_params(symbol))
        book = ep.parse_orderbook(raw, symbol, self._now())
        return book.model_copy(update={"bids": book.bids[:depth], "asks": book.asks[:depth]})

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]:
        raw = await self.http.get(ep.Paths.TRADES, params=ep.symbol_params(symbol))
        return ep.parse_trades(raw, symbol)[-limit:]
