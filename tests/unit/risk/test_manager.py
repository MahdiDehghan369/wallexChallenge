"""T-702: one test per RISK_* code + an approved path."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from siavash.config import load_config
from siavash.core.models import (
    Candidate,
    MLScore,
    OrderBook,
    OrderBookLevel,
    PortfolioSnapshot,
    Position,
    RegimeAssessment,
    TradeReview,
)
from siavash.core.types import BreakerState, ComponentStatus, PositionStatus, Regime, Side, StrategyId
from siavash.exchange import endpoints as ep
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.risk import codes
from siavash.risk.limits import CooldownTracker, EntryCounter
from siavash.risk.manager import DefaultRiskManager
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import DENYLIST, load

D = Decimal
ROOT = Path(__file__).resolve().parents[3]
CFG = load_config(ROOT / "config", env_file=None)
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
MKT = {m.symbol: m for m in REG.all()}
ML = MLScore(status=ComponentStatus.SKIPPED)
PRICE = 7350000000.0


def cand(symbol: str = "BTC/TMN", stop: float = PRICE * 0.985) -> Candidate:
    return Candidate(
        candidate_id="c",
        ts=FakeClock().now(),
        symbol=symbol,
        strategy=StrategyId.S3_IMPLIED_DEV,
        entry_ref_price=PRICE,
        stop_loss=stop,
        take_profit=PRICE * 1.02,
        time_stop_minutes=180,
        expected_move_pct=2.0,
        conditions={},
    )


def book(spread_pct: float = 0.05, symbol: str = "BTC/TMN") -> OrderBook:
    half = PRICE * spread_pct / 200
    return OrderBook(
        symbol=symbol,
        ts=FakeClock().now(),
        bids=[OrderBookLevel(price=D(str(round(PRICE - half))), qty=D("1"))],
        asks=[OrderBookLevel(price=D(str(round(PRICE + half))), qty=D("1"))],
    )


def regime(r: Regime = Regime.RANGE, mult: float = 0.7) -> RegimeAssessment:
    return RegimeAssessment(
        ts=FakeClock().now(),
        regime=r,
        risk_multiplier=mult,
        enabled_strategies=list(StrategyId),
        reasons=[],
        source="rule",
        rule_regime=r,
    )


def open_pos(symbol: str) -> Position:
    return Position(
        position_id=symbol,
        symbol=symbol,
        strategy=StrategyId.S1_TREND_BREAKOUT,
        status=PositionStatus.OPEN,
        entry_decision_id="x",
        qty=D("1"),
        avg_entry=D("1"),
        stop_loss=D("0.9"),
        take_profit=D("2"),
        time_stop_at=FakeClock().now() + timedelta(hours=1),
    )


def snap(**kw: Any) -> PortfolioSnapshot:
    base: dict[str, Any] = dict(
        ts=FakeClock().now(),
        equity_tmn=D("5000000"),
        cash_tmn=D("5000000"),
        peak_equity_tmn=D("5000000"),
        exposure_tmn=D("0"),
        open_positions=[],
        trades_last_hour=0,
        trades_today=0,
        daily_pnl_pct=0.0,
        drawdown_pct=0.0,
        breaker_state=BreakerState.NORMAL,
    )
    base.update(kw)
    return PortfolioSnapshot(**base)


def make(
    clock: FakeClock | None = None,
    phase: str = "NONE",
    stale: bool = False,
    sides: list[Side] | None = None,
    cooldowns: CooldownTracker | None = None,
) -> DefaultRiskManager:
    clock = clock or FakeClock()
    return DefaultRiskManager(
        CFG,
        SymbolGuard(DENYLIST, REG),
        clock,
        cooldowns or CooldownTracker(CFG),
        endgame_phase=lambda _n: phase,
        is_stale=lambda _s: stale,
        open_order_sides=lambda _s: sides or [],
    )


def run(
    rm: DefaultRiskManager,
    c: Candidate | None = None,
    review: TradeReview | None = None,
    reg: RegimeAssessment | None = None,
    pf: PortfolioSnapshot | None = None,
    b: OrderBook | None = None,
) -> Any:
    c = c or cand()
    return rm.evaluate(
        c,
        ML,
        review,
        reg or regime(),
        pf or snap(),
        MKT.get(c.symbol, MKT["BTC/TMN"]),
        b or book(symbol=c.symbol),
    )


def test_approved_path() -> None:
    review = TradeReview(status=ComponentStatus.OK, verdict="APPROVE", size_multiplier=0.5, reasons=[])
    d = run(make(), review=review)
    assert d.approved and d.checks_failed == []
    # 0.5 % × 5M / ~1.5 % ≈ 1.67M -> capped 1M × regime 0.7 × llm 0.5 = 350k
    assert D("340000") < d.size_tmn <= D("350000") and d.qty > 0
    assert d.size_tmn <= D("5000000") * D("0.2")


CASES: list[tuple[str, Callable[[], Any]]] = [
    (codes.RISK_SYMBOL_NOT_ALLOWED, lambda: run(make(), c=cand("TON/TMN"))),
    (codes.RISK_BREAKER_ACTIVE, lambda: run(make(), pf=snap(breaker_state=BreakerState.NO_NEW_ENTRIES))),
    (codes.RISK_ENDGAME, lambda: run(make(phase="NO_NEW"))),
    (codes.RISK_REGIME_PANIC, lambda: run(make(), reg=regime(Regime.PANIC, 0.0))),
    (
        codes.RISK_MAX_POSITIONS,
        lambda: run(make(), pf=snap(open_positions=[open_pos(s) for s in ("A/TMN", "B/TMN", "C/TMN")])),
    ),
    (codes.RISK_MARKET_ALREADY_OPEN, lambda: run(make(), pf=snap(open_positions=[open_pos("BTC/TMN")]))),
    (codes.RISK_MAX_TRADES_HOUR, lambda: run(make(), pf=snap(trades_last_hour=4))),
    (codes.RISK_MAX_TRADES_DAY, lambda: run(make(), pf=snap(trades_today=25))),
    (codes.RISK_SPREAD_TOO_WIDE, lambda: run(make(), b=book(0.5))),
    (codes.RISK_STALE_DATA, lambda: run(make(stale=True))),
    (codes.RISK_OPPOSITE_ORDER_OPEN, lambda: run(make(sides=[Side.SELL]))),
    (codes.RISK_MAX_EXPOSURE, lambda: run(make(), pf=snap(exposure_tmn=D("2500000")))),
    (codes.RISK_INSUFFICIENT_CASH, lambda: run(make(), pf=snap(cash_tmn=D("50000")))),
    (codes.RISK_SIZE_ZERO, lambda: run(make(), c=cand(stop=PRICE * 1.01))),
    (
        codes.RISK_BELOW_MIN_NOTIONAL,
        lambda: run(make(), pf=snap(equity_tmn=D("400000"), cash_tmn=D("400000"))),
    ),
]


@pytest.mark.parametrize(("code", "fn"), CASES, ids=[c for c, _ in CASES])
def test_reject_codes(code: str, fn: Callable[[], Any]) -> None:
    d = fn()
    assert not d.approved and code in d.checks_failed and d.qty == 0 and d.size_tmn == 0


def test_cooldowns() -> None:
    clock = FakeClock()
    cd = CooldownTracker(CFG)
    cd.record_exit("ETH/TMN", clock.now(), D("-10"))
    d = run(make(clock, cooldowns=cd))
    assert d.approved  # one loss, other market
    d = run(make(clock, cooldowns=cd), c=cand("ETH/TMN"), b=book(symbol="ETH/TMN"))
    assert codes.RISK_COOLDOWN_MARKET in d.checks_failed
    cd.record_exit("SOL/TMN", clock.now(), D("-5"))  # second consecutive loss -> 60 min pause
    d = run(make(clock, cooldowns=cd))
    assert codes.RISK_COOLDOWN_LOSSES in d.checks_failed
    clock.advance(61 * 60)
    assert run(make(clock, cooldowns=cd)).approved
    restored = CooldownTracker(CFG)
    restored.load_dict(cd.to_dict())
    assert restored.market_exit == cd.market_exit


def test_all_failures_collected() -> None:
    d = run(make(stale=True), pf=snap(trades_last_hour=9, breaker_state=BreakerState.FLATTEN_AND_HALT))
    assert {codes.RISK_STALE_DATA, codes.RISK_MAX_TRADES_HOUR, codes.RISK_BREAKER_ACTIVE} <= set(
        d.checks_failed
    )
    assert d.checks_failed[0] == codes.RISK_BREAKER_ACTIVE  # order of checks preserved


def test_entry_counter() -> None:
    clock = FakeClock()
    ec = EntryCounter()
    for m in (0, 30, 59, 61):
        ec.record(clock.now() + timedelta(minutes=m))
    now = clock.now() + timedelta(minutes=90)
    assert ec.last_hour(now) == 2 and ec.since(clock.now()) == 4  # window (30 min, 90 min]
