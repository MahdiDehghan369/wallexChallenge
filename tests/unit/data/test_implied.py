"""T-205: implied price and deviation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from siavash.core.models import MarketInfo, OrderBook, OrderBookLevel
from siavash.data.implied import (
    close_series,
    implied_deviation,
    implied_features,
    implied_from_books,
    implied_pairs,
)
from tests.fakes.synthetic import make_synthetic_market


@pytest.fixture(scope="module")
def market() -> dict[str, pd.DataFrame]:
    return make_synthetic_market(seed=21, days=10)


def _series(m: dict[str, pd.DataFrame]) -> tuple[pd.Series, pd.Series, pd.Series]:
    return close_series(m["BTC/TMN"]), close_series(m["BTC/USDT"]), close_series(m["USDT/TMN"])


def test_basic_columns(market: dict[str, pd.DataFrame]) -> None:
    tmn, usdt, ut = _series(market)
    out = implied_deviation(tmn, usdt, ut, 96)
    assert list(out.columns) == [
        "implied",
        "dev_pct",
        "x_implied_z",
        "x_implied_dev_pct",
        "x_implied_dev_mean_pct",
    ]
    assert out["x_implied_z"].iloc[:95].isna().all()
    assert out["x_implied_z"].iloc[96:].notna().all()
    np.testing.assert_allclose(out["implied"], usdt * ut)
    assert out["dev_pct"].abs().median() < 1.0


def test_two_percent_drop_gives_strong_negative_z(market: dict[str, pd.DataFrame]) -> None:
    tmn, usdt, ut = _series(market)
    base = implied_deviation(tmn, usdt, ut, 96)
    # choose a calm bar: the 96-bar window has no big deviation
    calm = base["dev_pct"].rolling(96).std().iloc[200:]
    t = calm.idxmin()
    bumped = tmn.copy()
    bumped.loc[t] = bumped.loc[t] * 0.98
    out = implied_deviation(bumped, usdt, ut, 96)
    assert out.loc[t, "x_implied_z"] < -2
    assert out.loc[t, "dev_pct"] < base.loc[t, "dev_pct"] - 1.9


def test_no_lookahead(market: dict[str, pd.DataFrame]) -> None:
    tmn, usdt, ut = _series(market)
    full = implied_deviation(tmn, usdt, ut, 96)
    for i in (150, 400, 700):
        t = tmn.index[i]
        part = implied_deviation(tmn.loc[:t], usdt.loc[:t], ut.loc[:t], 96)
        assert part.iloc[-1]["x_implied_z"] == pytest.approx(full.loc[t, "x_implied_z"], rel=1e-9)


def test_inner_join_and_nan_gap(market: dict[str, pd.DataFrame]) -> None:
    tmn, usdt, ut = _series(market)
    tmn = tmn.drop(tmn.index[10])
    usdt = usdt.copy()
    usdt.iloc[20] = np.nan
    out = implied_deviation(tmn, usdt, ut, 5)
    assert len(out) == len(ut) - 1
    assert np.isnan(out["dev_pct"].iloc[19])


def test_implied_features(market: dict[str, pd.DataFrame]) -> None:
    f = implied_features(market["BTC/TMN"], market["BTC/USDT"], market["USDT/TMN"], 96)
    assert list(f.columns) == ["x_implied_z", "x_implied_dev_pct", "x_implied_dev_mean_pct"]


def _mi(sym: str, active: bool = True) -> MarketInfo:
    b, q = sym.split("/")
    one = Decimal("1")
    return MarketInfo(
        symbol=sym,
        exchange_symbol=b + q,
        base=b,
        quote=q,
        price_tick=one,
        qty_step=one,
        min_qty=one,
        min_notional=one,
        is_active=active,
    )


def test_implied_pairs() -> None:
    mk = [_mi(s) for s in ("BTC/TMN", "BTC/USDT", "USDT/TMN", "ETH/TMN", "ETH/USDT", "SOL/TMN")]
    mk.append(_mi("XRP/TMN"))
    mk.append(_mi("XRP/USDT", active=False))
    assert implied_pairs(mk) == [("BTC/TMN", "BTC/USDT"), ("ETH/TMN", "ETH/USDT")]
    assert implied_pairs(mk, allowed=lambda s: not s.startswith("ETH")) == [("BTC/TMN", "BTC/USDT")]


def _book(sym: str, bid: str, ask: str) -> OrderBook:
    lv = lambda p: [OrderBookLevel(price=Decimal(p), qty=Decimal("1"))]  # noqa: E731
    return OrderBook(symbol=sym, ts=datetime(2026, 1, 1, tzinfo=UTC), bids=lv(bid), asks=lv(ask))


def test_implied_from_books() -> None:
    r = implied_from_books(
        _book("BTC/TMN", "5880", "5900"), _book("BTC/USDT", "59", "61"), _book("USDT/TMN", "99", "101")
    )
    assert r is not None
    assert r["implied"] == pytest.approx(6000)
    assert r["dev_pct"] == pytest.approx((5890 / 6000 - 1) * 100)
