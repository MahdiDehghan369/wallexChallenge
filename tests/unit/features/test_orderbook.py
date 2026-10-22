"""T-302: order-book features."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from siavash.core.models import OrderBook, OrderBookLevel
from siavash.features.orderbook import OB_FEATURES, orderbook_features


def _lv(p: float, q: float) -> OrderBookLevel:
    return OrderBookLevel(price=Decimal(str(p)), qty=Decimal(str(q)))


def test_features() -> None:
    book = OrderBook(
        symbol="X/TMN",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        bids=[_lv(99, 3), _lv(98, 1), _lv(90, 100)],
        asks=[_lv(101, 1), _lv(102, 1)],
    )
    f = orderbook_features(book, 1.5)
    assert set(f) == set(OB_FEATURES)
    assert f["ob_spread_pct"] == pytest.approx(2.0)
    assert f["ob_imbalance_1"] == pytest.approx((3 - 1) / 4)
    assert f["ob_imbalance_5"] == pytest.approx((104 - 2) / 106)
    # within ±1.5 % of 100: bids 99, (98.5 limit -> 98 excluded), asks 101 (102 excluded? 101.5 limit)
    assert f["ob_depth_tmn_05"] == pytest.approx(99 * 3 + 101 * 1)
    micro = (99 * 1 + 101 * 3) / 4
    assert f["ob_microprice_dist_pct"] == pytest.approx((micro / 100 - 1) * 100)


def test_missing_book() -> None:
    assert all(math.isnan(v) for v in orderbook_features(None).values())
    one_sided = OrderBook(symbol="X/TMN", ts=datetime(2026, 1, 1, tzinfo=UTC), bids=[_lv(1, 1)], asks=[])
    assert math.isnan(orderbook_features(one_sided)["ob_spread_pct"])
