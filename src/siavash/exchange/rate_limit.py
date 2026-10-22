"""Token-bucket rate limiter and API error counter (T-102)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable


class TokenBucket:
    """Async token bucket: ``rate_per_sec`` tokens/s, at most ``burst`` stored."""

    def __init__(
        self,
        rate_per_sec: float,
        burst: int,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0 or burst < 1:
            raise ValueError("rate_per_sec must be > 0 and burst >= 1")
        self.rate = float(rate_per_sec)
        self.burst = float(burst)
        self._time = time_fn
        self._sleep = sleep_fn
        self._tokens = float(burst)
        self._last = time_fn()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._time()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Wait until ``tokens`` are available, then consume them."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await self._sleep((tokens - self._tokens) / self.rate)


class ErrorCounter:
    """Timestamps of API errors; feeds the technical circuit breaker (T-703)."""

    def __init__(self, time_fn: Callable[[], float] = time.time, max_keep: int = 10_000) -> None:
        self._time = time_fn
        self._events: deque[float] = deque(maxlen=max_keep)

    def record(self, ts: float | None = None) -> None:
        """Record one error at ``ts`` (epoch seconds; default now)."""
        self._events.append(self._time() if ts is None else ts)

    def count(self, window_s: float, now: float | None = None) -> int:
        """Errors within the last ``window_s`` seconds."""
        now = self._time() if now is None else now
        return sum(1 for t in self._events if now - window_s <= t <= now)

    def last_error_ts(self) -> float | None:
        """Timestamp of the most recent error, if any."""
        return self._events[-1] if self._events else None
