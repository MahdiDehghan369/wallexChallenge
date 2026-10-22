"""T-1102: heartbeat watchdog."""

from __future__ import annotations

import os
import time
from pathlib import Path

from siavash.engine.watchdog import Watchdog, write_heartbeat


class FakeTime:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t


def test_fresh_heartbeat_is_healthy(tmp_path: Path) -> None:
    exits: list[int] = []
    ft = FakeTime()
    hb = tmp_path / "state" / "heartbeat"
    write_heartbeat(hb, "2026-01-01T00:00:00+00:00")
    wd = Watchdog(hb, timeout_s=180, exit_fn=exits.append, time_fn=ft)
    os.utime(hb, (ft.t, ft.t))
    assert wd.check_once() and exits == []
    ft.t += 179
    assert wd.check_once() and exits == []


def test_stale_heartbeat_exits(tmp_path: Path) -> None:
    exits: list[int] = []
    ft = FakeTime()
    hb = tmp_path / "heartbeat"
    write_heartbeat(hb, "x")
    os.utime(hb, (ft.t, ft.t))
    wd = Watchdog(hb, timeout_s=180, exit_fn=exits.append, time_fn=ft)
    ft.t += 181
    assert not wd.check_once() and exits == [1]


def test_missing_file_grace_then_exit(tmp_path: Path) -> None:
    exits: list[int] = []
    ft = FakeTime()
    wd = Watchdog(tmp_path / "nope", timeout_s=60, exit_fn=exits.append, time_fn=ft)
    assert wd.check_once() and exits == []  # startup grace
    ft.t += 61
    assert not wd.check_once() and exits == [1]


def test_thread_runs_and_stops(tmp_path: Path) -> None:
    exits: list[int] = []
    wd = Watchdog(
        tmp_path / "hb", timeout_s=0.0, check_every_s=0.01, exit_fn=exits.append, startup_grace_s=0.0
    )
    wd.start()
    time.sleep(0.1)
    wd.stop()
    assert exits and exits[0] == 1
