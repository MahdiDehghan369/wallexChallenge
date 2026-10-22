"""Idempotent order submission (T-802).

``place_order`` is never retried blindly. If submission fails with an *unknown outcome* (network
error / timeout), the order is looked up by its ``client_order_id`` up to ``lookup_attempts`` times
(``lookup_interval_s`` apart) via ``get_order``, open orders and recent fills. Only if it is not found
anywhere is it re-sent with a new client id (``attempt + 1``) — so there are never two live orders
for the same leg.
"""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from siavash.core.ids import client_order_id
from siavash.core.interfaces import Broker, Clock
from siavash.core.models import Order, OrderRequest
from siavash.core.types import OrderStatus
from siavash.exchange.errors import NetworkError
from siavash.logging import get_logger

log = get_logger(__name__)

_COID_RE = re.compile(r"^SIAV-(?P<did>.+)-(?P<leg>[EXTGOS])(?P<n>\d+)$")
UNKNOWN_OUTCOME = (NetworkError, asyncio.TimeoutError, TimeoutError)


def next_attempt(coid: str) -> str:
    """``SIAV-D-E1`` -> ``SIAV-D-E2``."""
    m = _COID_RE.match(coid)
    if not m:
        raise ValueError(f"not a bot client order id: {coid}")
    return client_order_id(m["did"], m["leg"], int(m["n"]) + 1)


class SafeSubmitter:
    """Callable ``(broker, req) -> Order`` usable as ``OrderRouter(submit=...)``."""

    def __init__(
        self, clock: Clock, lookup_attempts: int = 3, lookup_interval_s: float = 2.0, max_resubmits: int = 1
    ) -> None:
        self.clock = clock
        self.lookup_attempts = lookup_attempts
        self.lookup_interval_s = lookup_interval_s
        self.max_resubmits = max_resubmits

    @classmethod
    def from_config(cls, cfg: Any, clock: Clock) -> SafeSubmitter:
        ex = cfg.base.execution
        return cls(clock, ex.submit_lookup_attempts, ex.submit_lookup_interval_s)

    async def _find(self, broker: Broker, req: OrderRequest, since_s: float = 600) -> Order | None:
        try:
            o = await broker.get_order(req.symbol, req.client_order_id)
            if o is not None:
                return o
            for o in await broker.get_open_orders(req.symbol):
                if o.client_order_id == req.client_order_id:
                    return o
            fills = [
                f
                for f in await broker.get_fills(self.clock.now() - timedelta(seconds=since_s))
                if f.client_order_id == req.client_order_id
            ]
        except Exception as e:  # lookup itself failed: treat as "not found yet"
            log.warning("order_lookup_failed", client_order_id=req.client_order_id, error=str(e))
            return None
        if not fills:
            return None
        qty = sum((f.qty for f in fills), Decimal(0))
        avg = sum((f.qty * f.price for f in fills), Decimal(0)) / qty
        return Order(
            order_id=fills[0].order_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=req.side,
            type=req.type,
            price=req.price,
            qty=req.qty,
            filled_qty=qty,
            avg_fill_price=avg,
            status=OrderStatus.FILLED if qty >= req.qty else OrderStatus.PARTIALLY_FILLED,
            created_at=fills[0].ts,
            updated_at=fills[-1].ts,
        )

    async def __call__(self, broker: Broker, req: OrderRequest) -> Order:
        current = req
        for resubmit in range(self.max_resubmits + 1):
            try:
                return await broker.place_order(current)
            except UNKNOWN_OUTCOME as e:
                log.warning("submit_outcome_unknown", client_order_id=current.client_order_id, error=str(e))
            for _ in range(self.lookup_attempts):
                await self.clock.sleep(self.lookup_interval_s)
                found = await self._find(broker, current)
                if found is not None:
                    log.info(
                        "submit_recovered", client_order_id=current.client_order_id, status=found.status.value
                    )
                    return found
            if resubmit < self.max_resubmits:
                current = current.model_copy(
                    update={"client_order_id": next_attempt(current.client_order_id)}
                )
                log.warning("submit_resend", client_order_id=current.client_order_id)
        raise NetworkError(f"order {req.client_order_id}: outcome unknown after lookups and resubmits")


async def safe_place(
    broker: Broker, req: OrderRequest, clock: Clock, lookup_attempts: int = 3, lookup_interval_s: float = 2.0
) -> Order:
    """Functional form of :class:`SafeSubmitter`."""
    return await SafeSubmitter(clock, lookup_attempts, lookup_interval_s)(broker, req)
