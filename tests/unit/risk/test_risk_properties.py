"""T-705: property-based risk invariants over random candidate / price / fill sequences.

Invariants (checked after every executed entry): total exposure ≤ 50 % of equity, no position
> 20 % of equity, at most 3 open positions, no entry while the breaker is not NORMAL, every order
carries a decision_id, and no order ever reaches a banned market.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from siavash.config import load_config
from siavash.core.models import (
    Candidate,
    Fill,
    MLScore,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    PortfolioSnapshot,
    RegimeAssessment,
    TradeReview,
)
from siavash.core.types import BreakerState, ComponentStatus, ExitReason, OrderType, Regime, Side, StrategyId
from siavash.engine.positions import PositionManager
from siavash.engine.store import InMemoryStore
from siavash.exchange import endpoints as ep
from siavash.exchange.errors import SymbolNotAllowed
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.risk.limits import CooldownTracker
from siavash.risk.manager import DefaultRiskManager
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import DENYLIST, load

D = Decimal
CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
GUARD = SymbolGuard(DENYLIST, REG)
BASE_PRICES = {"BTC/TMN": 7.35e9, "ETH/TMN": 2.6e8, "TON/TMN": 5.2e5, "NEAR/TMN": 4.5e5, "XRP/TMN": 6.0e4}
SYMBOLS = list(BASE_PRICES)
BANNED = {"TON/TMN", "NEAR/TMN"}
ML = MLScore(status=ComponentStatus.SKIPPED)

step = st.tuples(
    st.sampled_from(["candidate", "candidate", "candidate", "move", "exit"]),
    st.sampled_from(SYMBOLS),
    st.floats(min_value=-0.05, max_value=0.05),  # price move / stop distance driver
    st.sampled_from(
        [
            BreakerState.NORMAL,
            BreakerState.NORMAL,
            BreakerState.NORMAL,
            BreakerState.NO_NEW_ENTRIES,
            BreakerState.FLATTEN_AND_HALT,
        ]
    ),
    st.sampled_from([Regime.TREND_UP, Regime.RANGE, Regime.HIGH_VOL, Regime.PANIC]),
    st.floats(min_value=0.0, max_value=1.5),  # llm size multiplier (clamped by the layer)
)


def _book(sym: str, price: Decimal) -> OrderBook:
    return OrderBook(
        symbol=sym,
        ts=FakeClock().now(),
        bids=[OrderBookLevel(price=price * D("0.9998"), qty=D("1e9"))],
        asks=[OrderBookLevel(price=price, qty=D("1e9"))],
    )


async def _simulate(steps: list[tuple]) -> int:  # type: ignore[type-arg]
    """Run the sequence; returns the number of executed entries."""
    clock = FakeClock()
    prices = {s: D(str(p)) for s, p in BASE_PRICES.items()}
    broker = FakeBroker({"TMN": D("5000000")}, fee_pct="0.35", clock=clock, guard=GUARD, prices=prices)
    pm = PositionManager(InMemoryStore(), clock)
    rm = DefaultRiskManager(CFG, GUARD, clock, CooldownTracker(CFG))
    mult = dict(CFG.risk.regime_multipliers)
    n = 0
    for action, sym, x, breaker, regime_label, llm in steps:
        clock.advance(600)
        n += 1
        if action == "move":
            prices[sym] = prices[sym] * D(str(1 + x))
            continue
        bal = await broker.get_balances()
        cash = bal["TMN"].free if "TMN" in bal else D(0)
        exposure = pm.exposure_tmn(prices)
        equity = (bal["TMN"].total if "TMN" in bal else D(0)) + exposure
        if action == "exit":
            p = pm.by_symbol(sym)
            if p is not None and p.qty > 0:
                await pm.start_exit(p.position_id, ExitReason.STOP_LOSS)
                coid = f"SIAV-X{n}-X1"
                await broker.place_order(
                    OrderRequest(
                        symbol=sym,
                        side=Side.SELL,
                        type=OrderType.MARKET,
                        qty=p.qty,
                        price=None,
                        client_order_id=coid,
                        decision_id=f"X{n}",
                        purpose="exit",
                    )
                )
                f = broker.fills[-1]
                closed = await pm.apply_exit_fill(p.position_id, f, f.fee)
                rm.cooldowns.record_exit(sym, clock.now(), closed.realized_pnl_tmn)
            continue
        price = prices[sym]
        stop = float(price) * (1 - max(abs(x), 0.003))
        c = Candidate(
            candidate_id=f"c{n}",
            ts=clock.now(),
            symbol=sym,
            strategy=StrategyId.S3_IMPLIED_DEV,
            entry_ref_price=float(price),
            stop_loss=stop,
            take_profit=float(price) * 1.03,
            time_stop_minutes=180,
            expected_move_pct=3.0,
            conditions={},
        )
        regime = RegimeAssessment(
            ts=clock.now(),
            regime=regime_label,
            risk_multiplier=mult[regime_label.value],
            enabled_strategies=list(StrategyId),
            reasons=[],
            source="rule",
            rule_regime=regime_label,
        )
        snap = PortfolioSnapshot(
            ts=clock.now(),
            equity_tmn=equity,
            cash_tmn=cash,
            peak_equity_tmn=max(equity, D("5000000")),
            exposure_tmn=exposure,
            open_positions=pm.open_positions(),
            trades_last_hour=0,
            trades_today=0,
            daily_pnl_pct=0.0,
            drawdown_pct=0.0,
            breaker_state=breaker,
        )
        review = TradeReview(
            status=ComponentStatus.OK, verdict="APPROVE", size_multiplier=min(max(llm, 0.25), 1.0), reasons=[]
        )
        decision = rm.evaluate(
            c, ML, review, regime, snap, REG.get(sym) if sym in REG else REG.get("BTC/TMN"), _book(sym, price)
        )
        if not decision.approved:
            continue
        # --- invariants on every executed entry ---
        assert breaker is BreakerState.NORMAL
        assert regime_label is not Regime.PANIC
        assert sym not in BANNED
        assert len(pm.open_positions()) < CFG.risk.max_concurrent_positions
        assert decision.size_tmn <= equity * D(str(CFG.risk.max_position_pct)) / 100 + D("1e-6")
        assert exposure + decision.size_tmn <= equity * D(str(CFG.risk.max_total_exposure_pct)) / 100 + D(
            "1e-6"
        )
        did = f"D{n}"
        pos = await pm.create(c, decision, did)
        req = OrderRequest(
            symbol=sym,
            side=Side.BUY,
            type=OrderType.LIMIT,
            qty=decision.qty,
            price=price,
            client_order_id=f"SIAV-{did}-E1",
            decision_id=did,
            purpose="entry",
        )
        await broker.place_order(req)
        f: Fill = broker.simulate_fill(req.client_order_id)
        await pm.apply_entry_fill(pos.position_id, f, f.fee)
        await pm.entry_finished(pos.position_id)
        assert len(pm.open_positions()) <= CFG.risk.max_concurrent_positions
    for r in broker.placed:
        assert r.decision_id
        assert r.symbol not in BANNED
    return sum(1 for r in broker.placed if r.purpose == "entry")


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(step, min_size=5, max_size=40))
def test_risk_invariants(steps: list[tuple]) -> None:  # type: ignore[type-arg]
    asyncio.run(_simulate(steps))


def test_sequence_is_not_vacuous() -> None:
    """A fixed sequence must execute several entries and hit the position cap without breaking invariants."""
    n = BreakerState.NORMAL
    seq = [
        ("candidate", s, -0.02, n, Regime.TREND_UP, 1.0)
        for s in ("BTC/TMN", "ETH/TMN", "XRP/TMN", "TON/TMN", "NEAR/TMN")
    ]
    seq += [
        ("exit", "BTC/TMN", 0.0, n, Regime.TREND_UP, 1.0),
        ("candidate", "BTC/TMN", -0.02, n, Regime.TREND_UP, 1.0),
    ]
    entries = asyncio.run(_simulate(seq))
    assert entries == 2  # BTC, ETH (XRP inactive, TON/NEAR banned); BTC re-entry blocked by market cooldown


def test_banned_never_reaches_broker() -> None:
    async def run() -> None:
        clock = FakeClock()
        broker = FakeBroker({"TMN": D("5000000")}, clock=clock, guard=GUARD, prices={"TON/TMN": D("1")})
        req = OrderRequest(
            symbol="TON/TMN",
            side=Side.BUY,
            type=OrderType.MARKET,
            qty=D("1"),
            price=None,
            client_order_id="SIAV-x-E1",
            decision_id="x",
            purpose="entry",
        )
        with pytest.raises(SymbolNotAllowed):
            await broker.place_order(req)
        assert broker.placed == []

    asyncio.run(run())
