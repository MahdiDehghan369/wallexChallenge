"""Stateful fake Wallex private API for respx (speaks the MOCK schema in ``endpoints.F``)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from siavash.core.symbols import to_canonical
from siavash.exchange import endpoints as ep

F = ep.F


class FakeWallexServer:
    def __init__(self, balances: dict[str, Decimal], fee_pct: Decimal = Decimal("0.35")) -> None:
        self.balances = {k: Decimal(v) for k, v in balances.items()}
        self.fee_rate = fee_pct / 100
        self.orders: dict[str, dict[str, Any]] = {}
        self.trades: list[dict[str, Any]] = []
        self._seq = 0
        self.posts = 0

    def _now(self) -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    # ------------------------------------------------------------------ test controls
    def fill(self, order_id: str, price: Decimal, qty: Decimal) -> None:
        o = self.orders[order_id]
        sym = to_canonical(o[F["ord_symbol"]])
        base, quote = sym.split("/")
        notional = price * qty
        fee = notional * self.fee_rate
        if o[F["ord_side"]] == "buy":
            self.balances[quote] = self.balances.get(quote, Decimal(0)) - notional - fee
            self.balances[base] = self.balances.get(base, Decimal(0)) + qty
        else:
            self.balances[base] = self.balances.get(base, Decimal(0)) - qty
            self.balances[quote] = self.balances.get(quote, Decimal(0)) + notional - fee
        filled = Decimal(o[F["ord_filled"]]) + qty
        o[F["ord_filled"]] = str(filled)
        o[F["ord_avg_price"]] = str(price)
        o[F["ord_status"]] = "FILLED" if filled >= Decimal(o[F["ord_qty"]]) else "PARTIALLY_FILLED"
        self.trades.append(
            {
                F["fill_id"]: f"t{len(self.trades) + 1}",
                F["fill_order_id"]: order_id,
                F["fill_client_id"]: o[F["ord_client_id"]],
                F["fill_symbol"]: o[F["ord_symbol"]],
                F["fill_side"]: o[F["ord_side"]],
                F["fill_price"]: str(price),
                F["fill_qty"]: str(qty),
                F["fill_fee"]: str(fee),
                F["fill_fee_asset"]: quote,
                F["fill_ts"]: self._now(),
                F["fill_is_maker"]: True,
            }
        )

    # ------------------------------------------------------------------ http
    def _ok(self, result: Any) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "message": "ok", "result": result})

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == ep.Paths.ORDERS and method == "POST":
            self.posts += 1
            body = json.loads(request.content)
            self._seq += 1
            oid = str(1000 + self._seq)
            self.orders[oid] = {
                F["ord_id"]: oid,
                F["ord_client_id"]: body.get(F["req_client_id"], ""),
                F["ord_symbol"]: body[F["req_symbol"]],
                F["ord_side"]: body[F["req_side"]],
                F["ord_type"]: body[F["req_type"]],
                F["ord_price"]: body.get(F["req_price"]),
                F["ord_qty"]: body[F["req_qty"]],
                F["ord_filled"]: "0",
                F["ord_status"]: "NEW",
                F["ord_created"]: self._now(),
            }
            return self._ok(self.orders[oid])
        if path == ep.Paths.ORDERS and method == "DELETE":
            body = json.loads(request.content)
            oid = body.get(F["ord_id"])
            if oid is None:
                cid = body.get(F["ord_client_id"])
                oid = next((k for k, v in self.orders.items() if v[F["ord_client_id"]] == cid), None)
            if oid not in self.orders:
                return httpx.Response(404, json={"success": False, "message": "order not found"})
            if self.orders[oid][F["ord_status"]] in ("NEW", "PARTIALLY_FILLED"):
                self.orders[oid][F["ord_status"]] = "CANCELED"
            return self._ok(self.orders[oid])
        if path.startswith(ep.Paths.ORDERS + "/") and method == "GET":
            oid = path.rsplit("/", 1)[-1]
            if oid not in self.orders:
                return httpx.Response(404, json={"success": False, "message": "order not found"})
            return self._ok(self.orders[oid])
        if path == ep.Paths.OPEN_ORDERS:
            sym = request.url.params.get("symbol")
            rows = [
                o
                for o in self.orders.values()
                if o[F["ord_status"]] in ("NEW", "PARTIALLY_FILLED")
                and (sym is None or o[F["ord_symbol"]] == sym)
            ]
            return self._ok({F["open_orders_root"]: rows})
        if path == ep.Paths.BALANCES:
            return self._ok(
                {
                    F["balances_root"]: {
                        a: {F["bal_asset"]: a, F["bal_total"]: str(v), F["bal_locked"]: "0"}
                        for a, v in self.balances.items()
                    }
                }
            )
        if path == ep.Paths.MY_TRADES:
            return self._ok({F["my_trades_root"]: list(self.trades)})
        return httpx.Response(404, json={"success": False, "message": f"unknown route {method} {path}"})
