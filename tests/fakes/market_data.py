"""In-memory ``MarketData`` implementation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle, MarketInfo, OrderBook, PublicTrade, Ticker
from siavash.core.types import Timeframe


class InMemoryMarketData(MarketData):
    """Serves pre-loaded candles/books/tickers/trades.

    If ``clock`` is given, candles that have not closed yet at ``clock.now()`` are never returned
    (no lookahead), matching what a live exchange would serve.
    """

    def __init__(
        self,
        markets: Iterable[MarketInfo] = (),
        candles: dict[tuple[str, Timeframe], list[Candle]] | None = None,
        books: dict[str, OrderBook] | None = None,
        tickers: dict[str, Ticker] | None = None,
        trades: dict[str, list[PublicTrade]] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.markets = {m.symbol: m for m in markets}
        self.candles: dict[tuple[str, Timeframe], list[Candle]] = {
            k: sorted(v, key=lambda c: c.ts_open) for k, v in (candles or {}).items()
        }
        self.books = dict(books or {})
        self.tickers = dict(tickers or {})
        self.trades = {k: list(v) for k, v in (trades or {}).items()}
        self.clock = clock
        self.calls: list[tuple[str, str]] = []

    # mutation helpers ------------------------------------------------------------------
    def add_candle(self, candle: Candle) -> None:
        lst = self.candles.setdefault((candle.symbol, candle.timeframe), [])
        lst.append(candle)
        lst.sort(key=lambda c: c.ts_open)

    def set_book(self, book: OrderBook) -> None:
        self.books[book.symbol] = book

    def set_ticker(self, ticker: Ticker) -> None:
        self.tickers[ticker.symbol] = ticker

    def add_trades(self, symbol: str, trades: Iterable[PublicTrade]) -> None:
        self.trades.setdefault(symbol, []).extend(trades)

    # MarketData ------------------------------------------------------------------------
    async def list_markets(self) -> list[MarketInfo]:
        self.calls.append(("list_markets", ""))
        return list(self.markets.values())

    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        self.calls.append(("get_candles", symbol))
        out = [c for c in self.candles.get((symbol, tf), []) if start <= c.ts_open < end]
        if self.clock is not None:
            now = self.clock.now()
            out = [c for c in out if c.ts_open.timestamp() + tf.seconds <= now.timestamp()]
        return out

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        self.calls.append(("get_orderbook", symbol))
        book = self.books[symbol]
        return book.model_copy(update={"bids": book.bids[:depth], "asks": book.asks[:depth]})

    async def get_ticker(self, symbol: str) -> Ticker:
        self.calls.append(("get_ticker", symbol))
        return self.tickers[symbol]

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]:
        self.calls.append(("get_recent_trades", symbol))
        trades = self.trades.get(symbol, [])
        if self.clock is not None:
            now = self.clock.now()
            trades = [t for t in trades if t.ts <= now]
        return sorted(trades, key=lambda t: t.ts)[-limit:]
