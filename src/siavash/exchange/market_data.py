"""``MarketData`` implementation over the Wallex REST API (T-104)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle, MarketInfo, OrderBook, PublicTrade, Ticker
from siavash.core.types import Timeframe
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.markets import UnknownMarket


class WallexMarketData(MarketData):
    """Public market data. ``get_candles`` paginates and aggregates (15m from 1m, 4h from 1h)."""

    def __init__(
        self,
        http: WallexHttp,
        clock: Clock | None = None,
        max_bars: int | None = None,
        ticker_cache_s: float = 5.0,
        min_notional: Mapping[str, Decimal] | None = None,
    ) -> None:
        self.http = http
        self.clock = clock
        self.max_bars = max_bars
        self.ticker_cache_s = ticker_cache_s
        self.min_notional = min_notional
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
        return ep.parse_markets(await self._markets_raw(0), self.min_notional)

    async def get_ticker(self, symbol: str) -> Ticker:
        """Last price / volume from the markets call (cached) + best bid/ask from ``/v1/depth``."""
        tickers = await self.get_tickers()
        if symbol not in tickers:
            raise UnknownMarket(symbol)
        book = await self.get_orderbook(symbol, depth=1)
        return tickers[symbol].model_copy(update={"bid": book.best_bid, "ask": book.best_ask})

    async def get_tickers(self) -> dict[str, Ticker]:
        """All tickers from one markets call (no top-of-book: ``bid``/``ask`` are None)."""
        return ep.parse_tickers(await self._markets_raw(self.ticker_cache_s), self._now())

    async def get_all_orderbooks(self) -> dict[str, OrderBook]:
        """Every market's book from one ``/depth/all`` call."""
        return ep.parse_all_orderbooks(await self.http.get(ep.Paths.DEPTH_ALL), self._now())

    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        """Candles with ``start <= ts_open < end``.

        Source bars (see ``endpoints.CANDLE_SOURCES``) are fetched over the tf-aligned range in pages
        of ``max_bars`` source bars, merged, de-duplicated and aggregated into ``tf`` candles, so a
        returned candle always contains all of its source bars.
        """
        src = ep.CANDLE_SOURCES[tf]
        page = timedelta(seconds=src.source_seconds * (self.max_bars or src.max_bars))
        lo = datetime.fromtimestamp(int(start.timestamp()) // tf.seconds * tf.seconds, tz=UTC)
        hi_s = -(-int(end.timestamp()) // tf.seconds) * tf.seconds
        hi = datetime.fromtimestamp(hi_s, tz=UTC)
        bars: dict[datetime, tuple[datetime, float, float, float, float, float]] = {}
        cursor = lo
        while cursor < hi:
            chunk_end = min(cursor + page, hi)
            # UDF "to" is inclusive; stop one second before the next chunk / bucket boundary
            raw = await self.http.get(
                ep.Paths.CANDLES,
                params=ep.candles_params(symbol, tf, cursor, chunk_end - timedelta(seconds=1)),
            )
            for b in ep.parse_udf_bars(raw):
                if lo <= b[0] < hi:
                    bars[b[0]] = b
            cursor = chunk_end
        candles = ep.aggregate_bars([bars[k] for k in sorted(bars)], symbol, tf)
        return [c for c in candles if start <= c.ts_open < end]

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        raw = await self.http.get(ep.Paths.DEPTH, params=ep.symbol_params(symbol))
        book = ep.parse_orderbook(raw, symbol, self._now())
        return book.model_copy(update={"bids": book.bids[:depth], "asks": book.asks[:depth]})

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]:
        raw = await self.http.get(ep.Paths.TRADES, params=ep.symbol_params(symbol))
        return ep.parse_trades(raw, symbol)[-limit:]
