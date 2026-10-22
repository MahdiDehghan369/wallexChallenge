"""T-704: end-game phases and liquidation with FakeClock + FakeBroker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.config import EndgameConfig, load_config
from siavash.core.models import Candidate, Fill, Order, OrderBook, OrderBookLevel, RiskDecision
from siavash.core.types import DecisionType, ExitReason, Side, StrategyId
from siavash.engine.decision_log import MemoryDecisionLogger
from siavash.engine.positions import PositionManager
from siavash.engine.store import InMemoryStore
from siavash.exchange import endpoints as ep
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from siavash.execution.router import OrderRouter
from siavash.risk.endgame import (
    EndgameConfigError,
    EndgameLiquidator,
    assert_endgame_configured,
    endgame_phase,
)
from tests.fakes.broker import FakeBroker
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import DENYLIST, load

D = Decimal
END = datetime(2026, 11, 8, 20, 30, tzinfo=UTC)
BASE_CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
CFG = BASE_CFG.model_copy(
    update={"risk": BASE_CFG.risk.model_copy(update={"endgame": EndgameConfig(competition_end_utc=END)})}
)
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
PRICES = {"BTC/TMN": D("7350000000"), "ETH/TMN": D("260100000"), "USDT/TMN": D("102500")}


def test_phases() -> None:
    assert endgame_phase(END - timedelta(hours=7), CFG) == "NONE"
    assert endgame_phase(END - timedelta(hours=6), CFG) == "NO_NEW"
    assert endgame_phase(END - timedelta(hours=2, minutes=59), CFG) == "UNWIND"
    assert endgame_phase(END - timedelta(minutes=59), CFG) == "FORCE"
    assert endgame_phase(END + timedelta(seconds=1), CFG) == "DONE"
    assert endgame_phase(END, BASE_CFG) == "NONE"  # not configured


def test_live_requires_end_time() -> None:
    live = BASE_CFG.model_copy(update={"base": BASE_CFG.base.model_copy(update={"mode": "live"})})
    with pytest.raises(EndgameConfigError):
        assert_endgame_configured(live)
    assert_endgame_configured(BASE_CFG)  # paper: fine


class FillAtLimit(FakeBroker):
    """Every resting order fills completely on its first status poll."""

    async def get_order(self, symbol: str, client_order_id: str) -> Order | None:
        o = self.orders.get(client_order_id)
        if o is not None and not o.status.is_terminal:
            self.simulate_fill(client_order_id)
        return self.orders.get(client_order_id)


def _book(sym: str) -> OrderBook:
    p = PRICES[sym]
    return OrderBook(
        symbol=sym,
        ts=END,
        bids=[OrderBookLevel(price=p, qty=D("1000"))],
        asks=[OrderBookLevel(price=p * D("1.001"), qty=D("1000"))],
    )


async def book_fn(sym: str) -> OrderBook:
    return _book(sym)


async def test_force_liquidates_everything(tmp_path: Path) -> None:
    clock = FakeClock(END - timedelta(minutes=30))
    guard = SymbolGuard(DENYLIST, REG, allow_usdt_liquidation=True)
    broker = FillAtLimit(
        {"TMN": D("1000000"), "BTC": D("0.0004"), "ETH": D("0.0123"), "USDT": D("7.5")},
        fee_pct="0.35",
        clock=clock,
        guard=guard,
    )
    # a resting buy order that must be cancelled first (it locks TMN)
    from siavash.core.models import OrderRequest
    from siavash.core.types import OrderType

    await FakeBroker.place_order(
        broker,
        OrderRequest(
            symbol="BTC/TMN",
            side=Side.BUY,
            type=OrderType.LIMIT,
            qty=D("0.0001"),
            price=D("7000000000"),
            client_order_id="SIAV-old-E1",
            decision_id="old",
            purpose="entry",
        ),
    )
    logger = MemoryDecisionLogger()

    async def value(asset: str, q: Decimal) -> Decimal | None:
        return q * PRICES[f"{asset}/TMN"] if f"{asset}/TMN" in PRICES else None

    liq = EndgameLiquidator(
        broker,
        OrderRouter(broker, REG, clock, CFG, book_fn=book_fn),
        REG,
        clock,
        CFG,
        book_fn,
        decision_logger=logger,
        report_path=tmp_path / "endgame.md",
        value_fn=value,
    )
    orders = await liq.run_step()
    assert {o.symbol for o in orders} == {"BTC/TMN", "ETH/TMN", "USDT/TMN"}
    assert orders[-1].symbol == "USDT/TMN"  # USDT converted last
    bal = await broker.get_balances()
    for asset in ("BTC", "ETH", "USDT"):
        m = REG.get(f"{asset}/TMN")
        assert bal[asset].total < m.min_qty, asset
    assert bal["TMN"].locked == 0
    assert any(r.type is DecisionType.ENDGAME for r in logger.records)
    text = (tmp_path / "endgame.md").read_text(encoding="utf-8")
    assert "return (official formula" in text


async def test_unwind_exits_positions_passively(tmp_path: Path) -> None:
    clock = FakeClock(END - timedelta(hours=2))
    broker = FillAtLimit({"TMN": D("0"), "BTC": D("0.0002")}, clock=clock, guard=SymbolGuard(DENYLIST, REG))
    pm = PositionManager(InMemoryStore(), clock)
    c = Candidate(
        candidate_id="c",
        ts=clock.now(),
        symbol="BTC/TMN",
        strategy=StrategyId.S3_IMPLIED_DEV,
        entry_ref_price=7.3e9,
        stop_loss=7.2e9,
        take_profit=7.5e9,
        time_stop_minutes=180,
        expected_move_pct=2.0,
        conditions={},
    )
    p = await pm.create(
        c, RiskDecision(approved=True, size_tmn=D("1"), qty=D("0.0002"), equity_tmn=D("1")), "D"
    )
    await pm.apply_entry_fill(
        p.position_id,
        Fill(
            fill_id="f",
            order_id="1",
            client_order_id="SIAV-D-E1",
            symbol="BTC/TMN",
            side=Side.BUY,
            price=D("7.3e9"),
            qty=D("0.0002"),
            fee=D(0),
            fee_asset="TMN",
            ts=clock.now(),
        ),
        D(0),
    )
    liq = EndgameLiquidator(
        broker,
        OrderRouter(broker, REG, clock, CFG, book_fn=book_fn),
        REG,
        clock,
        CFG,
        book_fn,
        positions=pm,
        report_path=None,
    )
    orders = await liq.run_step()
    assert len(orders) == 1 and orders[0].price == _book("BTC/TMN").best_ask  # passive first
    assert pm.get(p.position_id).status.value == "PENDING_EXIT"
    assert pm.pending_exit_reason[p.position_id] is ExitReason.ENDGAME
    assert orders[0].client_order_id.endswith("-G1")


async def test_no_action_before_unwind() -> None:
    clock = FakeClock(END - timedelta(hours=5))
    broker = FakeBroker({"TMN": D("1"), "BTC": D("1")}, clock=clock)
    liq = EndgameLiquidator(
        broker, OrderRouter(broker, REG, clock, CFG), REG, clock, CFG, book_fn, report_path=None
    )
    assert await liq.run_step() == [] and liq.last_phase == "NO_NEW"
