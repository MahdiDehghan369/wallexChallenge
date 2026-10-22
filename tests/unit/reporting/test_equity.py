"""T-1202: EquityTracker."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.config import load_config
from siavash.core.types import BreakerState
from siavash.engine.store import InMemoryStore
from siavash.reporting.equity import EquityTracker, trading_day_start
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock

D = Decimal
CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)


def _tracker(
    balances: dict[str, str], bids: dict[str, str], clock: FakeClock, tmp_path: Path
) -> tuple[EquityTracker, FakeBroker, dict[str, Decimal]]:
    broker = FakeBroker({k: D(v) for k, v in balances.items()}, clock=clock)
    prices = {k: D(v) for k, v in bids.items()}

    async def bid(sym: str) -> Decimal | None:
        return prices.get(sym)

    return EquityTracker(broker, bid, InMemoryStore(), clock, CFG, tmp_path / "equity.csv"), broker, prices


async def test_equity_peak_drawdown_daily(tmp_path: Path) -> None:
    # 2026-01-01 20:00 UTC = 23:30 Tehran
    clock = FakeClock(datetime(2026, 1, 1, 20, 0, tzinfo=UTC))
    tr, _, prices = _tracker(
        {"TMN": "4000000", "BTC": "0.0001", "USDT": "5", "XYZ": "3"},
        {"BTC/TMN": "7000000000", "USDT/TMN": "100000"},
        clock,
        tmp_path,
    )
    p = await tr.update()
    assert p.equity_tmn == D("4000000") + D("700000") + D("500000")
    assert p.exposure_tmn == D("1200000") and p.unpriced == ["XYZ"] and p.drawdown_pct == 0
    prices["BTC/TMN"] = D("6000000000")  # -100k
    clock.advance(300)
    p = await tr.update()
    assert p.peak_equity_tmn == D("5200000") and p.drawdown_pct == pytest.approx(100000 / 5200000 * 100)
    assert p.daily_pnl_pct == pytest.approx((5100000 / 5200000 - 1) * 100)
    # cross Tehran midnight (20:30 UTC) -> new trading day baseline
    clock.advance(1800)
    p = await tr.update()
    assert p.daily_pnl_pct == 0.0 and p.drawdown_pct > 0
    rows = list(csv.DictReader((tmp_path / "equity.csv").open()))
    assert len(rows) == 3 and D(rows[0]["equity_tmn"]) == D("5200000")
    assert len(await tr.store.equity_series()) == 3
    snap = tr.snapshot([], 1, 2, BreakerState.NORMAL)
    assert snap.equity_tmn == p.equity_tmn and snap.trades_today == 2


async def test_usdt_route_for_coin_without_tmn_market(tmp_path: Path) -> None:
    clock = FakeClock()
    tr, _, _ = _tracker({"TMN": "0", "ABC": "10"}, {"ABC/USDT": "2", "USDT/TMN": "100000"}, clock, tmp_path)
    p = await tr.update()
    assert p.equity_tmn == D("2000000") and p.unpriced == []


def test_trading_day_start() -> None:
    now = datetime(2026, 1, 1, 20, 31, tzinfo=UTC)  # 00:01 Tehran on Jan 2
    assert trading_day_start(now, "Asia/Tehran") == datetime(2026, 1, 1, 20, 30, tzinfo=UTC)
    assert trading_day_start(now - timedelta(minutes=2), "Asia/Tehran") == datetime(
        2025, 12, 31, 20, 30, tzinfo=UTC
    )


async def test_snapshot_requires_update(tmp_path: Path) -> None:
    tr, _, _ = _tracker({"TMN": "1"}, {}, FakeClock(), tmp_path)
    with pytest.raises(RuntimeError):
        tr.snapshot([], 0, 0, BreakerState.NORMAL)
