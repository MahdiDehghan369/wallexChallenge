"""Order-book features (T-302).

Live only: there is no historical order book, so in backtests these are NaN and the ML model is
trained without them (``OB_FEATURES`` are excluded from model inputs but still logged live).
"""

from __future__ import annotations

import math

from siavash.core.models import OrderBook
from siavash.data.universe import depth_tmn_within

OB_FEATURES: tuple[str, ...] = (
    "ob_spread_pct",
    "ob_imbalance_1",
    "ob_imbalance_5",
    "ob_imbalance_10",
    "ob_depth_tmn_05",
    "ob_microprice_dist_pct",
)


def _imbalance(book: OrderBook, levels: int) -> float:
    bq = float(sum(lv.qty for lv in book.bids[:levels]))
    aq = float(sum(lv.qty for lv in book.asks[:levels]))
    return (bq - aq) / (bq + aq) if bq + aq > 0 else math.nan


def empty_orderbook_features() -> dict[str, float]:
    return dict.fromkeys(OB_FEATURES, math.nan)


def orderbook_features(book: OrderBook | None, depth_band_pct: float = 0.5) -> dict[str, float]:
    """Spread %, level 1/5/10 imbalance ``(bid_qty − ask_qty)/(bid_qty + ask_qty)``, depth (quote) within
    ±``depth_band_pct`` %, and microprice distance from mid in %. Missing/one-sided book → NaN."""
    if book is None or not book.bids or not book.asks or book.mid is None or book.mid <= 0:
        return empty_orderbook_features()
    bid, ask = book.bids[0], book.asks[0]
    mid = float(book.mid)
    tot = float(bid.qty + ask.qty)
    micro = (float(bid.price) * float(ask.qty) + float(ask.price) * float(bid.qty)) / tot if tot > 0 else mid
    return {
        "ob_spread_pct": float(book.spread_pct) if book.spread_pct is not None else math.nan,
        "ob_imbalance_1": _imbalance(book, 1),
        "ob_imbalance_5": _imbalance(book, 5),
        "ob_imbalance_10": _imbalance(book, 10),
        "ob_depth_tmn_05": float(depth_tmn_within(book, depth_band_pct)),
        "ob_microprice_dist_pct": (micro / mid - 1.0) * 100.0,
    }
