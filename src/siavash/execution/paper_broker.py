"""PaperBroker (T-1001): simulated execution against the *real* live order book and trade feed.

Nothing is ever sent to the exchange. Rules:
- MARKET: walks the current order book (VWAP over levels), taker fee; unfilled remainder is cancelled.
- LIMIT BUY at P: if best ask ≤ P on placement/check → immediate taker fill over asks ≤ P; otherwise
  it fills (maker, at P) only from public trades printed *after* placement with price < P, up to the
  traded quantity (partial fills). SELL is symmetric.
- Funds are reserved on placement; fees are charged in the quote asset (TMN).
- Same ``client_order_id`` returns the existing order; SymbolGuard + rounding via ``prepare_order``.
- State (balances, orders, fills, trade watermarks) persists in SQLite (``state/paper.sqlite``).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from siavash.core.interfaces import Broker, Clock, MarketData
from siavash.core.models import Balance, Fill, Order, OrderBook, OrderRequest
from siavash.core.symbols import split
from siavash.core.types import OrderStatus, OrderType, Side
from siavash.exchange.account import GuardLike, prepare_order
from siavash.exchange.errors import InsufficientBalance, OrderNotFound
from siavash.exchange.markets import MarketRegistry
from siavash.logging import get_logger

log = get_logger(__name__)
ZERO = Decimal(0)


class _PaperState:
    """Tiny SQLite persistence for the paper account."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self.conn.executescript(
                "PRAGMA journal_mode=WAL;"
                "CREATE TABLE IF NOT EXISTS balances(asset TEXT PRIMARY KEY, free TEXT, locked TEXT);"
                "CREATE TABLE IF NOT EXISTS orders(coid TEXT PRIMARY KEY, data TEXT, meta TEXT);"
                "CREATE TABLE IF NOT EXISTS fills(fill_id TEXT PRIMARY KEY, ts TEXT, data TEXT);"
                "CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);"
            )
            self.conn.commit()

    def load(
        self,
    ) -> tuple[
        dict[str, Decimal], dict[str, Decimal], dict[str, Order], dict[str, dict[str, Any]], list[Fill], int
    ]:
        with self._lock:
            bal = self.conn.execute("SELECT asset, free, locked FROM balances").fetchall()
            ords = self.conn.execute("SELECT data, meta FROM orders").fetchall()
            fills = self.conn.execute("SELECT data FROM fills ORDER BY ts").fetchall()
            seq = self.conn.execute("SELECT v FROM kv WHERE k='seq'").fetchone()
        free = {a: Decimal(f) for a, f, _ in bal}
        locked = {a: Decimal(lk) for a, _, lk in bal}
        orders, metas = {}, {}
        for data, meta in ords:
            o = Order.model_validate_json(data)
            orders[o.client_order_id] = o
            metas[o.client_order_id] = json.loads(meta)
        return (
            free,
            locked,
            orders,
            metas,
            [Fill.model_validate_json(d) for (d,) in fills],
            int(seq[0]) if seq else 0,
        )

    def save(
        self,
        free: dict[str, Decimal],
        locked: dict[str, Decimal],
        orders: dict[str, Order],
        metas: dict[str, dict[str, Any]],
        new_fills: list[Fill],
        seq: int,
    ) -> None:
        with self._lock:
            assets = set(free) | set(locked)
            self.conn.executemany(
                "INSERT OR REPLACE INTO balances VALUES (?,?,?)",
                [(a, str(free.get(a, ZERO)), str(locked.get(a, ZERO))) for a in assets],
            )
            self.conn.executemany(
                "INSERT OR REPLACE INTO orders VALUES (?,?,?)",
                [(c, o.model_dump_json(), json.dumps(metas.get(c, {}))) for c, o in orders.items()],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO fills VALUES (?,?,?)",
                [(f.fill_id, f.ts.isoformat(), f.model_dump_json()) for f in new_fills],
            )
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES ('seq', ?)", (str(seq),))
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()


