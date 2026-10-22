"""T-008: fakes and synthetic data."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side, Timeframe
from tests.fakes.broker import FakeBroker, FakeBrokerError, InsufficientFunds
from tests.fakes.clock import FakeClock
from tests.fakes.market_data import InMemoryMarketData
from tests.fakes.synthetic import OHLCV_COLUMNS, make_synthetic_market, resample_ohlcv, to_candles


def _req(
    coid: str,
    side: Side = Side.BUY,
    typ: OrderType = OrderType.LIMIT,
    qty: str = "0.01",
    price: str | None = "100",
) -> OrderRequest:
    return OrderRequest(
        symbol="BTC/TMN",
        side=side,
        type=typ,
        qty=Decimal(qty),
        price=Decimal(price) if price else None,
        client_order_id=coid,
        decision_id="D1",
        purpose="entry",
    )


def test_synthetic_deterministic() -> None:
    a = make_synthetic_market(seed=7, days=5)
    b = make_synthetic_market(seed=7, days=5)
    c = make_synthetic_market(seed=8, days=5)
    assert set(a) == {"BTC/USDT", "USDT/TMN", "BTC/TMN", "ETH/USDT", "ETH/TMN"}
    for sym in a:
        pd.testing.assert_frame_equal(a[sym], b[sym])
        assert list(a[sym].columns) == OHLCV_COLUMNS
        assert len(a[sym]) == 5 * 96
    assert not a["BTC/USDT"]["close"].equals(c["BTC/USDT"]["close"])


def test_synthetic_ohlc_valid() -> None:
    m = make_synthetic_market(seed=1, days=3)
    for df in m.values():
        assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
        assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()
        assert (df["low"] > 0).all() and (df["volume"] > 0).all()
        assert df["ts_open"].dt.tz is not None


def test_tmn_lags_implied_by_two_bars() -> None:
    m = make_synthetic_market(seed=3, days=20)
    implied = m["BTC/USDT"]["close"] * m["USDT/TMN"]["close"]
    tmn = m["BTC/TMN"]["close"]
    r_imp = np.log(implied).diff()
    r_tmn = np.log(tmn).diff()
    corr0 = r_tmn.corr(r_imp)
    corr2 = r_tmn.corr(r_imp.shift(2))
    assert corr2 > corr0
    assert corr2 > 0.3


def test_resample_and_candles() -> None:
    m = make_synthetic_market(seed=1, days=2)
    h1 = resample_ohlcv(m["BTC/TMN"], "1h")
    assert len(h1) == 48
    first4 = m["BTC/TMN"].iloc[:4]
    assert h1["high"].iloc[0] == first4["high"].max()
    assert h1["close"].iloc[0] == first4["close"].iloc[-1]
    candles = to_candles(h1, "BTC/TMN", "1h")
    assert candles[0].timeframe is Timeframe.H1 and candles[0].ts_open.tzinfo is not None


async def test_fake_clock() -> None:
    clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    await clock.sleep(60)
    clock.advance(30)
    assert clock.now() == datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)
    with pytest.raises(ValueError):
        clock.advance(-1)


async def test_in_memory_market_data_no_lookahead() -> None:
    m = make_synthetic_market(seed=1, days=1)
    candles = to_candles(m["BTC/TMN"], "BTC/TMN", "15m")
    clock = FakeClock(candles[0].ts_open + timedelta(minutes=45))
    md = InMemoryMarketData(candles={("BTC/TMN", Timeframe.M15): candles}, clock=clock)
    got = await md.get_candles(
        "BTC/TMN", Timeframe.M15, candles[0].ts_open, candles[-1].ts_open + timedelta(days=1)
    )
    assert len(got) == 3  # only closed candles


async def test_fake_broker_idempotent_and_fills() -> None:
    clock = FakeClock()
    broker = FakeBroker({"TMN": Decimal("10000")}, fee_pct="0.5", clock=clock)
    o1 = await broker.place_order(_req("SIAV-D1-E1"))
    o2 = await broker.place_order(_req("SIAV-D1-E1"))
    assert o1 == o2 and len(broker.placed) == 1
    bal = await broker.get_balances()
    assert bal["TMN"].locked == Decimal("1.005")
    fill = broker.simulate_fill("SIAV-D1-E1", Decimal("99"), Decimal("0.004"))
    assert fill.fee == Decimal("0.004") * 99 * Decimal("0.005")
    order = await broker.get_order("BTC/TMN", "SIAV-D1-E1")
    assert order is not None and order.status is OrderStatus.PARTIALLY_FILLED
    await broker.cancel_order("BTC/TMN", "SIAV-D1-E1")
    bal = await broker.get_balances()
    assert bal["TMN"].locked == 0
    assert bal["BTC"].free == Decimal("0.004")
    assert bal["TMN"].free == Decimal("10000") - Decimal("0.004") * 99 * Decimal("1.005")


async def test_fake_broker_market_and_balance_checks() -> None:
    broker = FakeBroker({"TMN": Decimal("1000")}, fee_pct=0, prices={"BTC/TMN": Decimal("100")})
    o = await broker.place_order(_req("SIAV-D1-E2", typ=OrderType.MARKET, qty="5", price=None))
    assert o.status is OrderStatus.FILLED and o.avg_fill_price == Decimal("100")
    with pytest.raises(InsufficientFunds):
        await broker.place_order(_req("SIAV-D1-E3", typ=OrderType.MARKET, qty="100", price=None))
    with pytest.raises(InsufficientFunds):
        await broker.place_order(_req("SIAV-D1-X1", side=Side.SELL, qty="6"))
    bad = OrderRequest(
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("1"),
        price=Decimal("1"),
        client_order_id="SIAV-x-E1",
        decision_id="",
        purpose="entry",
    )
    with pytest.raises(FakeBrokerError):
        await broker.place_order(bad)
