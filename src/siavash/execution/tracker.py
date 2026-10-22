"""Order and fill tracking (T-803).

Every ``order_poll_seconds`` the tracker refreshes the bot's open orders, pulls new fills
(de-duplicated by ``fill_id``), converts fees and notionals to TMN, appends them to
``logs/fills.csv`` and publishes ``on_fill`` / ``on_order_terminal`` events.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from siavash.core.ids import is_ours
from siavash.core.interfaces import Broker, Clock
from siavash.core.models import Fill, Order
from siavash.core.symbols import split
from siavash.engine.decision_log import FillsCsvLogger
from siavash.logging import get_logger

log = get_logger(__name__)


@dataclass
class TrackMeta:
    decision_id: str
    position_id: str | None = None
    strategy: str | None = None


FillCallback = Callable[[Fill, Decimal, TrackMeta | None], Awaitable[None] | None]
TerminalCallback = Callable[[Order, TrackMeta | None], Awaitable[None] | None]
PriceFn = Callable[[str], Decimal | None]  # symbol -> last price (e.g. "USDT/TMN")


async def _call(cb: Callable[..., object], *args: object) -> None:
    res = cb(*args)
    if inspect.isawaitable(res):
        await res


class OrderTracker:
    def __init__(
        self,
        broker: Broker,
        clock: Clock,
        fills_logger: FillsCsvLogger | None = None,
        price_fn: PriceFn | None = None,
        poll_seconds: float = 5.0,
        fill_lookback_s: float = 3600.0,
    ) -> None:
        self.broker = broker
        self.clock = clock
        self.fills_logger = fills_logger
        self.price_fn = price_fn
        self.poll_seconds = poll_seconds
        self.fill_lookback = timedelta(seconds=fill_lookback_s)
        self.tracked: dict[str, Order] = {}
        self.meta: dict[str, TrackMeta] = {}
        self.seen_fills: set[str] = set()
        self.on_fill: list[FillCallback] = []
        self.on_order_terminal: list[TerminalCallback] = []
        self._since = clock.now() - self.fill_lookback

    def track(self, order: Order, meta: TrackMeta | None = None) -> None:
        """Start tracking a bot order (and remember its decision / position)."""
        self.tracked[order.client_order_id] = order
        if meta is not None:
            self.meta[order.client_order_id] = meta

    def meta_for(self, client_order_id: str) -> TrackMeta | None:
        return self.meta.get(client_order_id)

    def to_tmn(self, amount: Decimal, asset: str, ref_symbol: str, fill_price: Decimal) -> Decimal:
        """Convert ``amount`` of ``asset`` to TMN (base coin via the fill price, USDT via ``USDT/TMN``)."""
        base, quote = split(ref_symbol)
        value_in_quote = amount * fill_price if asset == base else amount if asset == quote else None
        if value_in_quote is None:
            px = self.price_fn(f"{asset}/TMN") if self.price_fn else None
            return amount * px if px is not None else Decimal(0)
        if quote == "TMN":
            return value_in_quote
        px = self.price_fn(f"{quote}/TMN") if self.price_fn else None
        return value_in_quote * px if px is not None else Decimal(0)

    async def poll_once(self) -> list[Fill]:
        """Refresh tracked orders and ingest new fills; returns the new fills."""
        now = self.clock.now()
        new_fills: list[Fill] = []
        try:
            fills = await self.broker.get_fills(self._since)
        except Exception as e:
            log.warning("fills_poll_failed", error=str(e))
            fills = []
        for f in sorted(fills, key=lambda x: x.ts):
            if f.fill_id in self.seen_fills:
                continue
            if (
                f.client_order_id not in self.tracked
                and f.client_order_id not in self.meta
                and not is_ours(f.client_order_id)
            ):
                continue
            self.seen_fills.add(f.fill_id)
            meta = self.meta.get(f.client_order_id)
            fee_tmn = self.to_tmn(f.fee, f.fee_asset, f.symbol, f.price)
            notional_tmn = self.to_tmn(f.qty * f.price, split(f.symbol)[1], f.symbol, f.price)
            if self.fills_logger is not None:
                self.fills_logger.write(
                    f,
                    notional_tmn,
                    meta.decision_id if meta else None,
                    meta.position_id if meta else None,
                    meta.strategy if meta else None,
                )
            new_fills.append(f)
            for cb in self.on_fill:
                await _call(cb, f, fee_tmn, meta)
        for coid, old in list(self.tracked.items()):
            try:
                latest = await self.broker.get_order(old.symbol, coid)
            except Exception as e:
                log.warning("order_poll_failed", client_order_id=coid, error=str(e))
                continue
            if latest is None:
                continue
            self.tracked[coid] = latest
            if latest.status.is_terminal:
                del self.tracked[coid]
                for tcb in self.on_order_terminal:
                    await _call(tcb, latest, self.meta.get(coid))
        # keep a safety overlap; seen_fills de-duplicates
        self._since = max(self._since, now - self.fill_lookback)
        return new_fills

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.poll_once()
            await self.clock.sleep(self.poll_seconds)

    def open_client_ids(self) -> list[str]:
        return list(self.tracked)

    def last_poll(self) -> datetime:
        return self._since + self.fill_lookback
