"""Clocks (T-601).

- ``SystemClock``: real UTC time and ``asyncio.sleep`` (paper / live).
- ``SimClock``: discrete-event virtual time for backtests and replay. ``sleep`` registers a wake-up
  and blocks; :meth:`SimClock.run_until` lets every task run until it is idle, then jumps virtual time
  to the earliest pending wake-up. Many concurrent tasks can sleep without time running faster than
  intended, and runs are deterministic.
"""

from __future__ import annotations

import asyncio
import heapq
from datetime import UTC, datetime, timedelta

from siavash.core.interfaces import Clock


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class SimClock(Clock):
    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("SimClock start must be timezone-aware")
        self._now = start.astimezone(UTC)
        self._heap: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._seq = 0

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._heap, (self._now + timedelta(seconds=seconds), self._seq, fut))
        self._seq += 1
        await fut

    @staticmethod
    async def settle(max_spins: int = 100_000) -> None:
        """Yield until no other callback is ready (all tasks are blocked on the clock or done)."""
        loop = asyncio.get_running_loop()
        for _ in range(max_spins):
            await asyncio.sleep(0)
            ready = getattr(loop, "_ready", None)
            if ready is None:
                for _ in range(50):
                    await asyncio.sleep(0)
                return
            if not ready:
                return

    def pending(self) -> int:
        return sum(1 for _, _, f in self._heap if not f.done())

    async def run_until(self, end: datetime, stop: asyncio.Event | None = None) -> None:
        """Advance virtual time event by event until ``end`` (or ``stop`` is set / nothing is pending)."""
        while True:
            await self.settle()
            if stop is not None and stop.is_set():
                return
            while self._heap and self._heap[0][2].done():
                heapq.heappop(self._heap)
            if not self._heap:
                return
            t = self._heap[0][0]
            if t > end:
                self._now = max(self._now, end)
                return
            self._now = max(self._now, t)
            while self._heap and self._heap[0][0] <= self._now:
                _, _, fut = heapq.heappop(self._heap)
                if not fut.done():
                    fut.set_result(None)

    def advance_to(self, when: datetime) -> None:
        """Jump forward without waking sleepers (setup helper)."""
        if when < self._now:
            raise ValueError("cannot move time backwards")
        self._now = when
