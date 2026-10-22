"""Deterministic clock for tests and simulations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from siavash.core.interfaces import Clock


class FakeClock(Clock):
    """``sleep`` only moves virtual time forward (yielding once to the event loop)."""

    def __init__(self, start: datetime | None = None) -> None:
        start = start or datetime(2026, 1, 1, tzinfo=UTC)
        if start.tzinfo is None:
            raise ValueError("FakeClock start must be timezone-aware")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)

    def advance(self, seconds: float) -> None:
        """Move virtual time forward by ``seconds`` (must be >= 0)."""
        if seconds < 0:
            raise ValueError("cannot move time backwards")
        self._now += timedelta(seconds=seconds)

    def set(self, when: datetime) -> None:
        """Jump to ``when`` (must not be in the past)."""
        if when < self._now:
            raise ValueError("cannot move time backwards")
        self._now = when
