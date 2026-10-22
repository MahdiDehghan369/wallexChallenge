"""Stateful fake Wallex private API for respx (speaks the Swagger schema in ``endpoints.F``).

Orders are keyed by ``client_id`` like the real API; fills live in each order's ``fills[]``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from siavash.core.symbols import to_canonical
from siavash.exchange import endpoints as ep

F = ep.F
ORDER_PREFIX = ep.Paths.ORDERS + "/"


class FakeWallexServer:
    def __init__(self, balances: dict[str, Decimal], fee_pct: Decimal = Decimal("0.35")) -> None:
        self.balances = {k: Decimal(v) for k, v in balances.items()}
        self.fee_rate = fee_pct / 100
        self.orders: dict[str, dict[str, Any]] = {}
        self.posts = 0
        self._ticks = 0

    def _now(self) -> str:
        # strictly increasing timestamps so fills of one order never collide
        self._ticks += 1
        return datetime.fromtimestamp(datetime.now(UTC).timestamp() + self._ticks / 1000, tz=UTC).isoformat()

    # ------------------------------------------------------------------ test controls
    def fill(self, client_id: str, price: Decimal, qty: Decimal) -> None:
        o = self.orders[client_id]
        sym = to_canonical(o[F["ord_symbol"]])
        base, quote = sym.split("/")
        notional = price * qty
        fee = notional * self.fee_rate
        buy = o[F["ord_side"]] == "BUY"
        if buy:
            self.balances[quote] = self.balances.get(quote, Decimal(0)) - notional - fee
            self.balances[base] = self.balances.get(base, Decimal(0)) + qty
        else:
            self.balances[base] = self.balances.get(base, Decimal(0)) - qty
            self.balances[quote] = self.balances.get(quote, Decimal(0)) + notional - fee
        filled = Decimal(o[F["ord_filled"]]) + qty
        o[F["ord_filled"]] = str(filled)
        o[F["ord_avg_price"]] = str(price)
        done = filled >= Decimal(o[F["ord_qty"]])
        o[F["ord_status"]] = "FILLED" if done else "PARTIALLY_FILLED"
        o[F["ord_active"]] = not done
        o[F["ord_fills"]].append(
            {
                F["fill_price"]: str(price),
                F["fill_qty"]: str(qty),
                F["fill_fee"]: str(fee),
                F["fill_fee_asset"]: quote,
                F["fill_is_buyer"]: buy,
                F["fill_ts"]: self._now(),
            }
        )

    # ------------------------------------------------------------------ http
    def _ok(self, result: Any, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json={"success": True, "message": "ok", "result": result})

    def _not_found(self) -> httpx.Response:
        return httpx.Response(
            404, json={"success": False, "code": 404, "message": "order not found", "result": {}}
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == ep.Paths.ORDERS and method == "POST":
            self.posts += 1
            body = json.loads(request.content)
            cid = body[F["req_client_id"]]
            self.orders[cid] = {
                F["ord_client_id"]: cid,
                F["ord_symbol"]: body[F["req_symbol"]],
                F["ord_side"]: body[F["req_side"]],
                F["ord_type"]: body[F["req_type"]],
                F["ord_price"]: body.get(F["req_price"]),
                F["ord_qty"]: body[F["req_qty"]],
                F["ord_filled"]: "0",
                F["ord_status"]: "NEW",
                F["ord_active"]: True,
                F["ord_created"]: self._now(),
                F["ord_fills"]: [],
            }
            return self._ok(self.orders[cid], 201)
        if path == ep.Paths.ORDERS and method == "GET":
            return self._ok({"orders": list(self.orders.values())})
        if path.startswith(ORDER_PREFIX):
            cid = path[len(ORDER_PREFIX) :]
            if cid not in self.orders:
                return self._not_found()
            o = self.orders[cid]
            if method == "DELETE" and o[F["ord_active"]]:
                o[F["ord_status"]] = "CANCELED"
                o[F["ord_active"]] = False
            return self._ok(o)
        if path == ep.Paths.OPEN_ORDERS:
            sym = request.url.params.get("symbol")
            rows = [
                o
                for o in self.orders.values()
                if o[F["ord_active"]] and (sym is None or o[F["ord_symbol"]] == sym)
            ]
            return self._ok({"orders": rows})
        if path == ep.Paths.BALANCES:
            return self._ok(
                {
                    F["balances_root"]: {
                        a: {F["bal_asset"]: a, F["bal_total"]: str(v), F["bal_locked"]: "0"}
                        for a, v in self.balances.items()
                    }
                }
            )
        return httpx.Response(404, json={"success": False, "message": f"unknown route {method} {path}"})
