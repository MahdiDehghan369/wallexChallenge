"""Market metadata registry and price/quantity rounding (T-103).

Rounding rules: BUY prices round **down** to the tick, SELL prices round **up**; quantities always
round **down** to the step (never more than requested).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from siavash.core.interfaces import Clock
from siavash.core.models import MarketInfo
from siavash.core.symbols import to_canonical
from siavash.core.types import Side


class UnknownMarket(KeyError):
    """Symbol not present in the registry."""


def _to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step <= 0:
        return value
    units = (value / step).to_integral_value(rounding=rounding)
    return (units * step).quantize(step) if step.as_tuple().exponent < 0 else units * step  # type: ignore[operator]


class MarketRegistry:
    """Caches ``MarketInfo`` from a loader (e.g. ``MarketData.list_markets``); refreshes every ``refresh_hours``."""

    def __init__(
        self,
        loader: Callable[[], Awaitable[list[MarketInfo]]] | None = None,
        clock: Clock | None = None,
        refresh_hours: float = 6.0,
    ) -> None:
        self._loader = loader
        self._clock = clock
        self._refresh = timedelta(hours=refresh_hours)
        self._markets: dict[str, MarketInfo] = {}
        self._loaded_at: datetime | None = None

    @classmethod
    def from_markets(cls, markets: Iterable[MarketInfo]) -> MarketRegistry:
        """Static registry (tests, backtests)."""
        reg = cls()
        reg._set(markets)
        return reg

    def _set(self, markets: Iterable[MarketInfo]) -> None:
        self._markets = {m.symbol: m for m in markets}

    async def load(self) -> None:
        """(Re)load from the loader."""
        if self._loader is None:
            return
        self._set(await self._loader())
        self._loaded_at = self._clock.now() if self._clock else None

    async def refresh_if_stale(self) -> bool:
        """Reload if never loaded or older than ``refresh_hours``; returns True if reloaded."""
        if self._loader is None:
            return False
        if (
            self._loaded_at is None
            or self._clock is None
            or self._clock.now() - self._loaded_at >= self._refresh
        ):
            await self.load()
            return True
        return False

    def __contains__(self, symbol: str) -> bool:
        try:
            return to_canonical(symbol) in self._markets
        except ValueError:
            return False

    def all(self) -> list[MarketInfo]:
        return list(self._markets.values())

    def get(self, symbol: str) -> MarketInfo:
        """``MarketInfo`` for a canonical or exchange-format symbol."""
        try:
            return self._markets[to_canonical(symbol)]
        except (KeyError, ValueError) as e:
            raise UnknownMarket(symbol) from e

    def round_price(self, symbol: str, price: Decimal, side: Side) -> Decimal:
        """Round to the tick: down for BUY, up for SELL."""
        tick = self.get(symbol).price_tick
        return _to_step(Decimal(price), tick, ROUND_FLOOR if side is Side.BUY else ROUND_CEILING)

    def round_qty(self, symbol: str, qty: Decimal) -> Decimal:
        """Round down to the quantity step (never above ``qty``)."""
        out = _to_step(Decimal(qty), self.get(symbol).qty_step, ROUND_FLOOR)
        return max(out, Decimal(0))

    def check_min(self, symbol: str, qty: Decimal, price: Decimal) -> bool:
        """True if ``qty ≥ min_qty`` and ``qty × price ≥ min_notional``."""
        m = self.get(symbol)
        return qty > 0 and qty >= m.min_qty and qty * price >= m.min_notional

    def max_qty_for_notional(self, symbol: str, notional: Decimal, price: Decimal) -> Decimal:
        """Largest step-aligned qty with ``qty × price ≤ notional``."""
        if price <= 0 or notional <= 0:
            return Decimal(0)
        return self.round_qty(symbol, Decimal(notional) / Decimal(price))
