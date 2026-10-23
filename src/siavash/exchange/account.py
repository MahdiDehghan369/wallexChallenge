"""Account and order endpoints (T-105).

Every order is rounded with the ``MarketRegistry``, checked against minimums and passed through
the ``SymbolGuard`` **before** any HTTP request. Order placement is never retried here (T-802).
Wallex identifies orders by the ``client_id`` we send (Swagger), so status / cancel use it directly;
``OrderIdMap`` (SQLite under ``state/``) still records every client id we submitted.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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


async def fetch_fees(http: WallexHttp) -> dict[str, tuple[Decimal, Decimal]]:
    """``{symbol: (maker_pct, taker_pct)}`` for this account (``GET /v1/account/fee``, read-only)."""
    return ep.parse_fees(await http.get(ep.Paths.FEE, private=True))


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


def prepare_order(req: OrderRequest, registry: MarketRegistry, guard: GuardLike) -> OrderRequest:
    """Shared pre-trade validation used by every broker (live, paper, backtest).

    decision_id required → SymbolGuard (rule 4) → round qty down / price by side → market minimums.
    """
    if not req.decision_id:
        raise BadRequest("order without decision_id")
    guard.assert_tradable(req.symbol, req.side)
    qty = registry.round_qty(req.symbol, req.qty)
    price = req.price
    if req.type is OrderType.LIMIT:
        if price is None or price <= 0:
            raise BadRequest("LIMIT order requires a positive price")
        price = registry.round_price(req.symbol, price, req.side)
    if price is None:
        m = registry.get(req.symbol)
        if qty <= 0 or qty < m.min_qty:
            raise BelowMinimum(f"{req.symbol}: qty {qty} below minimum {m.min_qty}")
    elif not registry.check_min(req.symbol, qty, price):
        raise BelowMinimum(f"{req.symbol}: qty {qty} @ {price} below market minimum")
    return req.model_copy(update={"qty": qty, "price": price})


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
        return prepare_order(req, self.registry, self.guard)

    async def get_balances(self) -> dict[str, Balance]:
        return ep.parse_balance(await self.http.get(ep.Paths.BALANCES, private=True))

    async def get_fees(self) -> dict[str, tuple[Decimal, Decimal]]:
        """``{symbol: (maker_pct, taker_pct)}`` for this account (read-only)."""
        return await fetch_fees(self.http)

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

    def _client_id(self, client_order_id: str | None, order_id: str | None) -> str:
        """Wallex keys orders by the client id; an ``order_id`` is mapped back through the id map."""
        cid = client_order_id or (self.id_map.client_id_for(order_id) if order_id else None) or order_id
        if not cid:
            raise OrderNotFound("order_id or client_order_id required")
        return cid

    async def cancel_order(
        self, symbol: str, client_order_id: str | None = None, order_id: str | None = None
    ) -> Order:
        """``DELETE /v1/account/orders/{client_id}``."""
        cid = self._client_id(client_order_id, order_id)
        raw = await self.http.delete(ep.order_path(cid))
        result = ep._result(raw)
        if isinstance(result, dict) and ep.F["ord_symbol"] in result:
            return ep.parse_order(raw, fallback_client_order_id=cid)
        got = await self.get_order(symbol, cid)
        if got is None:
            raise OrderNotFound(cid)
        return got

    async def get_order(self, symbol: str, client_order_id: str, order_id: str | None = None) -> Order | None:
        """``GET /v1/account/orders/{client_id}``; None if the exchange does not know it."""
        raw = await self._order_raw(self._client_id(client_order_id or None, order_id))
        return None if raw is None else ep.parse_order(raw)

    async def _order_raw(self, client_order_id: str) -> Any:
        try:
            return await self.http.get(ep.order_path(client_order_id), private=True)
        except OrderNotFound:
            return None

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        params: dict[str, Any] | None = ep.symbol_params(symbol) if symbol else None
        raw = await self.http.get(ep.Paths.OPEN_ORDERS, params=params, private=True)
        return [self._with_client_id(o) for o in ep.parse_open_orders(raw)]

    async def get_my_trades(self, since: datetime, symbol: str | None = None) -> list[Fill]:
        """Fills since ``since``. There is no user-trades endpoint: fills come from ``fills[]`` of the
        order history (paged) and of open orders; an order with executed quantity but no ``fills[]`` in
        the list is re-read by client id. De-duplicated by ``fill_id``."""
        # the history filter is a UTC date; start one day early so no fill near midnight is missed
        start = since - timedelta(days=1)
        rows: list[dict[str, Any]] = []
        for page in range(1, ep.ORDERS_MAX_PAGES + 1):
            raw = await self.http.get(
                ep.Paths.ORDERS, params=ep.orders_history_params(start, page, symbol), private=True
            )
            rows.extend(ep._rows(ep._result(raw)))
            if ep.page_size(raw) < ep.ORDERS_PER_PAGE:
                break
        params: dict[str, Any] | None = ep.symbol_params(symbol) if symbol else None
        rows.extend(
            ep._rows(ep._result(await self.http.get(ep.Paths.OPEN_ORDERS, params=params, private=True)))
        )
        seen: dict[str, Fill] = {}
        for row in rows:
            fills = ep.parse_order_fills(row)
            if not fills and Decimal(str(row.get(ep.F["ord_filled"]) or 0)) > 0:
                detail = await self._order_raw(str(row.get(ep.F["ord_client_id"]) or ""))
                fills = ep.parse_order_fills(detail) if detail is not None else []
            for f in fills:
                if f.ts >= since:
                    seen.setdefault(f.fill_id, f)
        return sorted(seen.values(), key=lambda f: f.ts)