class PaperBroker(Broker):
    def __init__(
        self,
        market_data: MarketData,
        guard: GuardLike,
        registry: MarketRegistry,
        clock: Clock,
        maker_fee_pct: Decimal | float | str = "0.35",
        taker_fee_pct: Decimal | float | str = "0.35",
        initial_balances: Mapping[str, Decimal | int | str] | None = None,
        state_path: str | Path = "state/paper.sqlite",
        live_data: Any = None,
        fill_check_seconds: float = 5.0,
        book_max_age_s: float = 30.0,
    ) -> None:
        self.md = market_data
        self.live_data = live_data
        self.guard = guard
        self.registry = registry
        self.clock = clock
        self.maker = Decimal(str(maker_fee_pct)) / 100
        self.taker = Decimal(str(taker_fee_pct)) / 100
        self.reserve_rate = max(self.maker, self.taker)
        self.fill_check_seconds = fill_check_seconds
        self.book_max_age_s = book_max_age_s
        self.state = _PaperState(state_path)
        self.free, self.locked, self.orders, self.meta, self.fills, self._seq = self.state.load()
        if not self.free and not self.locked:
            self.free = {k: Decimal(str(v)) for k, v in (initial_balances or {"TMN": 5_000_000}).items()}
            self._persist([])
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(
        cls,
        cfg: Any,
        market_data: MarketData,
        guard: GuardLike,
        registry: MarketRegistry,
        clock: Clock,
        live_data: Any = None,
    ) -> PaperBroker:
        fees = cfg.risk.fees
        maker = fees.maker_pct if fees.maker_pct is not None else fees.taker_pct
        return cls(
            market_data,
            guard,
            registry,
            clock,
            maker,
            fees.taker_pct,
            cfg.base.paper.initial_balances,
            Path(cfg.base.paths.state) / "paper.sqlite",
            live_data,
            cfg.base.paper.fill_check_seconds,
        )

    # ------------------------------------------------------------------ helpers
    def _add(self, book: dict[str, Decimal], asset: str, amt: Decimal) -> None:
        book[asset] = book.get(asset, ZERO) + amt

    def _persist(self, new_fills: list[Fill]) -> None:
        self.state.save(self.free, self.locked, self.orders, self.meta, new_fills, self._seq)

    async def _book(self, symbol: str) -> OrderBook:
        if self.live_data is not None:
            b = self.live_data.get_book(symbol)
            if b is not None and (self.clock.now() - b.ts).total_seconds() <= self.book_max_age_s:
                return b
        return await self.md.get_orderbook(symbol)

    def _apply_fill(self, coid: str, price: Decimal, qty: Decimal, maker: bool) -> Fill:
        o = self.orders[coid]
        base, quote = split(o.symbol)
        rate = self.maker if maker else self.taker
        notional = price * qty
        fee = notional * rate
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
        status = OrderStatus.FILLED if filled >= o.qty else OrderStatus.PARTIALLY_FILLED
        now = self.clock.now()
        self.orders[coid] = o.model_copy(
            update={"filled_qty": filled, "avg_fill_price": avg, "status": status, "updated_at": now}
        )
        f = Fill(
            fill_id=f"P{o.order_id}-{len(self.fills) + 1}",
            order_id=o.order_id,
            client_order_id=coid,
            symbol=o.symbol,
            side=o.side,
            price=price,
            qty=qty,
            fee=fee,
            fee_asset=quote,
            ts=now,
            is_maker=maker,
        )
        self.fills.append(f)
        log.info(
            "paper_fill",
            symbol=o.symbol,
            side=o.side.value,
            price=str(price),
            qty=str(qty),
            maker=maker,
            client_order_id=coid,
        )
        return f

    def _release(self, coid: str) -> None:
        o = self.orders[coid]
        base, quote = split(o.symbol)
        remaining = o.qty - o.filled_qty
        if o.type is not OrderType.LIMIT or remaining <= 0:
            return
        if o.side is Side.BUY:
            assert o.price is not None
            amt = remaining * o.price * (1 + self.reserve_rate)
            self._add(self.locked, quote, -amt)
            self._add(self.free, quote, amt)
        else:
            self._add(self.locked, base, -remaining)
            self._add(self.free, base, remaining)

    def _cross(self, coid: str, book: OrderBook) -> list[Fill]:
        """Taker fills against book levels that cross the order price (or all levels for MARKET)."""
        o = self.orders[coid]
        out: list[Fill] = []
        remaining = o.qty - o.filled_qty
        levels = book.asks if o.side is Side.BUY else book.bids
        for lv in levels:
            if remaining <= 0:
                break
            if o.price is not None and (
                (o.side is Side.BUY and lv.price > o.price) or (o.side is Side.SELL and lv.price < o.price)
            ):
                break
            q = self.registry.round_qty(o.symbol, min(lv.qty, remaining))
            if q <= 0:
                continue
            out.append(self._apply_fill(coid, lv.price, q, maker=False))
            remaining -= q
        return out

    # ------------------------------------------------------------------ Broker
    async def place_order(self, req: OrderRequest) -> Order:
        async with self._lock:
            if req.client_order_id in self.orders:
                return self.orders[req.client_order_id]
            req = prepare_order(req, self.registry, self.guard)
            base, quote = split(req.symbol)
            book = await self._book(req.symbol)
            if req.type is OrderType.LIMIT:
                assert req.price is not None
                if req.side is Side.BUY:
                    need = req.qty * req.price * (1 + self.reserve_rate)
                    if self.free.get(quote, ZERO) < need:
                        raise InsufficientBalance(f"paper: need {need} {quote}")
                    self._add(self.free, quote, -need)
                    self._add(self.locked, quote, need)
                else:
                    if self.free.get(base, ZERO) < req.qty:
                        raise InsufficientBalance(f"paper: need {req.qty} {base}")
                    self._add(self.free, base, -req.qty)
                    self._add(self.locked, base, req.qty)
            else:
                levels = book.asks if req.side is Side.BUY else book.bids
                if req.side is Side.BUY:
                    est, left = ZERO, req.qty
                    for lv in levels:
                        q = min(lv.qty, left)
                        est += q * lv.price
                        left -= q
                        if left <= 0:
                            break
                    if self.free.get(quote, ZERO) < est * (1 + self.taker):
                        raise InsufficientBalance(f"paper: need {est} {quote}")
                elif self.free.get(base, ZERO) < req.qty:
                    raise InsufficientBalance(f"paper: need {req.qty} {base}")
            self._seq += 1
            now = self.clock.now()
            self.orders[req.client_order_id] = Order(
                order_id=f"{self._seq}",
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
            self.meta[req.client_order_id] = {
                "watermark": now.isoformat(),
                "decision_id": req.decision_id,
                "purpose": req.purpose,
            }
            fills = self._cross(req.client_order_id, book)
            o = self.orders[req.client_order_id]
            if req.type is OrderType.MARKET and not o.status.is_terminal:
                self.orders[req.client_order_id] = o.model_copy(
                    update={"status": OrderStatus.CANCELED, "updated_at": now}
                )
            self._persist(fills)
            return self.orders[req.client_order_id]

    async def check_open_orders(self) -> list[Fill]:
        """Fill resting LIMIT orders from the book (crossing) or public trades printed after placement."""
        new: list[Fill] = []
        async with self._lock:
            for coid, o in list(self.orders.items()):
                if o.status.is_terminal or o.type is not OrderType.LIMIT:
                    continue
                try:
                    book = await self._book(o.symbol)
                    trades = await self.md.get_recent_trades(o.symbol)
                except Exception as e:
                    log.warning("paper_check_failed", symbol=o.symbol, error=str(e))
                    continue
                new += self._cross(coid, book)
                o = self.orders[coid]
                if o.status.is_terminal:
                    continue
                wm = datetime.fromisoformat(self.meta[coid]["watermark"])
                assert o.price is not None
                for t in sorted(trades, key=lambda x: x.ts):
                    if t.ts <= wm:
                        continue
                    wm = t.ts
                    remaining = o.qty - o.filled_qty
                    if remaining <= 0:
                        break
                    hit = t.price < o.price if o.side is Side.BUY else t.price > o.price
                    if hit:
                        q = self.registry.round_qty(o.symbol, min(t.qty, remaining))
                        if q > 0:
                            new.append(self._apply_fill(coid, o.price, q, maker=True))
                            o = self.orders[coid]
                self.meta[coid]["watermark"] = wm.isoformat()
            self._persist(new)
        return new

    async def run(self, stop: asyncio.Event) -> None:
        """Background loop: check resting orders every ``fill_check_seconds``."""
        while not stop.is_set():
            await self.check_open_orders()
            await self.clock.sleep(self.fill_check_seconds)

    async def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        async with self._lock:
            o = self.orders.get(client_order_id)
            if o is None:
                raise OrderNotFound(client_order_id)
            if not o.status.is_terminal:
                self._release(client_order_id)
                self.orders[client_order_id] = o.model_copy(
                    update={"status": OrderStatus.CANCELED, "updated_at": self.clock.now()}
                )
                self._persist([])
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
        assets = sorted(set(self.free) | set(self.locked))
        return {
            a: Balance(asset=a, free=self.free.get(a, ZERO), locked=self.locked.get(a, ZERO)) for a in assets
        }

    async def get_fills(self, since: datetime) -> list[Fill]:
        return [f for f in self.fills if f.ts >= since]

    def close(self) -> None:
        self.state.close()
