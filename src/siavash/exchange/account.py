"""Account and order endpoints (T-105).

Every order is rounded with the ``MarketRegistry``, checked against minimums and passed through
the ``SymbolGuard`` **before** any HTTP request. Order placement is never retried here (T-802).
Because client-order-id support is unverified, the ``order_id ↔ client_order_id`` mapping is kept
locally in ``OrderIdMap`` (SQLite under ``state/``).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from siavash.core.interfaces import Clock
from siavash.core.models import Balance, Fill, Order, OrderRequest
from siavash.core.types import OrderType, Side
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.errors import BadRequest, BelowMinimum, OrderNotFound
from siavash.exchange.markets import MarketRegistry
from siavash.logging import get_logger

log = get_logger(__name__)


class GuardLike(Protocol):
    def assert_tradable(self, symbol: str, side: Side | None = None) -> None: ...


class OrderIdMap:
    """Persistent ``client_order_id ↔ order_id`` map (SQLite, thread-safe)."""

    def __init__(self, path: str | Path = "state/id_map.sqlite") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS id_map (client_order_id TEXT PRIMARY KEY, order_id TEXT, "
                "symbol TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_id_map_order ON id_map(order_id)")
            self._conn.commit()

    def put(
        self, client_order_id: str, order_id: str | None, symbol: str, ts: datetime | None = None
    ) -> None:
        """Insert or update a mapping (an existing ``order_id`` is never overwritten with NULL)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO id_map(client_order_id, order_id, symbol, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(client_order_id) DO UPDATE SET order_id=COALESCE(excluded.order_id, id_map.order_id)",
                (client_order_id, order_id, symbol, (ts or datetime.now(UTC)).isoformat()),
            )
            self._conn.commit()

    def order_id_for(self, client_order_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT order_id FROM id_map WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
        return row[0] if row else None

    def client_id_for(self, order_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT client_order_id FROM id_map WHERE order_id=?", (order_id,)
            ).fetchone()
        return row[0] if row else None

    def known_client_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._conn.execute("SELECT client_order_id FROM id_map")}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class WallexAccount:
    """Private endpoints: balances, orders, user trades."""

    def __init__(
        self,
        http: WallexHttp,
        registry: MarketRegistry,
        id_map: OrderIdMap,
        guard: GuardLike,
        clock: Clock | None = None,
    ) -> None:
        self.http = http
        self.registry = registry
        self.id_map = id_map
        self.guard = guard
        self.clock = clock

    def _now(self) -> datetime:
        return self.clock.now() if self.clock else datetime.now(UTC)

    def _with_client_id(self, order: Order, fallback: str | None = None) -> Order:
        if order.client_order_id:
            return order
        cid = (self.id_map.client_id_for(order.order_id) if order.order_id else None) or fallback or ""
        return order.model_copy(update={"client_order_id": cid}) if cid else order

    def prepare(self, req: OrderRequest) -> OrderRequest:
        """Guard + rounding + minimum checks (no I/O). Raises before anything is sent."""
        if not req.decision_id:
            raise BadRequest("order without decision_id")
        self.guard.assert_tradable(req.symbol, req.side)  # mandatory SymbolGuard (rule 4)
        qty = self.registry.round_qty(req.symbol, req.qty)
        price = req.price
        if req.type is OrderType.LIMIT:
            if price is None or price <= 0:
                raise BadRequest("LIMIT order requires a positive price")
            price = self.registry.round_price(req.symbol, price, req.side)
        ref_price = price
        if ref_price is None:
            m = self.registry.get(req.symbol)
            if qty <= 0 or qty < m.min_qty:
                raise BelowMinimum(f"{req.symbol}: qty {qty} below minimum {m.min_qty}")
        elif not self.registry.check_min(req.symbol, qty, ref_price):
            raise BelowMinimum(f"{req.symbol}: qty {qty} @ {ref_price} below market minimum")
        return req.model_copy(update={"qty": qty, "price": price})

    async def get_balances(self) -> dict[str, Balance]:
        return ep.parse_balance(await self.http.get(ep.Paths.BALANCES, private=True))

    async def place_order(self, req: OrderRequest) -> Order:
        """Validate, then POST once (no automatic retry)."""
        req = self.prepare(req)
        self.id_map.put(req.client_order_id, None, req.symbol, self._now())
        raw = await self.http.post(ep.Paths.ORDERS, json=ep.order_body(req))
        order = ep.parse_order(raw, fallback_client_order_id=req.client_order_id)
        if order.client_order_id != req.client_order_id:
            order = order.model_copy(update={"client_order_id": req.client_order_id})
        self.id_map.put(req.client_order_id, order.order_id, req.symbol, self._now())
        log.info(
            "order_placed",
            symbol=req.symbol,
            side=req.side.value,
            client_order_id=req.client_order_id,
            order_id=order.order_id,
        )
        return order

    async def cancel_order(
        self, symbol: str, client_order_id: str | None = None, order_id: str | None = None
    ) -> Order:
        """Cancel by exchange id (looked up from the id map) or by client id."""
        if order_id is None and client_order_id:
            order_id = self.id_map.order_id_for(client_order_id)
        if order_id is None and client_order_id is None:
            raise OrderNotFound("order_id or client_order_id required")
        raw = await self.http.delete(ep.Paths.ORDERS, json=ep.cancel_body(order_id, client_order_id))
        result = ep._result(raw)
        if isinstance(result, dict) and ep.F["ord_symbol"] in result:
            return self._with_client_id(ep.parse_order(raw), client_order_id)
        got = await self.get_order(symbol, client_order_id or "", order_id=order_id)
        if got is None:
            raise OrderNotFound(f"{client_order_id or order_id}")
        return got

    async def get_order(self, symbol: str, client_order_id: str, order_id: str | None = None) -> Order | None:
        """Order by exchange id (from the id map); falls back to scanning open orders by client id."""
        order_id = order_id or self.id_map.order_id_for(client_order_id)
        if order_id:
            try:
                raw = await self.http.get(ep.Paths.ORDER.format(order_id=order_id), private=True)
            except OrderNotFound:
                return None
            return self._with_client_id(ep.parse_order(raw), client_order_id or None)
        for o in await self.get_open_orders(symbol):
            if o.client_order_id == client_order_id:
                return o
        return None

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params: dict[str, Any] | None = ep.symbol_params(symbol) if symbol else None
        raw = await self.http.get(ep.Paths.OPEN_ORDERS, params=params, private=True)
        return [self._with_client_id(o) for o in ep.parse_open_orders(raw)]

    async def get_my_trades(self, since: datetime, symbol: str | None = None) -> list[Fill]:
        params: dict[str, Any] | None = ep.symbol_params(symbol) if symbol else None
        raw = await self.http.get(ep.Paths.MY_TRADES, params=params, private=True)
        out = []
        for f in ep.parse_fills(raw):
            if f.ts < since:
                continue
            if not f.client_order_id and f.order_id:
                cid = self.id_map.client_id_for(f.order_id)
                if cid:
                    f = f.model_copy(update={"client_order_id": cid})
            out.append(f)
        return out
