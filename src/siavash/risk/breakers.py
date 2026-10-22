"""Circuit breakers (T-703, section 5).

- daily PnL ≤ −``daily_loss_limit_pct`` → NO_NEW_ENTRIES until the next trading day (Tehran);
- drawdown from peak ≥ ``kill_switch_drawdown_pct`` → FLATTEN_AND_HALT, persistent across restarts,
  cleared only by an explicit ``resume(confirm=True)`` (operator command, not a manual trade);
- ≥ ``api_errors_threshold`` API errors within ``api_errors_window_min`` → NO_NEW_ENTRIES until
  ``api_errors_clear_min`` minutes pass without errors;
- stale market data → NO_NEW_ENTRIES while stale.
The most severe active condition wins. Every state change writes a BREAKER decision record and alerts.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from siavash.core.interfaces import Clock, DecisionLogger
from siavash.core.models import PortfolioSnapshot
from siavash.core.types import BreakerState, DecisionType
from siavash.exchange.rate_limit import ErrorCounter
from siavash.logging import get_logger
from siavash.reporting.equity import trading_day_start

log = get_logger(__name__)
SEVERITY = {BreakerState.NORMAL: 0, BreakerState.NO_NEW_ENTRIES: 1, BreakerState.FLATTEN_AND_HALT: 2}
AlertFn = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class BreakerController:
    def __init__(
        self,
        store: Any,
        cfg: Any,
        clock: Clock,
        alert: AlertFn | None = None,
        decision_logger: DecisionLogger | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg
        self.clock = clock
        self.alert = alert
        self.decision_logger = decision_logger
        self.state = BreakerState.NORMAL
        self.reason = ""
        self.since = clock.now()
        self.halted = False
        self.daily_until: datetime | None = None
        self.api_active = False

    async def load(self) -> None:
        """Restore persisted state (a kill-switch halt survives restarts)."""
        saved = await self.store.get_breaker()
        if saved is not None:
            self.state, self.reason, self.since = saved
        self.halted = (
            bool(await self.store.kv_get("breaker_halted", False))
            or self.state is BreakerState.FLATTEN_AND_HALT
        )
        du = await self.store.kv_get("breaker_daily_until")
        self.daily_until = datetime.fromisoformat(du) if du else None

    async def _emit(self, new: BreakerState, reason: str) -> None:
        if new is self.state and reason == self.reason:
            return
        old = self.state
        self.state, self.reason, self.since = new, reason, self.clock.now()
        await self.store.set_breaker(new, reason, self.since)
        log.warning("breaker_change", old=old.value, new=new.value, reason=reason)
        if self.decision_logger is not None:
            from siavash.engine.decision_log import build_record

            self.decision_logger.write(
                build_record(
                    DecisionType.BREAKER,
                    clock=self.clock,
                    final_reason=f"breaker {old.value} -> {new.value}: {reason}",
                    extra={"old": old.value, "new": new.value, "reason": reason},
                )
            )
        if self.alert is not None:
            res = self.alert("breaker", {"old": old.value, "new": new.value, "reason": reason})
            if inspect.isawaitable(res):
                await res

    async def evaluate(
        self, snap: PortfolioSnapshot, errors: ErrorCounter | None = None, stale: bool = False
    ) -> BreakerState:
        """Update the breaker from the latest portfolio snapshot, API error counter and data staleness."""
        r = self.cfg.risk
        b = r.breakers
        now = self.clock.now()
        reasons: list[tuple[BreakerState, str]] = []

        if snap.drawdown_pct >= r.kill_switch_drawdown_pct and not self.halted:
            self.halted = True
            await self.store.kv_set("breaker_halted", True)
        if self.halted:
            reasons.append(
                (
                    BreakerState.FLATTEN_AND_HALT,
                    f"kill-switch: drawdown {snap.drawdown_pct:.2f}% >= {r.kill_switch_drawdown_pct}% (resume --confirm required)",
                )
            )

        if snap.daily_pnl_pct <= -r.daily_loss_limit_pct:
            until = trading_day_start(now, self.cfg.base.timezone) + timedelta(days=1)
            if self.daily_until != until:
                self.daily_until = until
                await self.store.kv_set("breaker_daily_until", until.isoformat())
        if self.daily_until is not None and now < self.daily_until:
            reasons.append(
                (
                    BreakerState.NO_NEW_ENTRIES,
                    f"daily loss limit {r.daily_loss_limit_pct}% hit; until {self.daily_until.isoformat()}",
                )
            )

        if errors is not None:
            ts = now.timestamp()
            n = errors.count(b.api_errors_window_min * 60, now=ts)
            if n >= b.api_errors_threshold:
                self.api_active = True
            elif self.api_active:
                last = errors.last_error_ts()
                if last is None or ts - last >= b.api_errors_clear_min * 60:
                    self.api_active = False
            if self.api_active:
                reasons.append(
                    (BreakerState.NO_NEW_ENTRIES, f"API errors: {n} in {b.api_errors_window_min} min")
                )

        if stale:
            reasons.append((BreakerState.NO_NEW_ENTRIES, f"stale market data (> {b.stale_data_seconds}s)"))

        if reasons:
            new, reason = max(reasons, key=lambda x: SEVERITY[x[0]])
        else:
            new, reason = BreakerState.NORMAL, ""
        if new is not self.state or (
            new is not BreakerState.NORMAL and reason.split(":")[0] != self.reason.split(":")[0]
        ):
            await self._emit(new, reason)
        return self.state

    async def resume(self, confirm: bool = False) -> BreakerState:
        """Operator command: clear the kill-switch halt (requires ``confirm``)."""
        if not confirm:
            raise PermissionError("resume requires confirm=True")
        self.halted = False
        await self.store.kv_set("breaker_halted", False)
        if self.state is BreakerState.FLATTEN_AND_HALT:
            await self._emit(BreakerState.NORMAL, "operator resume")
        return self.state

    @property
    def allows_entries(self) -> bool:
        return self.state is BreakerState.NORMAL
