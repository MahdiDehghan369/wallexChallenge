"""Historical market data for backtests and replay (T-902).

``HistoricalMarketData`` implements ``MarketData`` over OHLCV frames and only ever returns what was
known at ``clock.now()``: candles whose close time ≤ now (anti-lookahead). The order book is
synthetic: mid = last closed 15m close, spread = ``bt.assumed_spread_pct`` for the symbol.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from siavash.core.interfaces import Clock, MarketData
from siavash.core.models import Candle, MarketInfo, OrderBook, OrderBookLevel, PublicTrade, Ticker
from siavash.core.symbols import split, to_exchange
from siavash.core.types import Timeframe
from siavash.data.synthetic import resample_ohlcv

DEPTH_QTY_QUOTE = Decimal("1e12")  # synthetic depth: effectively unlimited at the touch


def frames_from_15m(frames_15m: Mapping[str, pd.DataFrame]) -> dict[str, dict[Timeframe, pd.DataFrame]]:
    """``{symbol: {15m, 1h, 4h}}`` with 1h / 4h resampled from 15m."""
    return {
        s: {Timeframe.M15: df, Timeframe.H1: resample_ohlcv(df, "1h"), Timeframe.H4: resample_ohlcv(df, "4h")}
        for s, df in frames_15m.items()
    }


def processed_frames(
    processed_dir: str | Path, symbols: Iterable[str] | None = None
) -> dict[str, dict[Timeframe, pd.DataFrame]]:
    """``data/processed/{BASE}_{QUOTE}_15m.parquet`` (+ native 1h if present, else resampled; 4h from 1h)."""
    from siavash.data.clean import load_processed

    root = Path(processed_dir)
    frames: dict[str, dict[Timeframe, pd.DataFrame]] = {}
    wanted = set(symbols) if symbols else None
    for f in sorted(root.glob("*_15m.parquet")):
        base, quote, _ = f.stem.split("_", 2)
        sym = f"{base}/{quote}"
        if wanted is not None and sym not in wanted:
            continue
        m15 = load_processed(f)
        h1_path = root / f"{base}_{quote}_1h.parquet"
        h1 = (
            load_processed(h1_path)
            if h1_path.exists()
            else resample_ohlcv(m15.dropna(subset=["close"]), "1h")
        )
        frames[sym] = {
            Timeframe.M15: m15,
            Timeframe.H1: h1,
            Timeframe.H4: resample_ohlcv(h1.dropna(subset=["close"]), "4h"),
        }
    return frames


def default_market(symbol: str) -> MarketInfo:
    """Conservative metadata when no exchange snapshot is available."""
    base, quote = split(symbol)
    tmn = quote == "TMN"
    return MarketInfo(
        symbol=symbol,
        exchange_symbol=to_exchange(symbol),
        base=base,
        quote=quote,
        price_tick=Decimal("1") if tmn else Decimal("0.0001"),
        qty_step=Decimal("0.00000001"),
        min_qty=Decimal("0.00000001"),
        min_notional=Decimal("100000") if tmn else Decimal("1"),
        is_active=True,
    )


class _Series:
    """Candles of one (symbol, tf) with a sorted close-time index for O(log n) clock gating."""

    def __init__(self, symbol: str, tf: Timeframe, df: pd.DataFrame) -> None:
        d = df.dropna(subset=["close"]).sort_values("ts_open").reset_index(drop=True)
        self.open_s = (d["ts_open"].astype("int64") // 10**9).to_numpy()
        self.close_s = self.open_s + tf.seconds
        self.opens = d["open"].to_numpy(dtype=float)
        self.highs = d["high"].to_numpy(dtype=float)
        self.lows = d["low"].to_numpy(dtype=float)
        self.closes = d["close"].to_numpy(dtype=float)
        filled = (
            d["is_filled"].to_numpy(dtype=bool) if "is_filled" in d.columns else np.zeros(len(d), dtype=bool)
        )
        self.candles = [
            Candle(
                symbol=symbol,
                timeframe=tf,
                ts_open=datetime.fromtimestamp(int(t), tz=UTC),
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=float(v),
                is_filled=bool(f),
            )
            for t, o, h, lo, c, v, f in zip(
                self.open_s,
                self.opens,
                self.highs,
                self.lows,
                self.closes,
                d["volume"].to_numpy(dtype=float),
                filled,
                strict=True,
            )
        ]

    def n_closed(self, now_s: float) -> int:
        """Number of candles closed at ``now_s``."""
        return int(np.searchsorted(self.close_s, now_s, side="right"))

    def index_open_at(self, ts_s: int) -> int | None:
        i = int(np.searchsorted(self.open_s, ts_s))
        return i if i < len(self.open_s) and self.open_s[i] == ts_s else None


class HistoricalMarketData(MarketData):
    def __init__(
        self,
        frames: Mapping[str, Mapping[Timeframe, pd.DataFrame]],
        clock: Clock,
        markets: Iterable[MarketInfo] | None = None,
        spread_pct_fn: Callable[[str], float] | None = None,
    ) -> None:
        self.clock = clock
        self.series: dict[tuple[str, Timeframe], _Series] = {}
        for sym, by_tf in frames.items():
            for tf, df in by_tf.items():
                self.series[(sym, Timeframe(tf))] = _Series(sym, Timeframe(tf), df)
        given = {m.symbol: m for m in markets or []}
        self.markets = {s: given.get(s) or default_market(s) for s in frames}
        self.spread_pct_fn = spread_pct_fn or (lambda _s: 0.15)

    @classmethod
    def from_15m(
        cls, frames_15m: Mapping[str, pd.DataFrame], clock: Clock, **kw: object
    ) -> HistoricalMarketData:
        """Build 1h / 4h by resampling the 15m frames."""
        return cls(frames_from_15m(frames_15m), clock, **kw)  # type: ignore[arg-type]

    @classmethod
    def from_processed_dir(
        cls, processed_dir: str | Path, clock: Clock, symbols: Iterable[str] | None = None, **kw: object
    ) -> HistoricalMarketData:
        """Load ``data/processed/{BASE}_{QUOTE}_15m.parquet`` (+ native 1h if present, else resampled)."""
        return cls(processed_frames(processed_dir, symbols), clock, **kw)  # type: ignore[arg-type]

    # ------------------------------------------------------------------ helpers for the broker
    def time_range(self, tf: Timeframe = Timeframe.M15) -> tuple[datetime, datetime]:
        starts = [s.open_s[0] for (_, t), s in self.series.items() if t is tf and len(s.open_s)]
        ends = [s.close_s[-1] for (_, t), s in self.series.items() if t is tf and len(s.close_s)]
        return datetime.fromtimestamp(int(min(starts)), tz=UTC), datetime.fromtimestamp(
            int(max(ends)), tz=UTC
        )

    def last_close(self, symbol: str) -> float | None:
        s = self.series.get((symbol, Timeframe.M15))
        if s is None:
            return None
        n = s.n_closed(self.clock.now().timestamp())
        return float(s.closes[n - 1]) if n else None

    def current_open(self, symbol: str) -> float | None:
        """Open of the candle starting at the current bar boundary (the price at this instant)."""
        s = self.series.get((symbol, Timeframe.M15))
        if s is None:
            return None
        now_s = int(self.clock.now().timestamp())
        i = s.index_open_at(now_s // 900 * 900)
        return float(s.opens[i]) if i is not None else self.last_close(symbol)

    def bars_between(
        self, symbol: str, start_s: float, end_s: float
    ) -> list[tuple[int, float, float, float, float]]:
        """15m bars with ``ts_open ≥ start_s`` and close ≤ ``end_s``: ``(close_s, open, high, low, close)``."""
        s = self.series.get((symbol, Timeframe.M15))
        if s is None:
            return []
        i0 = int(np.searchsorted(s.open_s, start_s, side="left"))
        i1 = s.n_closed(end_s)
        return [(int(s.close_s[i]), s.opens[i], s.highs[i], s.lows[i], s.closes[i]) for i in range(i0, i1)]

    # ------------------------------------------------------------------ MarketData
    async def list_markets(self) -> list[MarketInfo]:
        return list(self.markets.values())

    async def get_candles(self, symbol: str, tf: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        s = self.series.get((symbol, tf))
        if s is None:
            return []
        n = s.n_closed(self.clock.now().timestamp())
        i0 = int(np.searchsorted(s.open_s, start.timestamp(), side="left"))
        i1 = min(int(np.searchsorted(s.open_s, end.timestamp(), side="left")), n)
        return s.candles[i0:i1] if i1 > i0 else []

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        px = self.last_close(symbol)
        now = self.clock.now()
        if px is None:
            return OrderBook(symbol=symbol, ts=now, bids=[], asks=[])
        m = self.markets.get(symbol) or default_market(symbol)
        half = Decimal(str(px)) * Decimal(str(self.spread_pct_fn(symbol))) / 200
        mid = Decimal(str(px))
        tick = m.price_tick
        bid = ((mid - half) / tick).to_integral_value(rounding="ROUND_FLOOR") * tick
        ask = ((mid + half) / tick).to_integral_value(rounding="ROUND_CEILING") * tick
        if ask <= bid:
            ask = bid + tick
        qty = (DEPTH_QTY_QUOTE / mid).quantize(Decimal("0.00000001"))
        return OrderBook(
            symbol=symbol,
            ts=now,
            bids=[OrderBookLevel(price=bid, qty=qty)],
            asks=[OrderBookLevel(price=ask, qty=qty)],
        )

    async def get_ticker(self, symbol: str) -> Ticker:
        px = self.last_close(symbol)
        p = Decimal(str(px)) if px is not None else Decimal(0)
        return Ticker(
            symbol=symbol, ts=self.clock.now(), last=p, bid=p, ask=p, volume_24h_quote=Decimal("1e12")
        )

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[PublicTrade]:
        return []
