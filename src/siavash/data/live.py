"""Live data service (T-203).

Keeps a ring buffer of the last ``buffer_bars`` closed candles per (symbol, tf), polls the market
data source, detects candle closes (``now >= ts_open + tf``) and publishes ``on_candle_close``
events exactly once per candle (callbacks and an ``asyncio.Queue``). Order books are snapshotted
for active symbols and symbols with open positions. Closed candles and book snapshots are appended
to ``data/live/{YYYY-MM-DD}/`` for replay. Works with any ``MarketData`` + ``Clock`` (fakes included).

Wallex bars are sparse (no bar when nothing traded). A missing bucket is forward-filled
(``is_filled=True``, flat at the previous close, volume 0) only once a *later* real candle exists,
so a merely delayed candle is never replaced by a synthetic one.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle, OrderBook
from siavash.core.types import Timeframe
from siavash.logging import get_logger

log = get_logger(__name__)

CandleCallback = Callable[[str, Timeframe, Candle], Awaitable[None] | None]


class LiveDataService:
    def __init__(
        self,
        market_data: MarketData,
        clock: Clock,
        symbols: Iterable[str],
        tfs: Iterable[Timeframe] = (Timeframe.M15, Timeframe.H1, Timeframe.H4),
        buffer_bars: int = 500,
        poll_seconds: float = 20,
        book_poll_seconds: float = 15,
        stale_after_s: float = 180,
        store_dir: str | Path | None = None,
        book_depth: int = 20,
    ) -> None:
        self.md = market_data
        self.clock = clock
        self.symbols: list[str] = list(dict.fromkeys(symbols))
        self.tfs = list(tfs)
        self.buffer_bars = buffer_bars
        self.poll_seconds = poll_seconds
        self.book_poll_seconds = book_poll_seconds
        self.stale_after_s = stale_after_s
        self.store_dir = Path(store_dir) if store_dir else None
        self.book_depth = book_depth
        self.buffers: dict[tuple[str, Timeframe], deque[Candle]] = {}
        self.books: dict[str, OrderBook] = {}
        self.extra_book_symbols: set[str] = set()
        self._last_update: dict[str, datetime] = {}
        self.callbacks: list[CandleCallback] = []
        self.events: asyncio.Queue[tuple[str, Timeframe, Candle]] = asyncio.Queue()
        self.errors = 0

    @classmethod
    def from_config(
        cls, cfg: object, market_data: MarketData, clock: Clock, symbols: Iterable[str]
    ) -> LiveDataService:
        """Build with ``base.loop`` / ``risk.breakers`` settings."""
        loop = cfg.base.loop  # type: ignore[attr-defined]
        return cls(
            market_data,
            clock,
            symbols,
            buffer_bars=loop.buffer_bars,
            poll_seconds=loop.poll_seconds,
            book_poll_seconds=loop.book_poll_seconds,
            stale_after_s=cfg.risk.breakers.stale_data_seconds,  # type: ignore[attr-defined]
            store_dir=Path(cfg.base.paths.data) / "live",  # type: ignore[attr-defined]
        )

    # ------------------------------------------------------------------ subscriptions / state
    def subscribe(self, cb: CandleCallback) -> None:
        self.callbacks.append(cb)

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Change the tracked universe (new symbols are bootstrapped on the next poll)."""
        self.symbols = list(dict.fromkeys(symbols))

    def set_book_symbols(self, symbols: Iterable[str]) -> None:
        """Extra symbols whose books must be polled (e.g. open positions)."""
        self.extra_book_symbols = set(symbols)

    def last_update_ts(self, symbol: str) -> datetime | None:
        return self._last_update.get(symbol)

    def is_stale(self, symbol: str, max_age_s: float | None = None) -> bool:
        """True if no successful update for ``symbol`` within ``max_age_s`` (default ``stale_after_s``)."""
        last = self._last_update.get(symbol)
        limit = self.stale_after_s if max_age_s is None else max_age_s
        return last is None or (self.clock.now() - last).total_seconds() > limit

    def candles(self, symbol: str, tf: Timeframe) -> list[Candle]:
        return list(self.buffers.get((symbol, tf), ()))

    def candles_df(self, symbol: str, tf: Timeframe) -> pd.DataFrame:
        """Buffer as an OHLCV frame (``ts_open`` column, UTC) for the feature builder."""
        rows = [
            {
                "ts_open": c.ts_open,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "is_filled": c.is_filled,
            }
            for c in self.buffers.get((symbol, tf), ())
        ]
        df = pd.DataFrame(rows, columns=["ts_open", "open", "high", "low", "close", "volume", "is_filled"])
        if not df.empty:
            df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
        return df

    def get_book(self, symbol: str) -> OrderBook | None:
        return self.books.get(symbol)

    # ------------------------------------------------------------------ polling
    def _closed(self, c: Candle, now: datetime) -> bool:
        return c.ts_open + timedelta(seconds=c.timeframe.seconds) <= now

    async def _fetch(self, symbol: str, tf: Timeframe, start: datetime, now: datetime) -> list[Candle]:
        candles = await self.md.get_candles(symbol, tf, start, now)
        return [c for c in candles if self._closed(c, now)]

    async def bootstrap(self, symbols: Iterable[str] | None = None) -> None:
        """Fill buffers with history (no events are emitted for bootstrap candles)."""
        now = self.clock.now()
        for sym in symbols or self.symbols:
            for tf in self.tfs:
                aligned = datetime.fromtimestamp(
                    int(now.timestamp()) // tf.seconds * tf.seconds, tz=now.tzinfo
                )
                start = aligned - timedelta(seconds=tf.seconds * self.buffer_bars)
                try:
                    got = await self._fetch(sym, tf, start, now)
                except Exception as e:
                    self.errors += 1
                    log.warning("bootstrap_failed", symbol=sym, tf=tf.value, error=str(e))
                    continue
                buf = self.buffers.setdefault((sym, tf), deque(maxlen=self.buffer_bars))
                buf.clear()
                buf.extend(self._with_fills(None, got))
                self._last_update[sym] = now

    def _with_fills(self, last: Candle | None, new: list[Candle]) -> list[Candle]:
        """Insert flat filled candles for buckets missing *between* known candles."""
        out: list[Candle] = []
        prev = last
        for c in sorted(new, key=lambda x: x.ts_open):
            if prev is not None:
                step = timedelta(seconds=c.timeframe.seconds)
                t = prev.ts_open + step
                while t < c.ts_open:
                    p = prev.close
                    out.append(
                        Candle(
                            symbol=c.symbol,
                            timeframe=c.timeframe,
                            ts_open=t,
                            open=p,
                            high=p,
                            low=p,
                            close=p,
                            volume=0.0,
                            is_filled=True,
                        )
                    )
                    t += step
            out.append(c)
            prev = c
        return out

    async def poll_candles(self) -> int:
        """One polling pass; returns the number of candle-close events emitted."""
        now = self.clock.now()
        emitted = 0
        for sym in list(self.symbols):
            for tf in self.tfs:
                key = (sym, tf)
                if key not in self.buffers:
                    await self.bootstrap([sym])
                    continue
                buf = self.buffers[key]
                last = buf[-1] if buf else None
                start = (
                    (last.ts_open + timedelta(seconds=tf.seconds))
                    if last
                    else now - timedelta(seconds=tf.seconds * self.buffer_bars)
                )
                try:
                    got = await self._fetch(sym, tf, start, now)
                except Exception as e:
                    self.errors += 1
                    log.warning("poll_failed", symbol=sym, tf=tf.value, error=str(e))
                    continue
                self._last_update[sym] = now
                fresh = [c for c in got if last is None or c.ts_open > last.ts_open]
                for c in self._with_fills(last, fresh):
                    buf.append(c)
                    self._persist_candle(c)
                    await self._emit(sym, tf, c)
                    emitted += 1
        return emitted

    async def poll_books(self) -> None:
        """Snapshot books for tracked + position symbols."""
        for sym in sorted(set(self.symbols) | self.extra_book_symbols):
            try:
                book = await self.md.get_orderbook(sym, self.book_depth)
            except Exception as e:
                self.errors += 1
                log.warning("book_poll_failed", symbol=sym, error=str(e))
                continue
            self.books[sym] = book
            self._last_update[sym] = self.clock.now()
            self._persist_book(book)

    async def _emit(self, sym: str, tf: Timeframe, c: Candle) -> None:
        await self.events.put((sym, tf, c))
        for cb in self.callbacks:
            res = cb(sym, tf, c)
            if inspect.isawaitable(res):
                await res

    async def run(self, stop: asyncio.Event) -> None:
        """Poll candles every ``poll_seconds`` and books every ``book_poll_seconds`` until ``stop`` is set."""
        await self.bootstrap()
        next_candles = next_books = self.clock.now()
        while not stop.is_set():
            now = self.clock.now()
            if now >= next_books:
                await self.poll_books()
                next_books = now + timedelta(seconds=self.book_poll_seconds)
            if now >= next_candles:
                await self.poll_candles()
                next_candles = now + timedelta(seconds=self.poll_seconds)
            wait = max(0.0, (min(next_books, next_candles) - self.clock.now()).total_seconds())
            await self.clock.sleep(min(wait, 1.0) if wait > 0 else 0.1)

    # ------------------------------------------------------------------ persistence (replay)
    def _day_dir(self, ts: datetime) -> Path | None:
        if self.store_dir is None:
            return None
        d = self.store_dir / ts.strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist_candle(self, c: Candle) -> None:
        d = self._day_dir(c.ts_open)
        if d is None:
            return
        with (d / f"candles_{c.timeframe.value}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(c.model_dump(mode="json")) + "\n")

    def _persist_book(self, b: OrderBook) -> None:
        d = self._day_dir(b.ts)
        if d is None:
            return
        with (d / "books.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(b.model_dump(mode="json")) + "\n")
