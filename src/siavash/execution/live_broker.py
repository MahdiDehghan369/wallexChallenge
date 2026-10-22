"""LiveBroker (T-806): thin ``Broker`` over ``WallexAccount`` with order logging.

Guarantees: every order carries a ``decision_id`` and passes ``SymbolGuard`` (inside
``WallexAccount.prepare``); every request/response/error is appended to ``logs/orders.jsonl``;
re-submitting a known ``client_order_id`` returns the existing order instead of placing a new one.
"""

from __future__ import annotations

from datetime import datetime

from siavash.core.interfaces import Broker
from siavash.core.models import Balance, Fill, Order, OrderRequest
from siavash.engine.decision_log import OrdersLogger
from siavash.exchange.account import WallexAccount
from siavash.exchange.errors import BadRequest, WallexError


class LiveBroker(Broker):
    def __init__(self, account: WallexAccount, orders_logger: OrdersLogger | None = None) -> None:
        self.account = account
        self.orders_log = orders_logger

    def _log(self, kind: str, payload: object, **meta: object) -> None:
        if self.orders_log is not None:
            self.orders_log.log(kind, payload, **meta)

    async def place_order(self, req: OrderRequest) -> Order:
        if not req.decision_id:
            raise BadRequest("order without decision_id")
        known = self.account.id_map.order_id_for(req.client_order_id)
        if known:
            existing = await self.account.get_order(req.symbol, req.client_order_id, order_id=known)
            if existing is not None:
                self._log("duplicate", existing, client_order_id=req.client_order_id)
                return existing
        self._log("request", req, decision_id=req.decision_id)
        try:
            order = await self.account.place_order(req)
        except WallexError as e:
            self._log(
                "error", {"error": type(e).__name__, "message": str(e)}, client_order_id=req.client_order_id
            )
            raise
        self._log("response", order, client_order_id=req.client_order_id)
        return order

    async def cancel_order(self, symbol: str, client_order_id: str) -> Order:
        self._log("cancel_request", {"symbol": symbol, "client_order_id": client_order_id})
        try:
            order = await self.account.cancel_order(symbol, client_order_id=client_order_id)
        except WallexError as e:
            self._log(
                "error", {"error": type(e).__name__, "message": str(e)}, client_order_id=client_order_id
            )
            raise
        self._log("cancel_response", order, client_order_id=client_order_id)
        return order

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        return await self.account.get_order(symbol, client_order_id)

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return await self.account.get_open_orders(symbol)

    async def get_balances(self) -> dict[str, Balance]:
        return await self.account.get_balances()

    async def get_fills(self, since: datetime) -> list[Fill]:
        return await self.account.get_my_trades(since)
