"""T-007: contract model tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from siavash.core.ids import client_order_id, is_ours, new_ulid
from siavash.core.models import (
    Balance,
    Candidate,
    Candle,
    DecisionRecord,
    Fill,
    MarketInfo,
    MLScore,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    PortfolioSnapshot,
    Position,
    PublicTrade,
    RegimeAssessment,
    RiskDecision,
    Ticker,
    TradeReview,
)
from siavash.core.types import (
    BreakerState,
    ComponentStatus,
    DecisionType,
    Mode,
    OrderStatus,
    OrderType,
    PositionStatus,
    Regime,
    RejectStage,
    Side,
    StrategyId,
    Timeframe,
)

T0 = datetime(2026, 11, 5, 9, 15, tzinfo=UTC)


def _lvl(p: str, q: str) -> OrderBookLevel:
    return OrderBookLevel(price=Decimal(p), qty=Decimal(q))


def _position() -> Position:
    return Position(
        position_id="P1",
        symbol="BTC/TMN",
        strategy=StrategyId.S3_IMPLIED_DEV,
        status=PositionStatus.OPEN,
        entry_decision_id="D1",
        qty=Decimal("0.001"),
        avg_entry=Decimal("7350000000"),
        stop_loss=Decimal("7262000000"),
        take_profit=Decimal("7460000000"),
        time_stop_at=T0 + timedelta(hours=3),
        opened_at=T0,
    )


def _regime() -> RegimeAssessment:
    return RegimeAssessment(
        ts=T0,
        regime=Regime.RANGE,
        risk_multiplier=0.7,
        enabled_strategies=[StrategyId.S2_MEAN_REVERSION, StrategyId.S3_IMPLIED_DEV],
        reasons=["adx low"],
        source="rule",
        rule_regime=Regime.RANGE,
    )


def _candidate() -> Candidate:
    return Candidate(
        candidate_id=new_ulid(),
        ts=T0,
        symbol="BTC/TMN",
        strategy=StrategyId.S3_IMPLIED_DEV,
        entry_ref_price=7350000000.0,
        stop_loss=7262000000.0,
        take_profit=7460000000.0,
        time_stop_minutes=180,
        expected_move_pct=1.5,
        conditions={"z_dev": -2.4, "ok": True, "note": "x"},
    )


def all_models() -> list[BaseModel]:
    review = TradeReview(status=ComponentStatus.OK, verdict="APPROVE", size_multiplier=0.75, reasons=["ok"])
    ml = MLScore(status=ComponentStatus.OK, p_win=0.62, threshold=0.55, top_features=[("x_implied_z", -0.21)])
    risk = RiskDecision(
        approved=True, size_tmn=Decimal("760000"), qty=Decimal("0.0001"), equity_tmn=Decimal("5032000")
    )
    snapshot = PortfolioSnapshot(
        ts=T0,
        equity_tmn=Decimal("5000000"),
        cash_tmn=Decimal("4000000"),
        peak_equity_tmn=Decimal("5100000"),
        exposure_tmn=Decimal("1000000"),
        open_positions=[_position()],
        trades_last_hour=1,
        trades_today=3,
        daily_pnl_pct=-0.4,
        drawdown_pct=1.96,
        breaker_state=BreakerState.NORMAL,
    )
    return [
        MarketInfo(
            symbol="BTC/TMN",
            exchange_symbol="BTCTMN",
            base="BTC",
            quote="TMN",
            price_tick=Decimal("1"),
            qty_step=Decimal("0.000001"),
            min_qty=Decimal("0.000001"),
            min_notional=Decimal("100000"),
            is_active=True,
        ),
        Candle(
            symbol="BTC/TMN",
            timeframe=Timeframe.M15,
            ts_open=T0,
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=10,
        ),
        OrderBook(symbol="BTC/TMN", ts=T0, bids=[_lvl("99", "1")], asks=[_lvl("101", "2")]),
        Ticker(
            symbol="BTC/TMN",
            ts=T0,
            last=Decimal("100"),
            bid=None,
            ask=Decimal("101"),
            volume_24h_quote=Decimal("1e9"),
        ),
        PublicTrade(symbol="BTC/TMN", ts=T0, price=Decimal("100"), qty=Decimal("0.1"), side=Side.BUY),
        Balance(asset="TMN", free=Decimal("100"), locked=Decimal("5")),
        OrderRequest(
            symbol="BTC/TMN",
            side=Side.BUY,
            type=OrderType.LIMIT,
            qty=Decimal("0.001"),
            price=Decimal("100"),
            client_order_id="SIAV-D-E1",
            decision_id="D",
            purpose="entry",
        ),
        Order(
            order_id="1",
            client_order_id="SIAV-D-E1",
            symbol="BTC/TMN",
            side=Side.BUY,
            type=OrderType.LIMIT,
            price=Decimal("100"),
            qty=Decimal("0.001"),
            status=OrderStatus.NEW,
            created_at=T0,
            updated_at=T0,
        ),
        Fill(
            fill_id="f1",
            order_id="1",
            client_order_id="SIAV-D-E1",
            symbol="BTC/TMN",
            side=Side.BUY,
            price=Decimal("100"),
            qty=Decimal("0.001"),
            fee=Decimal("0.0000035"),
            fee_asset="BTC",
            ts=T0,
        ),
        _candidate(),
        ml,
        _regime(),
        review,
        snapshot,
        risk,
        _position(),
        DecisionRecord(
            decision_id=new_ulid(),
            ts=T0,
            mode=Mode.PAPER,
            git_sha="abc1234",
            version_tag=None,
            config_hash="sha256:x",
            type=DecisionType.ENTRY_CANDIDATE,
            symbol="BTC/TMN",
            strategy=StrategyId.S3_IMPLIED_DEV,
            regime=_regime(),
            signal=_candidate(),
            ml=ml,
            llm_review=review,
            risk=risk,
            outcome_action="REJECTED",
            rejected_at=RejectStage.LLM,
            final_reason="test",
            extra={"k": [1, 2]},
        ),
    ]


@pytest.mark.parametrize("model", all_models(), ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    dumped = model.model_dump(mode="json")
    text = json.dumps(dumped)
    restored = type(model).model_validate_json(text)
    assert restored == model


def test_json_types() -> None:
    d = all_models()[-1].model_dump(mode="json")
    assert d["ts"] == "2026-11-05T09:15:00Z"
    assert isinstance(d["risk"]["size_tmn"], str)


def test_trade_review_multiplier_bounds() -> None:
    with pytest.raises(ValidationError):
        TradeReview(status=ComponentStatus.OK, verdict="APPROVE", size_multiplier=1.5, reasons=[])
    with pytest.raises(ValidationError):
        TradeReview(status=ComponentStatus.OK, verdict="APPROVE", size_multiplier=0.1, reasons=[])


def test_frozen_and_extra_forbid() -> None:
    bal = Balance(asset="TMN", free=Decimal("1"), locked=Decimal("2"))
    assert bal.total == Decimal("3")
    with pytest.raises(ValidationError):
        bal.free = Decimal("5")  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Balance(asset="TMN", free=Decimal("1"), locked=Decimal("0"), extra_field=1)  # type: ignore[call-arg]


def test_orderbook_properties() -> None:
    ob = OrderBook(symbol="X/TMN", ts=T0, bids=[_lvl("99", "1"), _lvl("98", "1")], asks=[_lvl("101", "1")])
    assert ob.best_bid == Decimal("99")
    assert ob.best_ask == Decimal("101")
    assert ob.mid == Decimal("100")
    assert ob.spread_pct == pytest.approx(2.0)
    empty = OrderBook(symbol="X/TMN", ts=T0, bids=[], asks=[_lvl("1", "1")])
    assert empty.mid is None and empty.spread_pct is None and empty.best_bid is None


def test_enum_helpers() -> None:
    assert OrderStatus.FILLED.is_terminal and not OrderStatus.NEW.is_terminal
    assert Timeframe.M15.seconds == 900 and Timeframe.H4.seconds == 14400


def test_client_order_id() -> None:
    did = new_ulid()
    coid = client_order_id(did, "E", 1)
    assert coid == f"SIAV-{did}-E1"
    assert coid.startswith("SIAV-")
    assert is_ours(coid)
    assert not is_ours("manual-123")
    with pytest.raises(ValueError):
        client_order_id(did, "Z", 1)
    with pytest.raises(ValueError):
        client_order_id("", "E", 1)
