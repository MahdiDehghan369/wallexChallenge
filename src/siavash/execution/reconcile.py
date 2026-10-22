"""Reconciliation between exchange state and bot state (T-804).

Runs at startup and every ``reconcile_interval_min``:
1. open orders on the exchange: ours (``SIAV-`` prefix or known) but missing from state → adopted;
   **not ours → critical ``ORDER_NOT_FROM_BOT`` alert and left untouched** (sign of manual trading);
2. orders open in state but terminal on the exchange → state updated (fills are ingested by the tracker);
3. coin balances vs Σ position quantities: a position holding more than the balance is reduced; extra
   balance is recorded as an *orphan balance* (sold by the end-game liquidator).
Every correction writes a decision record (``outcome_action=INFO``, ``extra.kind="reconcile"``).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from siavash.core.ids import is_ours
from siavash.core.interfaces import Broker, Clock, DecisionLogger
from siavash.core.models import Order
from siavash.core.symbols import split
from siavash.core.types import DecisionType, PositionStatus
from siavash.exchange.markets import MarketRegistry, UnknownMarket
from siavash.logging import get_logger

log = get_logger(__name__)
ZERO = Decimal(0)
AlertFn = Callable[[str, dict[str, Any]], Awaitable[None] | None]


@dataclass
class ReconcileReport:
    adopted: list[str] = field(default_factory=list)
    foreign: list[str] = field(default_factory=list)
    updated: list[Order] = field(default_factory=list)
    reduced: dict[str, Decimal] = field(default_factory=dict)  # position_id -> new qty
    orphans: dict[str, Decimal] = field(default_factory=dict)  # asset -> extra qty

    @property
    def clean(self) -> bool:
        return not (self.adopted or self.foreign or self.updated or self.reduced or self.orphans)


async def reconcile(
    broker: Broker,
    store: Any,
    positions: Any,
    registry: MarketRegistry,
    clock: Clock,
    decision_logger: DecisionLogger | None = None,
    alert: AlertFn | None = None,
    tolerance_pct: float = 0.5,
) -> ReconcileReport:
    """Compare exchange and bot state and fix what can safely be fixed."""
    from siavash.engine.decision_log import build_record

    rep = ReconcileReport()

    def record(reason: str, **extra: Any) -> None:
        if decision_logger is not None:
            decision_logger.write(
                build_record(
                    DecisionType.OPERATOR_COMMAND,
                    clock=clock,
                    final_reason=f"reconcile: {reason}",
                    extra={"kind": "reconcile", **extra},
                )
            )

    async def notify(kind: str, data: dict[str, Any]) -> None:
        if alert is not None:
            res = alert(kind, data)
            if inspect.isawaitable(res):
                await res

    known = {o.client_order_id: o for o, _ in await store.load_orders()}

    # 1) exchange open orders
    for o in await broker.get_open_orders():
        if o.client_order_id in known:
            continue
        if is_ours(o.client_order_id):
            await store.save_order(o, {"adopted": True})
            known[o.client_order_id] = o
            rep.adopted.append(o.client_order_id)
            record(
                f"adopted unknown bot order {o.client_order_id}",
                client_order_id=o.client_order_id,
                symbol=o.symbol,
            )
        else:
            ident = o.client_order_id or f"order_id={o.order_id}"
            rep.foreign.append(ident)
            log.critical("ORDER_NOT_FROM_BOT", symbol=o.symbol, side=o.side.value, order=ident)
            await notify("ORDER_NOT_FROM_BOT", {"symbol": o.symbol, "order": ident, "side": o.side.value})
            record(f"ORDER_NOT_FROM_BOT {ident} on {o.symbol} (left untouched)", symbol=o.symbol, order=ident)

    # 2) state says open, exchange says terminal
    for coid, o in known.items():
        if o.status.is_terminal:
            continue
        latest = await broker.get_order(o.symbol, coid)
        if latest is not None and latest.status.is_terminal:
            await store.save_order(latest, {"reconciled": True})
            rep.updated.append(latest)
            record(
                f"order {coid} is {latest.status.value} on the exchange",
                client_order_id=coid,
                status=latest.status.value,
            )

    # 3) balances vs positions
    balances = await broker.get_balances()
    held: dict[str, Decimal] = {}
    for p in positions.open_positions():
        if p.status in (PositionStatus.OPEN, PositionStatus.PENDING_EXIT):
            base, _ = split(p.symbol)
            held[base] = held.get(base, ZERO) + p.qty
    assets = (set(balances) | set(held)) - {"TMN"}
    orphans: dict[str, str] = {}
    for asset in sorted(assets):
        bal = balances[asset].total if asset in balances else ZERO
        pos_qty = held.get(asset, ZERO)
        try:
            min_qty = registry.get(f"{asset}/TMN").min_qty
        except UnknownMarket:
            min_qty = ZERO
        tol = max(min_qty, max(bal, pos_qty) * Decimal(str(tolerance_pct)) / 100)
        diff = bal - pos_qty
        if abs(diff) <= tol:
            continue
        if diff < 0:
            remaining = bal
            for p in positions.open_positions():
                if split(p.symbol)[0] != asset or p.qty <= 0:
                    continue
                new_qty = min(p.qty, remaining)
                remaining -= new_qty
                if new_qty != p.qty:
                    log.warning(
                        "reconcile_reduce_position",
                        position_id=p.position_id,
                        old=str(p.qty),
                        new=str(new_qty),
                    )
                    p.qty = new_qty
                    await positions.update(p)
                    rep.reduced[p.position_id] = new_qty
                    record(
                        f"position {p.position_id} qty reduced to balance {new_qty} {asset}",
                        position_id=p.position_id,
                    )
        else:
            rep.orphans[asset] = diff
            orphans[asset] = str(diff)
            record(f"orphan balance {diff} {asset} (sold at end-game)", asset=asset, qty=str(diff))
    await store.kv_set("orphan_balances", orphans)
    if not rep.clean:
        log.info(
            "reconcile_done",
            adopted=len(rep.adopted),
            foreign=len(rep.foreign),
            updated=len(rep.updated),
            reduced=len(rep.reduced),
            orphans=list(rep.orphans),
        )
    return rep
