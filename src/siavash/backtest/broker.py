"""BacktestBroker (T-902): fills, fees and slippage on historical 15m candles.

Fill model (documented assumptions):
- passive LIMIT BUY at P: filled at P (maker fee) by the first 15m candle that *starts after the
  order was placed* and trades below P (``low < P``); SELL symmetric (``high > P``). Never the
  candle the order was placed in.
- crossing LIMIT (BUY with P ≥ synthetic ask, SELL with P ≤ bid) and MARKET: filled at the open of
  the candle starting at the decision time × (1 ± (spread/2 + ``bt.extra_slippage_pct``)%) with the
  taker fee — if that price is still within the limit, otherwise it rests as a passive order.
- intrabar stop-loss touches are not simulated (exits are evaluated on candle closes).
SymbolGuard + rounding via ``prepare_order``; funds are reserved like on the exchange.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from siavash.backtest.market_data import HistoricalMarketData
from siavash.core.interfaces import Broker, Clock
from siavash.core.models import Balance, Fill, Order, OrderRequest
from siavash.core.symbols import split
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.exchange.account import GuardLike, prepare_order
from siavash.exchange.errors import InsufficientBalance, OrderNotFound
from siavash.exchange.markets import MarketRegistry

ZERO = Decimal(0)


class BacktestBroker(Broker):
    def __init__(
        self,
        md: HistoricalMarketData,
        guard: GuardLike,
        registry: MarketRegistry,
        clock: Clock,
        maker_fee_pct: Decimal | float | str = "0.35",
        taker_fee_pct: Decimal | float | str = "0.35",
        extra_slippage_pct: float = 0.05,
        initial_balances: Mapping[str, Decimal | int | str] | None = None,
    ) -> None:
        self.md = md
        self.guard = guard
        self.registry = registry
        self.clock = clock
        self.maker = Decimal(str(maker_fee_pct)) / 100
        self.taker = Decimal(str(taker_fee_pct)) / 100
        self.reserve_rate = max(self.maker, self.taker)
        self.extra_slip = Decimal(str(extra_slippage_pct)) / 100
        self.free: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in (initial_balances or {"TMN": 5_000_000}).items()
        }
        self.locked: dict[str, Decimal] = {}
        self.orders: dict[str, Order] = {}
        self.fills: list[Fill] = []
        self.placed: list[OrderRequest] = []
        self._seq = 0

    @classmethod
    def from_config(
        cls, cfg: Any, md: HistoricalMarketData, guard: GuardLike, registry: MarketRegistry, clock: Clock
    ) -> BacktestBroker:
        fees = cfg.risk.fees
        maker = fees.maker_pct if fees.maker_pct is not None else fees.taker_pct
        return cls(
            md,
            guard,
            registry,
            clock,
            maker,
            fees.taker_pct,
            cfg.base.bt.extra_slippage_pct,
            cfg.base.paper.initial_balances,
        )

    # ------------------------------------------------------------------ helpers
    def _add(self, book: dict[str, Decimal], asset: str, amt: Decimal) -> None:
        book[asset] = book.get(asset, ZERO) + amt

    def _slip(self, symbol: str) -> Decimal:
        return Decimal(str(self.md.spread_pct_fn(symbol))) / 200 + self.extra_slip

    def _apply_fill(self, coid: str, price: Decimal, qty: Decimal, maker: bool, ts: datetime) -> Fill:
        o = self.orders[coid]
        base, quote = split(o.symbol)
        notional = price * qty
        fee = notional * (self.maker if maker else self.taker)
        if o.side is Side.BUY:
            if o.type is OrderType.LIMIT:
                assert o.price is not None
                reserved = qty * o.price * (1 + self.reserve_rate)
                self._add(self.locked, quote, -reserved)
                self._add(self.free, quote, reserved - notional - fee)
            else:
                self._add(self.free, quote, -(notional + fee))
            self._add(self.free, base, qty)
        else:
            if o.type is OrderType.LIMIT:
                self._add(self.locked, base, -qty)
            else:
                self._add(self.free, base, -qty)
            self._add(self.free, quote, notional - fee)
        filled = o.filled_qty + qty
        avg = ((o.avg_fill_price or ZERO) * o.filled_qty + notional) / filled
        self.orders[coid] = o.model_copy(
            update={
                "filled_qty": filled,
                "avg_fill_price": avg,
                "status": OrderStatus.FILLED if filled >= o.qty else OrderStatus.PARTIALLY_FILLED,
                "updated_at": ts,
            }
        )
        f = Fill(
            fill_id=f"B{o.order_id}-{len(self.fills) + 1}",
            order_id=o.order_id,
            client_order_id=coid,
            symbol=o.symbol,
            side=o.side,
            price=price,
            qty=qty,
            fee=fee,
            fee_asset=quote,
            ts=ts,
            is_maker=maker,
        )
        self.fills.append(f)
        return f

    def _try_aggressive(self, coid: str) -> None:
        o = self.orders[coid]
        ref = self.md.current_open(o.symbol)
        if ref is None:
            return
        slip = self._slip(o.symbol)
        px = Decimal(str(ref)) * (1 + slip if o.side is Side.BUY else 1 - slip)
        px = self.registry.round_price(
            o.symbol, px, Side.SELL if o.side is Side.BUY else Side.BUY
        )  # round against us
        if o.price is not None and (
            (o.side is Side.BUY and px > o.price) or (o.side is Side.SELL and px < o.price)
        ):
            return
        self._apply_fill(coid, px, o.qty - o.filled_qty, maker=False, ts=self.clock.now())

    def _crosses(self, req: OrderRequest) -> bool:
        if req.type is OrderType.MARKET or req.price is None:
            return True
        last = self.md.last_close(req.symbol)
        if last is None:
            return False
        half = Decimal(str(self.md.spread_pct_fn(req.symbol))) / 200
        ask = Decimal(str(last)) * (1 + half)
        bid = Decimal(str(last)) * (1 - half)
        return req.price >= ask if req.side is Side.BUY else req.price <= bid

    def process(self) -> list[Fill]:
        """Fill resting LIMIT orders from candles that started after placement and have closed."""
        now_s = self.clock.now().timestamp()
        new: list[Fill] = []
        for coid, o in list(self.orders.items()):
            if o.status.is_terminal or o.type is not OrderType.LIMIT or o.price is None:
                continue
            start = o.created_at.timestamp()
            for close_s, _op, hi, lo, _cl in self.md.bars_between(o.symbol, start, now_s):
                hit = lo < float(o.price) if o.side is Side.BUY else hi > float(o.price)
                if hit:
                    new.append(
                        self._apply_fill(
                            coid,
                            o.price,
                            o.qty - o.filled_qty,
                            True,
                            datetime.fromtimestamp(min(close_s, now_s), tz=UTC),
                        )
                    )
                    break
        return new

    # ------------------------------------------------------------------ Broker
    async def place_order(self, req: OrderRequest) -> Order:
        if req.client_order_id in self.orders:
            return self.orders[req.client_order_id]
        req = prepare_order(req, self.registry, self.guard)
        base, quote = split(req.symbol)
        ref = self.md.current_open(req.symbol) or self.md.last_close(req.symbol) or 0.0
        est_price = req.price if req.price is not None else Decimal(str(ref)) * (1 + self._slip(req.symbol))
        if req.side is Side.BUY:
            need = req.qty * est_price * (1 + self.reserve_rate)
            if self.free.get(quote, ZERO) < need:
                raise InsufficientBalance(f"backtest: need {need} {quote}")
        elif self.free.get(base, ZERO) < req.qty:
            raise InsufficientBalance(f"backtest: need {req.qty} {base}")
        if req.type is OrderType.LIMIT:
            assert req.price is not None
            if req.side is Side.BUY:
                amt = req.qty * req.price * (1 + self.reserve_rate)
                self._add(self.free, quote, -amt)
                self._add(self.locked, quote, amt)
            else:
                self._add(self.free, base, -req.qty)
                self._add(self.locked, base, req.qty)
        self._seq += 1
        now = self.clock.now()
        self.orders[req.client_order_id] = Order(
            order_id=str(self._seq),
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
        self.placed.append(req)
        if self._crosses(req):
            self._try_aggressive(req.client_order_id)
            o = self.orders[req.client_order_id]
            if req.type is OrderType.MARKET and not o.status.is_terminal:
                self.orders[req.client_order_id] = o.model_copy(update={"status": OrderStatus.CANCELED})
        return self.orders[req.client_order_id]

    async def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        self.process()
        o = self.orders.get(client_order_id)
        if o is None:
            raise OrderNotFound(client_order_id)
        if o.status.is_terminal:
            return o
        base, quote = split(o.symbol)
        remaining = o.qty - o.filled_qty
        if o.type is OrderType.LIMIT and remaining > 0:
            if o.side is Side.BUY:
                assert o.price is not None
                amt = remaining * o.price * (1 + self.reserve_rate)
                self._add(self.locked, quote, -amt)
                self._add(self.free, quote, amt)
            else:
                self._add(self.locked, base, -remaining)
                self._add(self.free, base, remaining)
        self.orders[client_order_id] = o.model_copy(
            update={"status": OrderStatus.CANCELED, "updated_at": self.clock.now()}
        )
        return self.orders[client_order_id]

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        self.process()
        return self.orders.get(client_order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        self.process()
        return [
            o
            for o in self.orders.values()
            if not o.status.is_terminal and (symbol is None or o.symbol == symbol)
        ]

    async def get_balances(self) -> dict[str, Balance]:
        assets = sorted(set(self.free) | set(self.locked))
        return {
            a: Balance(asset=a, free=self.free.get(a, ZERO), locked=self.locked.get(a, ZERO)) for a in assets
        }

    async def get_fills(self, since: datetime) -> list[Fill]:
        self.process()
        return [f for f in self.fills if f.ts >= since]
