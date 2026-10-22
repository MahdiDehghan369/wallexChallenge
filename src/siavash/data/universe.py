"""Universe selection (T-204, section 4.1).

Tradable markets (SymbolGuard) are ranked by 24h quote volume; among the top ``top_volume_n`` a
market is selected if its spread is ≤ ``max_spread_pct`` and the book depth within
±``depth_band_pct`` is ≥ ``depth_multiple`` × our max position size. At most ``max_markets`` are
selected; markets with open positions are always kept. ``BTC/TMN``, ``USDT/TMN`` and the USDT pairs
of selected markets are tracked for data (features) even when not traded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, Protocol

from siavash.core.interfaces import Clock
from siavash.core.models import DecisionRecord, MarketInfo, OrderBook, Ticker
from siavash.core.types import DecisionType, Side


class _Guard(Protocol):
    def is_tradable(self, symbol: str, side: Side | None = None) -> tuple[bool, str]: ...


def depth_tmn_within(book: OrderBook, band_pct: float) -> Decimal:
    """Quote-currency value of bids and asks within ±``band_pct`` % of the mid."""
    mid = book.mid
    if mid is None:
        return Decimal(0)
    band = Decimal(str(band_pct)) / Decimal(100)
    lo, hi = mid * (1 - band), mid * (1 + band)
    bids = sum((lv.price * lv.qty for lv in book.bids if lv.price >= lo), Decimal(0))
    asks = sum((lv.price * lv.qty for lv in book.asks if lv.price <= hi), Decimal(0))
    return bids + asks


def max_position_tmn(cfg: Any, equity_tmn: Decimal | None = None) -> Decimal:
    """``max_position_pct`` % of equity (capital base if equity unknown)."""
    eq = equity_tmn if equity_tmn is not None else Decimal(cfg.risk.capital_base_tmn)
    return eq * Decimal(str(cfg.risk.max_position_pct)) / Decimal(100)


def select_universe(
    markets: Iterable[MarketInfo],
    tickers: Mapping[str, Ticker],
    books: Mapping[str, OrderBook],
    guard: _Guard,
    open_position_symbols: Iterable[str],
    cfg: Any,
    equity_tmn: Decimal | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return ``(selected, report)``; ``report[symbol]`` explains why each market was kept or dropped."""
    u = cfg.strategy.universe
    max_spread = float(cfg.risk.max_spread_pct)
    min_depth = max_position_tmn(cfg, equity_tmn) * Decimal(str(u.depth_multiple))
    report: dict[str, str] = {}
    tradable: list[MarketInfo] = []
    for m in markets:
        ok, reason = guard.is_tradable(m.symbol, Side.BUY)
        if not ok:
            report[m.symbol] = f"not tradable: {reason}"
        elif m.symbol not in tickers:
            report[m.symbol] = "no ticker"
        else:
            tradable.append(m)
    ranked = sorted(tradable, key=lambda m: tickers[m.symbol].volume_24h_quote, reverse=True)
    selected: list[str] = []
    for rank, m in enumerate(ranked, 1):
        vol = tickers[m.symbol].volume_24h_quote
        if rank > u.top_volume_n:
            report[m.symbol] = f"volume rank {rank} > {u.top_volume_n}"
            continue
        book = books.get(m.symbol)
        if book is None or book.spread_pct is None:
            report[m.symbol] = "no order book"
            continue
        if book.spread_pct > max_spread:
            report[m.symbol] = f"spread {book.spread_pct:.3f}% > {max_spread}%"
            continue
        depth = depth_tmn_within(book, u.depth_band_pct)
        if depth < min_depth:
            report[m.symbol] = f"depth {depth:.0f} < {min_depth:.0f} TMN within ±{u.depth_band_pct}%"
            continue
        if len(selected) >= u.max_markets:
            report[m.symbol] = f"max_markets={u.max_markets} reached"
            continue
        selected.append(m.symbol)
        report[m.symbol] = (
            f"selected: rank {rank}, vol24h={vol:.0f}, spread={book.spread_pct:.3f}%, depth={depth:.0f}"
        )
    for sym in open_position_symbols:
        if sym not in selected:
            selected.append(sym)
            report[sym] = "kept: open position"
    return selected, report


def data_symbols(selected: Iterable[str], cfg: Any, available: Iterable[str] | None = None) -> list[str]:
    """Symbols to track for features: selected + ``always_keep`` + USDT pairs of selected TMN markets."""
    avail = set(available) if available is not None else None
    out = list(dict.fromkeys([*selected, *cfg.strategy.universe.always_keep]))
    for sym in list(out):
        base, _, quote = sym.partition("/")
        if quote == "TMN" and base != "USDT":
            usdt = f"{base}/USDT"
            if usdt not in out and (avail is None or usdt in avail):
                out.append(usdt)
    return out


def universe_record(
    selected: list[str], report: dict[str, str], clock: Clock | None = None
) -> DecisionRecord:
    """``UNIVERSE_UPDATE`` decision record."""
    from siavash.engine.decision_log import build_record

    return build_record(
        DecisionType.UNIVERSE_UPDATE,
        clock=clock,
        final_reason=f"universe: {', '.join(selected) or '(empty)'}",
        outcome_action="INFO",
        extra={"selected": selected, "report": report},
    )
