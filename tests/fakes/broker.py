"""In-memory ``Broker`` for tests.

- MARKET orders fill immediately at the ticker ``last`` price (or ``prices[symbol]``).
- LIMIT orders fill only through :meth:`FakeBroker.simulate_fill`.
- Same ``client_order_id`` twice returns the original order (idempotent).
- Fees are charged in the quote asset at ``fee_pct`` percent of notional.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from siavash.core.interfaces import Broker, Clock, MarketData
from siavash.core.models import Balance, Fill, Order, OrderRequest
from siavash.core.symbols import split
from siavash.core.types import OrderStatus, OrderType, Side


class FakeBrokerError(Exception):
    """Base error raised by FakeBroker."""


class InsufficientFunds(FakeBrokerError):
    pass


class OrderNotFoundError(FakeBrokerError):
    pass


class _Guard(Protocol):
    def assert_tradable(self, symbol: str, side: Any = None) -> None: ...


class FakeBroker(Broker):
    def __init__(
        self,
        balances: Mapping[str, Decimal | int | str] | None = None,
        fee_pct: Decimal | float | str = Decimal("0.35"),
        market_data: MarketData | None = None,
        prices: Mapping[str, Decimal] | None = None,
        clock: Clock | None = None,
        guard: _Guard | None = None,
    ) -> None:
        self.free: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in (balances or {"TMN": 5_000_000}).items()
        }
        self.locked: dict[str, Decimal] = {}
        self.fee_rate = Decimal(str(fee_pct)) / Decimal(100)
        self.market_data = market_data
        self.prices: dict[str, Decimal] = dict(prices or {})
        self.clock = clock
        self.guard = guard
        self.orders: dict[str, Order] = {}
        self.requests: dict[str, OrderRequest] = {}
        self.fills: list[Fill] = []
        self.placed: list[OrderRequest] = []  # every accepted (non-duplicate) request
        self._seq = 0

    # helpers ---------------------------------------------------------------------------
    def _now(self) -> datetime:
        return self.clock.now() if self.clock else datetime.now(UTC)

    def _add(self, book: dict[str, Decimal], asset: str, amount: Decimal) -> None:
        book[asset] = book.get(asset, Decimal(0)) + amount

    async def _market_price(self, symbol: str) -> Decimal:
        if symbol in self.prices:
            return self.prices[symbol]
        if self.market_data is None:
            raise FakeBrokerError(f"no price for {symbol}")
        return (await self.market_data.get_ticker(symbol)).last

    def set_price(self, symbol: str, price: Decimal) -> None:
        self.prices[symbol] = Decimal(price)

    # Broker ----------------------------------------------------------------------------
    async def place_order(self, req: OrderRequest) -> Order:
        if req.client_order_id in self.orders:
            return self.orders[req.client_order_id]
        if not req.decision_id:
            raise FakeBrokerError("order without decision_id")
        if self.guard is not None:
            self.guard.assert_tradable(req.symbol, req.side)
        if req.qty <= 0:
            raise FakeBrokerError("qty must be positive")
        base, quote = split(req.symbol)
        now = self._now()
        self._seq += 1
        order = Order(
            order_id=f"F{self._seq}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            price=req.price,
            qty=req.qty,
            status=OrderStatus.NEW,
            created_at=now,
            updated_at=now,
        )
        if req.type is OrderType.LIMIT:
            if req.price is None or req.price <= 0:
                raise FakeBrokerError("LIMIT order requires a positive price")
            if req.side is Side.BUY:
                need = req.qty * req.price * (1 + self.fee_rate)
                if self.free.get(quote, Decimal(0)) < need:
                    raise InsufficientFunds(f"need {need} {quote}")
                self._add(self.free, quote, -need)
                self._add(self.locked, quote, need)
            else:
                if self.free.get(base, Decimal(0)) < req.qty:
                    raise InsufficientFunds(f"need {req.qty} {base}")
                self._add(self.free, base, -req.qty)
                self._add(self.locked, base, req.qty)
            self.orders[req.client_order_id] = order
            self.requests[req.client_order_id] = req
            self.placed.append(req)
            return order

        price = await self._market_price(req.symbol)
        if req.side is Side.BUY:
            need = req.qty * price * (1 + self.fee_rate)
            if self.free.get(quote, Decimal(0)) < need:
                raise InsufficientFunds(f"need {need} {quote}")
        elif self.free.get(base, Decimal(0)) < req.qty:
            raise InsufficientFunds(f"need {req.qty} {base}")
        self.orders[req.client_order_id] = order
        self.requests[req.client_order_id] = req
        self.placed.append(req)
        self._apply_fill(req.client_order_id, price, req.qty, is_maker=False)
        return self.orders[req.client_order_id]

    def _apply_fill(self, coid: str, price: Decimal, qty: Decimal, is_maker: bool) -> Fill:
        order = self.orders[coid]
        base, quote = split(order.symbol)
        notional = qty * price
        fee = notional * self.fee_rate
        if order.side is Side.BUY:
            if order.type is OrderType.LIMIT:
                assert order.price is not None
                reserved = qty * order.price * (1 + self.fee_rate)
                self._add(self.locked, quote, -reserved)
                self._add(self.free, quote, reserved - notional - fee)
            else:
                self._add(self.free, quote, -(notional + fee))
            self._add(self.free, base, qty)
        else:
            if order.type is OrderType.LIMIT:
                self._add(self.locked, base, -qty)
            else:
                self._add(self.free, base, -qty)
            self._add(self.free, quote, notional - fee)

        filled = order.filled_qty + qty
        prev_cost = (order.avg_fill_price or Decimal(0)) * order.filled_qty
        avg = (prev_cost + notional) / filled
        status = OrderStatus.FILLED if filled >= order.qty else OrderStatus.PARTIALLY_FILLED
        now = self._now()
        self.orders[coid] = order.model_copy(
            update={"filled_qty": filled, "avg_fill_price": avg, "status": status, "updated_at": now}
        )
        fill = Fill(
            fill_id=f"{order.order_id}-{len(self.fills) + 1}",
            order_id=order.order_id,
            client_order_id=coid,
            symbol=order.symbol,
            side=order.side,
            price=price,
            qty=qty,
            fee=fee,
            fee_asset=quote,
            ts=now,
            is_maker=is_maker,
        )
        self.fills.append(fill)
        return fill

    def simulate_fill(
        self, client_order_id: str, price: Decimal | None = None, qty: Decimal | None = None
    ) -> Fill:
        """Fill (part of) an open LIMIT order. Defaults: full remaining qty at the limit price."""
        order = self.orders.get(client_order_id)
        if order is None:
            raise OrderNotFoundError(client_order_id)
        if order.status.is_terminal:
            raise FakeBrokerError(f"order {client_order_id} is {order.status.value}")
        remaining = order.qty - order.filled_qty
        fill_qty = min(qty if qty is not None else remaining, remaining)
        fill_price = Decimal(price) if price is not None else order.price
        assert fill_price is not None
        if order.side is Side.BUY and order.price is not None and fill_price > order.price:
            raise FakeBrokerError("buy limit cannot fill above its price")
        if order.side is Side.SELL and order.price is not None and fill_price < order.price:
            raise FakeBrokerError("sell limit cannot fill below its price")
        return self._apply_fill(client_order_id, fill_price, fill_qty, is_maker=True)

    async def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        order = self.orders.get(client_order_id)
        if order is None:
            raise OrderNotFoundError(client_order_id)
        if order.status.is_terminal:
            return order
        base, quote = split(order.symbol)
        remaining = order.qty - order.filled_qty
        if order.side is Side.BUY:
            assert order.price is not None
            release = remaining * order.price * (1 + self.fee_rate)
            self._add(self.locked, quote, -release)
            self._add(self.free, quote, release)
        else:
            self._add(self.locked, base, -remaining)
            self._add(self.free, base, remaining)
        self.orders[client_order_id] = order.model_copy(
            update={"status": OrderStatus.CANCELED, "updated_at": self._now()}
        )
        return self.orders[client_order_id]

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        return self.orders.get(client_order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return [
            o
            for o in self.orders.values()
            if not o.status.is_terminal and (symbol is None or o.symbol == symbol)
        ]

    async def get_balances(self) -> dict[str, Balance]:
        assets = set(self.free) | set(self.locked)
        return {
            a: Balance(asset=a, free=self.free.get(a, Decimal(0)), locked=self.locked.get(a, Decimal(0)))
            for a in sorted(assets)
        }

    async def get_fills(self, since: datetime) -> list[Fill]:
        return [f for f in self.fills if f.ts >= since]
