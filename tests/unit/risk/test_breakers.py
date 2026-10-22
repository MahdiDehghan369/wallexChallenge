"""T-703: circuit breakers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from siavash.config import load_config
from siavash.core.models import PortfolioSnapshot
from siavash.core.types import BreakerState, DecisionType
from siavash.engine.decision_log import MemoryDecisionLogger
from siavash.engine.store import SqliteStore
from siavash.exchange.rate_limit import ErrorCounter
from siavash.risk.breakers import BreakerController
from tests.fakes.clock import FakeClock

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)  # 13:30 Tehran
OPEN_STORES: list[SqliteStore] = []


@pytest.fixture(autouse=True)
async def _close_stores() -> Any:
    yield
    for s in OPEN_STORES:
        await s.close()
    OPEN_STORES.clear()


def snap(dd: float = 0.0, daily: float = 0.0) -> PortfolioSnapshot:
    e = Decimal("5000000")
    return PortfolioSnapshot(
        ts=T0,
        equity_tmn=e,
        cash_tmn=e,
        peak_equity_tmn=e,
        exposure_tmn=Decimal(0),
        open_positions=[],
        trades_last_hour=0,
        trades_today=0,
        daily_pnl_pct=daily,
        drawdown_pct=dd,
        breaker_state=BreakerState.NORMAL,
    )


async def _ctl(
    tmp_path: Path, clock: FakeClock
) -> tuple[BreakerController, list[dict[str, Any]], MemoryDecisionLogger]:
    alerts: list[dict[str, Any]] = []
    logger = MemoryDecisionLogger()
    store = SqliteStore(tmp_path / "state.sqlite")
    OPEN_STORES.append(store)
    ctl = BreakerController(store, CFG, clock, alert=lambda k, d: alerts.append(d), decision_logger=logger)
    await ctl.load()
    return ctl, alerts, logger


async def test_daily_loss_until_next_day(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    ctl, alerts, logger = await _ctl(tmp_path, clock)
    assert await ctl.evaluate(snap(daily=-1.0)) is BreakerState.NORMAL
    assert await ctl.evaluate(snap(daily=-2.1)) is BreakerState.NO_NEW_ENTRIES
    clock.advance(3600)
    assert await ctl.evaluate(snap(daily=-0.5)) is BreakerState.NO_NEW_ENTRIES  # recovery does not lift it
    clock.set(datetime(2026, 1, 1, 20, 31, tzinfo=UTC))  # 00:01 Tehran next day
    assert await ctl.evaluate(snap(daily=0.0)) is BreakerState.NORMAL
    assert [a["new"] for a in alerts] == ["NO_NEW_ENTRIES", "NORMAL"]
    assert all(r.type is DecisionType.BREAKER for r in logger.records) and len(logger.records) == 2


async def test_kill_switch_persists_after_restart(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    ctl, _, _ = await _ctl(tmp_path, clock)
    assert await ctl.evaluate(snap(dd=5.2)) is BreakerState.FLATTEN_AND_HALT
    assert await ctl.evaluate(snap(dd=0.0)) is BreakerState.FLATTEN_AND_HALT  # sticky
    await ctl.store.close()
    ctl2, _, _ = await _ctl(tmp_path, clock)
    assert ctl2.state is BreakerState.FLATTEN_AND_HALT
    assert await ctl2.evaluate(snap()) is BreakerState.FLATTEN_AND_HALT
    with pytest.raises(PermissionError):
        await ctl2.resume()
    assert await ctl2.resume(confirm=True) is BreakerState.NORMAL
    assert await ctl2.evaluate(snap()) is BreakerState.NORMAL


async def test_api_errors_clear_after_quiet_period(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    ctl, _, _ = await _ctl(tmp_path, clock)
    ec = ErrorCounter(time_fn=lambda: clock.now().timestamp())
    for _ in range(5):
        ec.record()
        clock.advance(10)
    assert await ctl.evaluate(snap(), ec) is BreakerState.NO_NEW_ENTRIES
    clock.advance(6 * 60)  # outside the 5-minute window, but not 15 minutes quiet yet
    assert await ctl.evaluate(snap(), ec) is BreakerState.NO_NEW_ENTRIES
    clock.advance(10 * 60)
    assert await ctl.evaluate(snap(), ec) is BreakerState.NORMAL


async def test_stale_and_severity(tmp_path: Path) -> None:
    clock = FakeClock(T0)
    ctl, _, _ = await _ctl(tmp_path, clock)
    assert await ctl.evaluate(snap(), stale=True) is BreakerState.NO_NEW_ENTRIES
    assert "stale" in ctl.reason
    assert await ctl.evaluate(snap(), stale=False) is BreakerState.NORMAL
    assert (
        await ctl.evaluate(snap(dd=6, daily=-3), stale=True) is BreakerState.FLATTEN_AND_HALT
    )  # strongest wins
    assert not ctl.allows_entries
