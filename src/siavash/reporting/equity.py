"""Equity and drawdown tracking (T-1202).

``equity = TMN.total + Σ asset.total × best_bid(asset/TMN)`` (USDT and coins without a TMN market
are valued through ``asset/USDT × USDT/TMN``). Tracks peak equity, drawdown from peak and daily PnL
(trading day starts 00:00 in ``base.timezone``, Asia/Tehran), appends to ``logs/equity.csv`` and the
store, and builds the ``PortfolioSnapshot`` used by the risk layer.
"""

from __future__ import annotations

import asyncio
import csv
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from siavash.core.interfaces import Broker, Clock
from siavash.core.models import Balance, PortfolioSnapshot, Position
from siavash.core.types import BreakerState
from siavash.logging import get_logger

log = get_logger(__name__)
ZERO = Decimal(0)
EQUITY_COLUMNS = [
    "ts",
    "equity_tmn",
    "cash_tmn",
    "exposure_tmn",
    "peak_equity_tmn",
    "drawdown_pct",
    "daily_pnl_pct",
]

BidFn = Callable[[str], Awaitable[Decimal | None]]


@dataclass
class EquityPoint:
    ts: datetime
    equity_tmn: Decimal
    cash_tmn: Decimal
    exposure_tmn: Decimal
    peak_equity_tmn: Decimal
    drawdown_pct: float
    daily_pnl_pct: float
    unpriced: list[str]


def trading_day_start(now: datetime, tz: str) -> datetime:
    """Start (as aware UTC datetime) of the trading day containing ``now`` in timezone ``tz``."""
    local = now.astimezone(ZoneInfo(tz))
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(now.tzinfo or ZoneInfo("UTC"))


class EquityTracker:
    def __init__(
        self,
        broker: Broker,
        bid_fn: BidFn,
        store: Any,
        clock: Clock,
        cfg: Any,
        csv_path: str | Path | None = None,
    ) -> None:
        self.broker = broker
        self.bid_fn = bid_fn
        self.store = store
        self.clock = clock
        self.cfg = cfg
        self.tz = cfg.base.timezone
        self.interval_s = cfg.base.loop.equity_interval_s
        self.csv_path = Path(csv_path) if csv_path else None
        self.last: EquityPoint | None = None

    @staticmethod
    def bid_fn_from_live(live_data: Any, market_data: Any = None) -> BidFn:
        """Best bid from the live book cache, falling back to the ticker."""

        async def fn(symbol: str) -> Decimal | None:
            book = live_data.get_book(symbol) if live_data is not None else None
            if book is not None and book.best_bid is not None:
                return book.best_bid
            if market_data is not None:
                try:
                    t = await market_data.get_ticker(symbol)
                    return t.bid or t.last
                except Exception:
                    return None
            return None

        return fn

    async def value_of(self, asset: str, amount: Decimal) -> Decimal | None:
        """TMN value of ``amount`` of ``asset`` (None if it cannot be priced)."""
        if asset == "TMN" or amount == 0:
            return amount
        direct = await self.bid_fn(f"{asset}/TMN")
        if direct is not None:
            return amount * direct
        via = await self.bid_fn(f"{asset}/USDT")
        usdt = await self.bid_fn("USDT/TMN")
        if via is not None and usdt is not None:
            return amount * via * usdt
        return None

    async def compute(self, balances: dict[str, Balance]) -> tuple[Decimal, Decimal, Decimal, list[str]]:
        """``(equity, cash, exposure, unpriced_assets)``."""
        cash_total = balances["TMN"].total if "TMN" in balances else ZERO
        cash_free = balances["TMN"].free if "TMN" in balances else ZERO
        exposure = ZERO
        unpriced: list[str] = []
        for asset, bal in balances.items():
            if asset == "TMN" or bal.total == 0:
                continue
            v = await self.value_of(asset, bal.total)
            if v is None:
                unpriced.append(asset)
                continue
            exposure += v
        return cash_total + exposure, cash_free, exposure, unpriced

    async def update(self) -> EquityPoint:
        """Recompute equity now; persist peak / day start; append CSV and store rows."""
        now = self.clock.now()
        balances = await self.broker.get_balances()
        equity, cash, exposure, unpriced = await self.compute(balances)
        if unpriced:
            log.warning("equity_unpriced_assets", assets=unpriced)
        peak = Decimal(str(await self.store.kv_get("peak_equity", "0")))
        if equity > peak:
            peak = equity
            await self.store.kv_set("peak_equity", str(peak))
        day0 = trading_day_start(now, self.tz)
        day = await self.store.kv_get("day_start") or {}
        if day.get("start") != day0.isoformat():
            day = {"start": day0.isoformat(), "equity": str(equity)}
            await self.store.kv_set("day_start", day)
        day_equity = Decimal(day["equity"])
        dd = float((peak - equity) / peak * 100) if peak > 0 else 0.0
        daily = float((equity / day_equity - 1) * 100) if day_equity > 0 else 0.0
        point = EquityPoint(now, equity, cash, exposure, peak, max(dd, 0.0), daily, unpriced)
        await self.store.add_equity(now, equity)
        self._append_csv(point)
        self.last = point
        return point

    def _append_csv(self, p: EquityPoint) -> None:
        if self.csv_path is None:
            return
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(EQUITY_COLUMNS)
            w.writerow(
                [
                    p.ts.isoformat().replace("+00:00", "Z"),
                    str(p.equity_tmn),
                    str(p.cash_tmn),
                    str(p.exposure_tmn),
                    str(p.peak_equity_tmn),
                    f"{p.drawdown_pct:.6f}",
                    f"{p.daily_pnl_pct:.6f}",
                ]
            )

    def snapshot(
        self,
        open_positions: Sequence[Position],
        trades_last_hour: int,
        trades_today: int,
        breaker_state: BreakerState,
        exposure_override: Decimal | None = None,
    ) -> PortfolioSnapshot:
        """``PortfolioSnapshot`` from the latest point (call :meth:`update` first)."""
        if self.last is None:
            raise RuntimeError("EquityTracker.update() has not run yet")
        p = self.last
        return PortfolioSnapshot(
            ts=p.ts,
            equity_tmn=p.equity_tmn,
            cash_tmn=p.cash_tmn,
            peak_equity_tmn=p.peak_equity_tmn,
            exposure_tmn=exposure_override if exposure_override is not None else p.exposure_tmn,
            open_positions=list(open_positions),
            trades_last_hour=trades_last_hour,
            trades_today=trades_today,
            daily_pnl_pct=p.daily_pnl_pct,
            drawdown_pct=p.drawdown_pct,
            breaker_state=breaker_state,
        )

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.update()
            except Exception as e:
                log.warning("equity_update_failed", error=str(e))
            await self.clock.sleep(self.interval_s)

    def day_start(self) -> datetime:
        return trading_day_start(self.clock.now(), self.tz)

    def next_day_start(self) -> datetime:
        return self.day_start() + timedelta(days=1)
