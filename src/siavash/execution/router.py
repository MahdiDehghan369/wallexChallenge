"""Order router (T-801, section 4.4 step 5 and 4.5).

Entry: passive LIMIT BUY at the best bid (one tick better when the spread allows, never crossing),
wait ``entry_timeout_s``; if not (fully) filled and the signal is still valid, cancel the rest and
send an aggressive LIMIT at ``best_ask × (1 + max_slippage_pct%)`` for ``aggressive_timeout_s``.

Exit: urgent reasons (stop-loss, trailing, breaker, end-game, operator) use aggressive LIMIT SELLs at
``best_bid × (1 − exit_max_slippage_pct%)`` repeated up to ``exit_max_attempts`` with a fresh book;
take-profit-like reasons first rest at ``max(best_ask, target)`` and then fall back to aggressive.
All client order ids come from ``core.ids.client_order_id``; slippage vs the reference is logged.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from siavash.core.ids import client_order_id
from siavash.core.interfaces import Broker, Clock
from siavash.core.models import Order, OrderBook, OrderRequest, Position
from siavash.core.types import ExitReason, OrderType, Side
from siavash.exchange.markets import MarketRegistry
from siavash.logging import get_logger

log = get_logger(__name__)

URGENT_EXITS = frozenset(
    {
        ExitReason.STOP_LOSS,
        ExitReason.TRAILING_STOP,
        ExitReason.BREAKER,
        ExitReason.ENDGAME,
        ExitReason.OPERATOR,
    }
)
EXIT_LEG = {ExitReason.TP1_PARTIAL: "T", ExitReason.ENDGAME: "G", ExitReason.OPERATOR: "O"}
EXIT_PURPOSE = {ExitReason.TP1_PARTIAL: "tp1", ExitReason.ENDGAME: "endgame", ExitReason.OPERATOR: "operator"}

BookFn = Callable[[str], Awaitable[OrderBook]]
SubmitFn = Callable[[Broker, OrderRequest], Awaitable[Order]]


async def _direct_submit(broker: Broker, req: OrderRequest) -> Order:
    return await broker.place_order(req)


class OrderRouter:
    def __init__(
        self,
        broker: Broker,
        registry: MarketRegistry,
        clock: Clock,
        cfg: Any,
        book_fn: BookFn | None = None,
        submit: SubmitFn | None = None,
    ) -> None:
        self.broker = broker
        self.registry = registry
        self.clock = clock
        self.ex = cfg.base.execution
        self.book_fn = book_fn
        self.submit = submit or _direct_submit
        self._attempts: dict[tuple[str, str], int] = {}
        # called after every accepted order: (order, decision_id, leg) — used to start fill tracking
        self.on_order: Callable[[Order, str, str], Awaitable[None] | None] | None = None
        self.latency: Any = None  # reporting.latency.LatencyRecorder

    # ------------------------------------------------------------------ helpers
    def _next_coid(self, decision_id: str, leg: str) -> str:
        n = self._attempts.get((decision_id, leg), 0) + 1
        self._attempts[(decision_id, leg)] = n
        return client_order_id(decision_id, leg, n)

    async def _fresh_book(self, symbol: str, fallback: OrderBook) -> OrderBook:
        if self.book_fn is None:
            return fallback
        try:
            return await self.book_fn(symbol)
        except Exception as e:
            log.warning("book_refresh_failed", symbol=symbol, error=str(e))
            return fallback

    async def _wait(self, order: Order, timeout_s: float) -> Order:
        """Poll until terminal or ``timeout_s`` elapsed; returns the latest view of the order."""
        waited = 0.0
        current = order
        while not current.status.is_terminal and waited < timeout_s:
            step = min(self.ex.order_poll_seconds, timeout_s - waited)
            await self.clock.sleep(step)
            waited += step
            latest = await self.broker.get_order(order.symbol, order.client_order_id)
            if latest is not None:
                current = latest
        return current

    async def _cancel_rest(self, order: Order) -> Order:
        if order.status.is_terminal:
            return order
        try:
            return await self.broker.cancel_order(order.symbol, order.client_order_id)
        except Exception as e:
            log.warning("cancel_failed", client_order_id=order.client_order_id, error=str(e))
            latest = await self.broker.get_order(order.symbol, order.client_order_id)
            return latest or order

    def _tradeable_qty(self, symbol: str, qty: Decimal, price: Decimal) -> Decimal:
        q = self.registry.round_qty(symbol, qty)
        return q if q > 0 and self.registry.check_min(symbol, q, price) else Decimal(0)

    async def _place(
        self, symbol: str, side: Side, qty: Decimal, price: Decimal, decision_id: str, leg: str, purpose: str
    ) -> Order | None:
        price = self.registry.round_price(symbol, price, side)
        q = self._tradeable_qty(symbol, qty, price)
        if q <= 0:
            log.info("order_skipped_below_min", symbol=symbol, qty=str(qty), price=str(price))
            return None
        req = OrderRequest(
            symbol=symbol,
            side=side,
            type=OrderType.LIMIT,
            qty=q,
            price=price,
            client_order_id=self._next_coid(decision_id, leg),
            decision_id=decision_id,
            purpose=purpose,  # type: ignore[arg-type]
        )
        t0 = time.perf_counter()
        order = await self.submit(self.broker, req)
        if self.latency is not None:
            self.latency.record(
                "order_sent_to_ack", (time.perf_counter() - t0) * 1000, symbol=symbol, purpose=purpose
            )
        if self.on_order is not None:
            res = self.on_order(order, decision_id, leg)
            if res is not None:
                await res
        return order

    def _log_slippage(self, order: Order, ref: Decimal, kind: str) -> None:
        if order.avg_fill_price is not None and ref > 0 and order.filled_qty > 0:
            slip = float((order.avg_fill_price / ref - 1) * 100)
            if order.side is Side.SELL:
                slip = -slip
            log.info(
                "slippage",
                kind=kind,
                client_order_id=order.client_order_id,
                ref=str(ref),
                fill=str(order.avg_fill_price),
                slippage_pct=round(slip, 4),
            )

    # ------------------------------------------------------------------ entry
    def passive_buy_price(self, book: OrderBook) -> Decimal:
        """Best bid, improved by one tick if that still stays below the best ask."""
        assert book.best_bid is not None and book.best_ask is not None
        tick = self.registry.get(book.symbol).price_tick
        if book.best_ask - book.best_bid > tick:
            return book.best_bid + tick
        return book.best_bid

    async def enter(
        self,
        position: Position,
        qty: Decimal,
        book: OrderBook,
        still_valid: Callable[[], Awaitable[bool]],
    ) -> list[Order]:
        """Maker attempt, then (if still valid) an aggressive attempt for the remainder."""
        if book.best_bid is None or book.best_ask is None:
            log.warning("enter_no_book", symbol=position.symbol)
            return []
        did, sym = position.entry_decision_id, position.symbol
        orders: list[Order] = []
        ref = book.best_ask
        o = await self._place(sym, Side.BUY, qty, self.passive_buy_price(book), did, "E", "entry")
        if o is None:
            return orders
        o = await self._wait(o, self.ex.entry_timeout_s)
        o = await self._cancel_rest(o)
        orders.append(o)
        remaining = qty - o.filled_qty
        if remaining <= 0:
            self._log_slippage(o, ref, "entry")
            return orders
        if not await still_valid():
            log.info("entry_abandoned_signal_invalid", symbol=sym, filled=str(o.filled_qty))
            return orders
        fresh = await self._fresh_book(sym, book)
        if fresh.best_ask is None:
            return orders
        aggressive = fresh.best_ask * (1 + Decimal(str(self.ex.max_slippage_pct)) / 100)
        o2 = await self._place(sym, Side.BUY, remaining, aggressive, did, "E", "entry")
        if o2 is None:
            return orders
        o2 = await self._wait(o2, self.ex.aggressive_timeout_s)
        o2 = await self._cancel_rest(o2)
        orders.append(o2)
        self._log_slippage(o2, fresh.best_ask, "entry_aggressive")
        return orders

    # ------------------------------------------------------------------ exit
    async def exit(
        self,
        position: Position,
        qty: Decimal,
        reason: ExitReason,
        book: OrderBook,
        decision_id: str | None = None,
        target_price: Decimal | None = None,
    ) -> list[Order]:
        """Sell ``qty`` of a position for ``reason``. Returns all orders sent (final states)."""
        if target_price is None and reason is ExitReason.TAKE_PROFIT:
            target_price = position.take_profit
        return await self.sell(
            position.symbol,
            qty,
            decision_id or position.entry_decision_id,
            reason,
            book,
            urgent=reason in URGENT_EXITS,
            target_price=target_price,
        )

    async def sell(
        self,
        symbol: str,
        qty: Decimal,
        decision_id: str,
        reason: ExitReason,
        book: OrderBook,
        urgent: bool = True,
        target_price: Decimal | None = None,
        passive_timeout_s: float | None = None,
    ) -> list[Order]:
        """Sell ``qty`` of ``symbol``: optional passive attempt at ``max(best_ask, target)``, then up to
        ``exit_max_attempts`` aggressive LIMITs at ``best_bid × (1 − exit_max_slippage_pct%)``."""
        did, sym = decision_id, symbol
        leg = EXIT_LEG.get(reason, "X")
        purpose = EXIT_PURPOSE.get(reason, "exit")
        orders: list[Order] = []
        remaining = qty
        if not urgent and book.best_ask is not None:
            passive = max(book.best_ask, target_price) if target_price is not None else book.best_ask
            o = await self._place(sym, Side.SELL, remaining, passive, did, leg, purpose)
            if o is not None:
                o = await self._wait(
                    o, passive_timeout_s if passive_timeout_s is not None else self.ex.entry_timeout_s
                )
                o = await self._cancel_rest(o)
                orders.append(o)
                remaining -= o.filled_qty
        current = book
        for attempt in range(self.ex.exit_max_attempts):
            if remaining <= 0:
                break
            if attempt > 0 or orders:
                current = await self._fresh_book(sym, current)
            if current.best_bid is None:
                log.warning("exit_no_bid", symbol=sym)
                break
            price = current.best_bid * (1 - Decimal(str(self.ex.exit_max_slippage_pct)) / 100)
            o = await self._place(sym, Side.SELL, remaining, price, did, leg, purpose)
            if o is None:
                break
            o = await self._wait(o, self.ex.aggressive_timeout_s)
            o = await self._cancel_rest(o)
            orders.append(o)
            remaining -= o.filled_qty
            self._log_slippage(o, current.best_bid, f"exit_{reason.value.lower()}")
        if remaining > 0:
            log.warning("exit_incomplete", symbol=sym, reason=reason.value, remaining=str(remaining))
        return orders
