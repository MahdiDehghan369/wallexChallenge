"""T-603: PositionManager transitions, partial fills, PnL with fees."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from siavash.core.models import Candidate, Fill, Position, RiskDecision
from siavash.core.types import ExitReason, PositionStatus, Side, StrategyId
from siavash.engine.positions import InvalidTransition, PositionManager
from tests.fakes.clock import FakeClock

D = Decimal


class DictStore:
    def __init__(self) -> None:
        self.saved: dict[str, Position] = {}
        self.writes = 0

    async def save_position(self, p: Position) -> None:
        self.saved[p.position_id] = p.model_copy(deep=True)
        self.writes += 1

    async def load_positions(self) -> list[Position]:
        return [p.model_copy(deep=True) for p in self.saved.values()]


def _cand(clock: FakeClock, tp1: float | None = 104.0) -> Candidate:
    return Candidate(
        candidate_id="c1",
        ts=clock.now(),
        symbol="BTC/TMN",
        strategy=StrategyId.S1_TREND_BREAKOUT,
        entry_ref_price=100.0,
        stop_loss=98.5,
        take_profit=108.0,
        tp1_price=tp1,
        time_stop_minutes=720,
        expected_move_pct=4.0,
        conditions={},
    )


def _fill(clock: FakeClock, side: Side, price: str, qty: str, coid: str = "SIAV-D-E1") -> Fill:
    return Fill(
        fill_id=f"{coid}-{price}-{qty}",
        order_id="1",
        client_order_id=coid,
        symbol="BTC/TMN",
        side=side,
        price=D(price),
        qty=D(qty),
        fee=D("0"),
        fee_asset="TMN",
        ts=clock.now(),
    )


RISK = RiskDecision(approved=True, size_tmn=D("1000"), qty=D("10"), equity_tmn=D("5000000"))


async def _mgr() -> tuple[PositionManager, DictStore, FakeClock]:
    clock = FakeClock()
    store = DictStore()
    return PositionManager(store, clock), store, clock


async def test_full_lifecycle_with_partial_fills_and_pnl() -> None:
    pm, store, clock = await _mgr()
    p = await pm.create(_cand(clock), RISK, "D")
    assert p.status is PositionStatus.PENDING_ENTRY and p.stop_loss == D("98.5") and p.tp1_price == D("104.0")
    assert p.time_stop_at == clock.now() + timedelta(hours=12)
    assert pm.exposure_tmn({"BTC/TMN": D("100")}) == D("1000")  # planned size counts before the fill
    clock.advance(10)
    await pm.apply_entry_fill(p.position_id, _fill(clock, Side.BUY, "100", "4"), D("1"))
    p = await pm.apply_entry_fill(p.position_id, _fill(clock, Side.BUY, "101", "6"), D("1.5"))
    assert p.status is PositionStatus.OPEN and p.qty == D("10") and p.avg_entry == D("100.6")
    assert p.opened_at is not None and p.realized_pnl_tmn == D("-2.5")
    await pm.entry_finished(p.position_id)
    assert pm.exposure_tmn({"BTC/TMN": D("110")}) == D("1100")
    # TP1: half out, back to OPEN with tp1_done
    await pm.start_exit(p.position_id, ExitReason.TP1_PARTIAL)
    p = await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "104", "5", "SIAV-X-T1"), D("1"))
    assert p.status is PositionStatus.PENDING_EXIT and p.tp1_done
    p = await pm.exit_finished(p.position_id)
    assert p.status is PositionStatus.OPEN
    # final exit in two partial fills
    await pm.start_exit(p.position_id, ExitReason.TAKE_PROFIT)
    await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "108", "2", "SIAV-Y-X1"), D("0.5"))
    p = await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "108", "3", "SIAV-Y-X1"), D("0.5"))
    assert p.status is PositionStatus.CLOSED and p.exit_reason is ExitReason.TAKE_PROFIT and p.qty == 0
    expected = (D("104") - D("100.6")) * 5 + (D("108") - D("100.6")) * 5 - D("2.5") - D("1") - D("1")
    assert p.realized_pnl_tmn == expected and p.fees_tmn == D("4.5")
    assert pm.open_positions() == [] and store.saved[p.position_id].status is PositionStatus.CLOSED


async def test_cancel_without_fill() -> None:
    pm, _, clock = await _mgr()
    p = await pm.create(_cand(clock), RISK, "D")
    p = await pm.entry_finished(p.position_id)
    assert p.status is PositionStatus.CANCELLED and pm.exposure_tmn({}) == 0


async def test_close_by_min_qty_dust() -> None:
    pm, _, clock = await _mgr()
    p = await pm.create(_cand(clock, None), RISK, "D")
    await pm.apply_entry_fill(p.position_id, _fill(clock, Side.BUY, "100", "1"), D("0"))
    await pm.start_exit(p.position_id, ExitReason.STOP_LOSS)
    p = await pm.apply_exit_fill(
        p.position_id, _fill(clock, Side.SELL, "98", "0.9999", "SIAV-Z-X1"), D("0"), min_qty=D("0.001")
    )
    assert p.status is PositionStatus.CLOSED and p.exit_reason is ExitReason.STOP_LOSS


async def test_illegal_transitions() -> None:
    pm, _, clock = await _mgr()
    p = await pm.create(_cand(clock), RISK, "D")
    with pytest.raises(InvalidTransition):
        await pm.start_exit(p.position_id, ExitReason.STOP_LOSS)  # not OPEN yet
    with pytest.raises(InvalidTransition):
        await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "1", "1"), D("0"))
    await pm.apply_entry_fill(p.position_id, _fill(clock, Side.BUY, "100", "1"), D("0"))
    with pytest.raises(InvalidTransition):
        await pm.apply_entry_fill(p.position_id, _fill(clock, Side.SELL, "100", "1"), D("0"))
    await pm.start_exit(p.position_id, ExitReason.STOP_LOSS)
    with pytest.raises(InvalidTransition):
        await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "100", "5"), D("0"))  # more than held
    await pm.apply_exit_fill(p.position_id, _fill(clock, Side.SELL, "100", "1"), D("0"))
    with pytest.raises(InvalidTransition):
        await pm.start_exit(p.position_id, ExitReason.STOP_LOSS)  # closed


async def test_reload_from_store() -> None:
    pm, store, clock = await _mgr()
    p = await pm.create(_cand(clock), RISK, "D")
    await pm.apply_entry_fill(p.position_id, _fill(clock, Side.BUY, "100", "1"), D("0"))
    pm2 = PositionManager(store, clock)
    await pm2.load()
    assert pm2.by_symbol("BTC/TMN") is not None and pm2.get(p.position_id).qty == D("1")
