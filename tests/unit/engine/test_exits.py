"""T-604: exit rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.config import AppConfig, load_config
from siavash.core.models import Position, RegimeAssessment
from siavash.core.types import BreakerState, ExitReason, PositionStatus, Regime, StrategyId
from siavash.engine.exits import after_tp1, evaluate_exit, update_trailing

D = Decimal
NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)


def pos(strategy: StrategyId = StrategyId.S1_TREND_BREAKOUT, **kw: object) -> Position:
    base: dict[str, object] = dict(
        position_id="P",
        symbol="BTC/TMN",
        strategy=strategy,
        status=PositionStatus.OPEN,
        entry_decision_id="D",
        qty=D("1"),
        avg_entry=D("100"),
        stop_loss=D("98"),
        take_profit=D("108"),
        tp1_price=D("104"),
        time_stop_at=NOW + timedelta(hours=6),
        opened_at=NOW - timedelta(hours=1),
    )
    base.update(kw)
    return Position(**base)  # type: ignore[arg-type]


def regime(r: Regime) -> RegimeAssessment:
    return RegimeAssessment(
        ts=NOW, regime=r, risk_multiplier=0, enabled_strategies=[], reasons=[], source="rule", rule_regime=r
    )


def ev(
    cfg: AppConfig,
    p: Position,
    bid: str,
    now: datetime = NOW,
    breaker: BreakerState = BreakerState.NORMAL,
    phase: str = "NONE",
    reg: Regime | None = None,
):  # type: ignore[no-untyped-def]
    return evaluate_exit(p, D(bid), now, breaker, phase, regime(reg) if reg else None, cfg)


def test_each_reason(cfg: AppConfig) -> None:
    assert ev(cfg, pos(), "101") is None
    assert ev(cfg, pos(), "101", phase="UNWIND").reason is ExitReason.ENDGAME
    assert ev(cfg, pos(), "101", breaker=BreakerState.FLATTEN_AND_HALT).reason is ExitReason.BREAKER
    assert ev(cfg, pos(), "101", breaker=BreakerState.NO_NEW_ENTRIES) is None  # no new entries ≠ exit
    assert ev(cfg, pos(), "97.9").reason is ExitReason.STOP_LOSS
    assert ev(cfg, pos(trailing_stop=D("102"), tp1_done=True), "101.5").reason is ExitReason.TRAILING_STOP
    tp1 = ev(cfg, pos(), "104.5")
    assert tp1.reason is ExitReason.TP1_PARTIAL and tp1.qty_fraction == D("0.5")
    assert ev(cfg, pos(tp1_done=True), "108").reason is ExitReason.TAKE_PROFIT
    assert (
        ev(cfg, pos(strategy=StrategyId.S3_IMPLIED_DEV, tp1_price=None), "108.1").reason
        is ExitReason.TAKE_PROFIT
    )
    assert ev(cfg, pos(strategy=StrategyId.S2_MEAN_REVERSION), "104.5") is None  # TP1 only for S1
    assert ev(cfg, pos(), "101", now=NOW + timedelta(hours=7)).reason is ExitReason.TIME_STOP
    assert ev(cfg, pos(), "100.2", reg=Regime.PANIC).reason is ExitReason.SIGNAL
    assert (
        ev(cfg, pos(), "101", reg=Regime.PANIC) is None
    )  # profit 1 % >= 0.3 %: keep (other rules still apply)


def test_priority(cfg: AppConfig) -> None:
    # SL and TP "simultaneously" (gap through both) -> SL wins
    weird = pos(stop_loss=D("105"), take_profit=D("103"))
    assert ev(cfg, weird, "104").reason is ExitReason.STOP_LOSS
    assert ev(cfg, pos(), "90", phase="FORCE").reason is ExitReason.ENDGAME
    assert ev(cfg, pos(), "97", now=NOW + timedelta(days=1)).reason is ExitReason.STOP_LOSS


def test_not_open_returns_none(cfg: AppConfig) -> None:
    assert ev(cfg, pos(status=PositionStatus.PENDING_EXIT), "50") is None
    assert ev(cfg, pos(status=PositionStatus.PENDING_ENTRY, qty=D("0")), "50") is None


def test_after_tp1_and_trailing(cfg: AppConfig) -> None:
    p = after_tp1(pos(), cfg)
    assert p.tp1_done and p.stop_loss == D("100") * D("1.007")
    p = update_trailing(p, D("106"), 1.0, cfg)
    assert p.highest_price == D("106") and p.trailing_stop == D("104.5")
    p = update_trailing(p, D("105"), 1.0, cfg)  # price falls: trail does not move down
    assert p.trailing_stop == D("104.5") and p.highest_price == D("106")
    p = update_trailing(p, D("107"), 1.0, cfg)
    assert p.trailing_stop == D("105.5")
    before = update_trailing(pos(), D("106"), 1.0, cfg)  # before TP1: no trailing
    assert before.trailing_stop is None and before.highest_price == D("106")
