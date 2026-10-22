"""Supervisor heartbeat watchdog (T-1102).

The orchestrator writes ``state/heartbeat`` (an ISO timestamp) every ``loop.heartbeat_s``. A daemon
thread checks it; if the heartbeat is older than ``loop.watchdog_timeout_s`` (e.g. the event loop is
stuck) it logs a critical event and hard-exits the process (``os._exit(1)``) so Docker
(``restart: unless-stopped``) restarts the bot. The container healthcheck uses the same file
(``find /app/state/heartbeat -mmin -3``).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from siavash.logging import get_logger

log = get_logger(__name__)


def write_heartbeat(path: str | Path, ts_iso: str) -> None:
    """Atomically replace the heartbeat file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(ts_iso, encoding="utf-8")
    os.replace(tmp, p)


class Watchdog:
    def __init__(
        self,
        heartbeat_path: str | Path,
        timeout_s: float = 180.0,
        check_every_s: float = 15.0,
        exit_fn: Callable[[int], None] = os._exit,
        time_fn: Callable[[], float] = time.time,
        startup_grace_s: float | None = None,
    ) -> None:
        self.path = Path(heartbeat_path)
        self.timeout_s = timeout_s
        self.check_every_s = check_every_s
        self.exit_fn = exit_fn
        self.time_fn = time_fn
        self.started_at = time_fn()
        self.grace = timeout_s if startup_grace_s is None else startup_grace_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def age_s(self) -> float | None:
        """Seconds since the heartbeat file was modified (None if it does not exist)."""
        try:
            return self.time_fn() - self.path.stat().st_mtime
        except FileNotFoundError:
            return None

    def check_once(self) -> bool:
        """True if healthy; otherwise logs critical and calls ``exit_fn(1)``."""
        age = self.age_s()
        if age is None:
            if self.time_fn() - self.started_at <= self.grace:
                return True
            reason = "heartbeat file missing"
        elif age <= self.timeout_s:
            return True
        else:
            reason = f"heartbeat stale for {age:.0f}s (> {self.timeout_s:.0f}s)"
        log.critical("watchdog_restart", reason=reason, path=str(self.path))
        self.exit_fn(1)
        return False

    def _run(self) -> None:
        while not self._stop.wait(self.check_every_s):
            self.check_once()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="siavash-watchdog", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
