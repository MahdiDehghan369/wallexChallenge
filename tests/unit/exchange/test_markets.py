"""T-103: market registry and rounding (incl. hypothesis)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from siavash.core.models import MarketInfo
from siavash.core.types import Side
from siavash.exchange import endpoints as ep
from siavash.exchange.markets import MarketRegistry, UnknownMarket
from tests.fakes.clock import FakeClock
from tests.unit.exchange.conftest import load

MARKETS = ep.parse_markets(load("markets"))
REG = MarketRegistry.from_markets(MARKETS)


def test_get_and_formats() -> None:
    assert REG.get("BTC/TMN").exchange_symbol == "BTCTMN"
    assert REG.get("BTCTMN").symbol == "BTC/TMN"
    assert "ETH/TMN" in REG and "DOGE/TMN" not in REG
    with pytest.raises(UnknownMarket):
        REG.get("DOGE/TMN")


def test_round_price_directions() -> None:
    assert REG.round_price("BTC/USDT", Decimal("71705.129"), Side.BUY) == Decimal("71705.12")
    assert REG.round_price("BTC/USDT", Decimal("71705.121"), Side.SELL) == Decimal("71705.13")
    assert REG.round_price("BTC/TMN", Decimal("7350000000.6"), Side.BUY) == Decimal("7350000000")
    assert REG.round_price("BTC/TMN", Decimal("7350000000.2"), Side.SELL) == Decimal("7350000001")


def test_round_qty_and_min() -> None:
    assert REG.round_qty("BTC/TMN", Decimal("0.0001369")) == Decimal("0.000136")
    assert REG.check_min("BTC/TMN", Decimal("0.000136"), Decimal("7350000000"))
    assert not REG.check_min("BTC/TMN", Decimal("0.00001"), Decimal("7350000000"))  # 73,500 < 100,000
    assert not REG.check_min("ETH/TMN", Decimal("0.00005"), Decimal("1e12"))  # below min_qty
    q = REG.max_qty_for_notional("BTC/TMN", Decimal("1000000"), Decimal("7350000000"))
    assert q == Decimal("0.000136") and q * Decimal("7350000000") <= Decimal("1000000")


@settings(max_examples=300, deadline=None)
@given(
    sym=st.sampled_from([m.symbol for m in MARKETS]),
    price=st.decimals(
        min_value=Decimal("0.0001"),
        max_value=Decimal("1e11"),
        allow_nan=False,
        allow_infinity=False,
        places=6,
    ),
    qty=st.decimals(
        min_value=Decimal("0"), max_value=Decimal("1e6"), allow_nan=False, allow_infinity=False, places=8
    ),
)
def test_rounding_properties(sym: str, price: Decimal, qty: Decimal) -> None:
    m = REG.get(sym)
    rq = REG.round_qty(sym, qty)
    assert rq <= qty and rq >= 0
    assert (rq / m.qty_step) == (rq / m.qty_step).to_integral_value()
    assert qty - rq < m.qty_step
    for side in (Side.BUY, Side.SELL):
        rp = REG.round_price(sym, price, side)
        assert (rp / m.price_tick) == (rp / m.price_tick).to_integral_value()
        assert abs(rp - price) < m.price_tick
    assert REG.round_price(sym, price, Side.BUY) <= price <= REG.round_price(sym, price, Side.SELL)


async def test_refresh_with_loader() -> None:
    calls = 0

    async def loader() -> list[MarketInfo]:
        nonlocal calls
        calls += 1
        return MARKETS

    clock = FakeClock()
    reg = MarketRegistry(loader, clock, refresh_hours=6)
    assert await reg.refresh_if_stale() and calls == 1
    clock.advance(timedelta(hours=5).total_seconds())
    assert not await reg.refresh_if_stale()
    clock.advance(timedelta(hours=1).total_seconds())
    assert await reg.refresh_if_stale() and calls == 2
    assert reg.get("BTC/TMN").min_notional == Decimal("100000")
