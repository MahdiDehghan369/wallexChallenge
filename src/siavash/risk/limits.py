"""Stateful risk limits (T-702): cooldowns after losses / exits and entry counters.

State is plain JSON (``to_dict`` / ``from_dict``) so the engine can persist it in the store's kv table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any


class CooldownTracker:
    """- after ``losses`` consecutive losing exits: no entries for ``minutes`` (section 5);
    - after any exit from a market: no re-entry into that market for ``cooldown_after_exit_minutes``."""

    def __init__(self, cfg: Any) -> None:
        r = cfg.risk
        self.loss_n = r.cooldown_after_consecutive_losses.losses
        self.loss_minutes = r.cooldown_after_consecutive_losses.minutes
        self.exit_minutes = r.cooldown_after_exit_minutes
        self.consecutive_losses = 0
        self.loss_cooldown_until: datetime | None = None
        self.market_exit: dict[str, datetime] = {}

    def record_exit(self, symbol: str, ts: datetime, pnl_tmn: Decimal) -> None:
        """Call when a position is fully closed."""
        self.market_exit[symbol] = ts
        if pnl_tmn < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.loss_n:
                self.loss_cooldown_until = ts + timedelta(minutes=self.loss_minutes)
                self.consecutive_losses = 0
        else:
            self.consecutive_losses = 0

    def losses_active(self, now: datetime) -> bool:
        return self.loss_cooldown_until is not None and now < self.loss_cooldown_until

    def market_active(self, symbol: str, now: datetime) -> bool:
        last = self.market_exit.get(symbol)
        return last is not None and now < last + timedelta(minutes=self.exit_minutes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "consecutive_losses": self.consecutive_losses,
            "loss_cooldown_until": self.loss_cooldown_until.isoformat() if self.loss_cooldown_until else None,
            "market_exit": {k: v.isoformat() for k, v in self.market_exit.items()},
        }

    def load_dict(self, d: dict[str, Any] | None) -> None:
        if not d:
            return
        self.consecutive_losses = int(d.get("consecutive_losses", 0))
        lc = d.get("loss_cooldown_until")
        self.loss_cooldown_until = datetime.fromisoformat(lc) if lc else None
        self.market_exit = {k: datetime.fromisoformat(v) for k, v in (d.get("market_exit") or {}).items()}


class EntryCounter:
    """Entries (new positions) in the last hour and in the current trading day."""

    def __init__(self) -> None:
        self.entries: list[datetime] = []

    def record(self, ts: datetime) -> None:
        self.entries.append(ts)

    def last_hour(self, now: datetime) -> int:
        return sum(1 for t in self.entries if now - timedelta(hours=1) < t <= now)

    def since(self, start: datetime) -> int:
        return sum(1 for t in self.entries if t >= start)

    def prune(self, now: datetime, keep: timedelta = timedelta(days=2)) -> None:
        self.entries = [t for t in self.entries if t >= now - keep]

    def to_list(self) -> list[str]:
        return [t.isoformat() for t in self.entries]

    def load_list(self, items: list[str] | None) -> None:
        self.entries = [datetime.fromisoformat(x) for x in items or []]
