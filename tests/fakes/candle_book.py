"""Market data whose order book follows the latest closed 15m candle (for engine simulations)."""

from __future__ import annotations

from decimal import Decimal

from siavash.core.models import OrderBook, OrderBookLevel, Ticker
from siavash.core.types import Timeframe
from tests.fakes.market_data import InMemoryMarketData


class CandleBookMarketData(InMemoryMarketData):
    spread_pct = Decimal("0.04")

    def _last_close(self, symbol: str) -> Decimal | None:
        assert self.clock is not None
        now = self.clock.now()
        closed = [
            c
            for c in self.candles.get((symbol, Timeframe.M15), [])
            if c.ts_open.timestamp() + 900 <= now.timestamp()
        ]
        return Decimal(str(closed[-1].close)) if closed else None

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        assert self.clock is not None
        px = self._last_close(symbol)
        if px is None:
            return OrderBook(symbol=symbol, ts=self.clock.now(), bids=[], asks=[])
        half = px * self.spread_pct / 200
        return OrderBook(
            symbol=symbol,
            ts=self.clock.now(),
            bids=[OrderBookLevel(price=(px - half).quantize(Decimal(1)), qty=Decimal("1000"))],
            asks=[OrderBookLevel(price=(px + half).quantize(Decimal(1)), qty=Decimal("1000"))],
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        assert self.clock is not None
        px = self._last_close(symbol) or Decimal(0)
        return Ticker(
            symbol=symbol, ts=self.clock.now(), last=px, bid=px, ask=px, volume_24h_quote=Decimal("1e12")
        )
