"""T-106: SymbolGuard."""

from __future__ import annotations

import pytest

from siavash.core.types import Side
from siavash.exchange import endpoints as ep
from siavash.exchange.errors import SymbolNotAllowed
from siavash.exchange.guard import SymbolGuard, load_denylist
from siavash.exchange.markets import MarketRegistry
from tests.unit.exchange.conftest import DENYLIST, load

REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))


@pytest.fixture
def guard() -> SymbolGuard:
    return SymbolGuard(DENYLIST, REG, allowed_quotes=["TMN"], allow_usdt_liquidation=True)


def test_denylist_size() -> None:
    assert len(load_denylist(DENYLIST)) == 345


@pytest.mark.parametrize("sym", ["TON/TMN", "NEAR/TMN", "YFI/USDT", "1BBABYDOGE/TMN", "TONTMN"])
def test_denied(guard: SymbolGuard, sym: str) -> None:
    ok, reason = guard.is_tradable(sym)
    assert not ok and "denylist" in reason
    with pytest.raises(SymbolNotAllowed):
        guard.assert_tradable(sym)


@pytest.mark.parametrize("sym", ["BTC/TMN", "ETH/TMN", "BTCTMN"])
def test_allowed(guard: SymbolGuard, sym: str) -> None:
    assert guard.is_tradable(sym) == (True, "")
    guard.assert_tradable(sym, Side.BUY)


def test_other_rejections(guard: SymbolGuard) -> None:
    assert not guard.is_tradable("BTC/USDT")[0]  # quote not allowed
    assert not guard.is_tradable("XRP/TMN")[0]  # inactive
    assert not guard.is_tradable("DOGE/TMN")[0]  # not in registry
    assert not guard.is_tradable("garbage")[0]


def test_usdt_liquidation_only_sell(guard: SymbolGuard) -> None:
    assert guard.is_tradable("USDT/TMN", Side.SELL) == (True, "")
    assert not guard.is_tradable("USDT/TMN", Side.BUY)[0]
    strict = SymbolGuard(DENYLIST, REG, allow_usdt_liquidation=False)
    assert not strict.is_tradable("USDT/TMN", Side.SELL)[0]


def test_pair_level_not_coin_level(guard: SymbolGuard) -> None:
    # YFI/USDT is banned but that says nothing about other YFI pairs: guard works on pairs
    assert guard.is_denied("YFI/USDT") and not guard.is_denied("YFI/TMN")
