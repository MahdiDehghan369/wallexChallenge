"""Persistent engine state (T-1101).

``SqliteStore`` (WAL, ``state/state.sqlite``) and ``InMemoryStore`` share one async interface:
positions (JSON), orders, id_map, breaker state, a small key/value table (peak equity, counters,
last regime, cooldowns, operator flags) and the equity series.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite

from siavash.core.models import Order, Position
from siavash.core.types import BreakerState

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS positions(position_id TEXT PRIMARY KEY, symbol TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders(client_order_id TEXT PRIMARY KEY, symbol TEXT, status TEXT, data TEXT NOT NULL, meta TEXT);
CREATE TABLE IF NOT EXISTS id_map(client_order_id TEXT PRIMARY KEY, order_id TEXT, symbol TEXT);
CREATE TABLE IF NOT EXISTS breaker(id INTEGER PRIMARY KEY CHECK (id = 1), state TEXT, reason TEXT, since TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity TEXT NOT NULL);
"""


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)}")


class InMemoryStore:
    """Same interface as ``SqliteStore``; used by tests and backtests."""

    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}
        self.orders: dict[str, tuple[Order, dict[str, Any]]] = {}
        self.id_map: dict[str, tuple[str | None, str]] = {}
        self.breaker: tuple[BreakerState, str, datetime] | None = None
        self.kv: dict[str, Any] = {}
        self.equity: dict[datetime, Decimal] = {}

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save_position(self, p: Position) -> None:
        self.positions[p.position_id] = p.model_copy(deep=True)

    async def load_positions(self) -> list[Position]:
        return [p.model_copy(deep=True) for p in self.positions.values()]

    async def save_order(self, order: Order, meta: dict[str, Any] | None = None) -> None:
        self.orders[order.client_order_id] = (order, dict(meta or {}))

    async def load_orders(self) -> list[tuple[Order, dict[str, Any]]]:
        return list(self.orders.values())

    async def put_id(self, client_order_id: str, order_id: str | None, symbol: str) -> None:
        old = self.id_map.get(client_order_id)
        self.id_map[client_order_id] = (order_id or (old[0] if old else None), symbol)

    async def get_order_id(self, client_order_id: str) -> str | None:
        v = self.id_map.get(client_order_id)
        return v[0] if v else None

    async def set_breaker(self, state: BreakerState, reason: str, since: datetime) -> None:
        self.breaker = (state, reason, since)

    async def get_breaker(self) -> tuple[BreakerState, str, datetime] | None:
        return self.breaker

    async def kv_set(self, key: str, value: Any) -> None:
        self.kv[key] = json.loads(json.dumps(value, default=_json_default))

    async def kv_get(self, key: str, default: Any = None) -> Any:
        return self.kv.get(key, default)

    async def add_equity(self, ts: datetime, equity: Decimal) -> None:
        self.equity[ts] = Decimal(equity)

    async def equity_series(self, since: datetime | None = None) -> list[tuple[datetime, Decimal]]:
        return sorted((t, e) for t, e in self.equity.items() if since is None or t >= since)


class SqliteStore:
    def __init__(self, path: str | Path = "state/state.sqlite") -> None:
        self.path = str(path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            await self.open()
        assert self._db is not None
        return self._db

    async def _exec(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        db = await self._conn()
        await db.execute(sql, args)
        await db.commit()

    async def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        db = await self._conn()
        async with db.execute(sql, args) as cur:
            return [tuple(r) for r in await cur.fetchall()]

    async def save_position(self, p: Position) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO positions VALUES (?,?,?,?)",
            (p.position_id, p.symbol, p.status.value, p.model_dump_json()),
        )

    async def load_positions(self) -> list[Position]:
        return [Position.model_validate_json(r[0]) for r in await self._rows("SELECT data FROM positions")]

    async def save_order(self, order: Order, meta: dict[str, Any] | None = None) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?)",
            (
                order.client_order_id,
                order.symbol,
                order.status.value,
                order.model_dump_json(),
                json.dumps(meta or {}, default=_json_default),
            ),
        )

    async def load_orders(self) -> list[tuple[Order, dict[str, Any]]]:
        return [
            (Order.model_validate_json(d), json.loads(m or "{}"))
            for d, m in await self._rows("SELECT data, meta FROM orders")
        ]

    async def put_id(self, client_order_id: str, order_id: str | None, symbol: str) -> None:
        await self._exec(
            "INSERT INTO id_map VALUES (?,?,?) ON CONFLICT(client_order_id) DO UPDATE SET "
            "order_id=COALESCE(excluded.order_id, id_map.order_id)",
            (client_order_id, order_id, symbol),
        )

    async def get_order_id(self, client_order_id: str) -> str | None:
        rows = await self._rows("SELECT order_id FROM id_map WHERE client_order_id=?", (client_order_id,))
        return rows[0][0] if rows else None

    async def set_breaker(self, state: BreakerState, reason: str, since: datetime) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO breaker VALUES (1,?,?,?)", (state.value, reason, since.isoformat())
        )

    async def get_breaker(self) -> tuple[BreakerState, str, datetime] | None:
        rows = await self._rows("SELECT state, reason, since FROM breaker WHERE id=1")
        if not rows:
            return None
        s, r, t = rows[0]
        return BreakerState(s), r, datetime.fromisoformat(t)

    async def kv_set(self, key: str, value: Any) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, default=_json_default))
        )

    async def kv_get(self, key: str, default: Any = None) -> Any:
        rows = await self._rows("SELECT v FROM kv WHERE k=?", (key,))
        return json.loads(rows[0][0]) if rows else default

    async def add_equity(self, ts: datetime, equity: Decimal) -> None:
        await self._exec("INSERT OR REPLACE INTO equity VALUES (?,?)", (ts.isoformat(), str(equity)))

    async def equity_series(self, since: datetime | None = None) -> list[tuple[datetime, Decimal]]:
        if since is None:
            rows = await self._rows("SELECT ts, equity FROM equity ORDER BY ts")
        else:
            rows = await self._rows(
                "SELECT ts, equity FROM equity WHERE ts >= ? ORDER BY ts", (since.isoformat(),)
            )
        return [(datetime.fromisoformat(t), Decimal(e)) for t, e in rows]
