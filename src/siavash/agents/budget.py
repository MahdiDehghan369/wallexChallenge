"""LLM budget guard (T-501).

The whole LLM budget is 3 USD, so every real model request goes through ``LLMBudget``:
- ``check(input_text)`` before a call: rejects inputs larger than ``max_input_tokens_per_call``
  (estimated as ``len(text) / 3``) and raises ``BudgetExceeded`` once a call/token cap is reached;
- ``record(tokens)`` after each request.

Counters (hourly / daily, boundaries in Asia/Tehran) are persisted in the store (``kv`` key
``llm_budget``) so a restart does not reset them. Cached answers never reach ``LLMClient`` and are
therefore never counted. ``BudgetExceeded`` is an ``LLMError``: reviewer / analyst fall back to their
deterministic rules (``status=FALLBACK``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from siavash.agents.llm import BudgetExceeded
from siavash.logging import get_logger

__all__ = ["BudgetExceeded", "LLMBudget", "estimate_tokens"]

log = get_logger(__name__)

STORE_KEY = "llm_budget"
CHARS_PER_TOKEN = 3


class KVStore(Protocol):
    async def kv_get(self, key: str, default: Any = None) -> Any: ...
    async def kv_set(self, key: str, value: Any) -> None: ...


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


class LLMBudget:
    def __init__(
        self,
        cfg: Any,
        store: KVStore | None = None,
        now_fn: Callable[[], datetime] | None = None,
        tz: str = "Asia/Tehran",
        on_exceeded: Callable[[str], Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.tz = ZoneInfo(tz)
        self.on_exceeded = on_exceeded
        self.state: dict[str, Any] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(cls, cfg: Any, store: KVStore | None = None, **kw: Any) -> LLMBudget:
        return cls(cfg.llm.budget, store, tz=cfg.base.timezone, **kw)

    async def _load(self) -> None:
        if self._loaded:
            return
        if self.store is not None:
            self.state = dict(await self.store.kv_get(STORE_KEY, {}) or {})
        self._loaded = True

    def _roll(self) -> None:
        local = self.now_fn().astimezone(self.tz)
        day, hour = local.strftime("%Y-%m-%d"), local.strftime("%Y-%m-%dT%H")
        if self.state.get("day") != day:
            self.state.update(day=day, day_calls=0, day_tokens=0)
        if self.state.get("hour") != hour:
            self.state.update(hour=hour, hour_calls=0)

    def _exceeded(self, reason: str) -> BudgetExceeded:
        log.warning("llm_budget_exceeded", reason=reason, **self.snapshot())
        if self.on_exceeded is not None:
            try:
                self.on_exceeded(reason)
            except Exception as e:  # alerting must never break the fallback path
                log.warning("llm_budget_alert_failed", error=str(e))
        return BudgetExceeded(reason)

    async def check(self, input_text: str | None = None) -> None:
        """Raise ``BudgetExceeded`` if this call must not be made."""
        c = self.cfg
        if input_text is not None and estimate_tokens(input_text) > c.max_input_tokens_per_call:
            raise self._exceeded(
                f"input ~{estimate_tokens(input_text)} tokens > max_input_tokens_per_call={c.max_input_tokens_per_call}"
            )
        async with self._lock:
            await self._load()
            self._roll()
            s = self.state
            if s["hour_calls"] >= c.max_calls_per_hour:
                raise self._exceeded(f"max_calls_per_hour={c.max_calls_per_hour}")
            if s["day_calls"] >= c.max_calls_per_day:
                raise self._exceeded(f"max_calls_per_day={c.max_calls_per_day}")
            if s["day_tokens"] >= c.max_tokens_per_day:
                raise self._exceeded(f"max_tokens_per_day={c.max_tokens_per_day}")

    async def record(self, tokens: int) -> None:
        """Count one model request and its tokens (prompt + completion)."""
        async with self._lock:
            await self._load()
            self._roll()
            self.state["hour_calls"] += 1
            self.state["day_calls"] += 1
            self.state["day_tokens"] += max(int(tokens), 0)
            if self.store is not None:
                await self.store.kv_set(STORE_KEY, self.state)

    def snapshot(self) -> dict[str, Any]:
        return {k: self.state.get(k) for k in ("day", "day_calls", "day_tokens", "hour", "hour_calls")}
