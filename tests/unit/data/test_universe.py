"""T-204: universe selection."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.config import AppConfig, load_config
from siavash.core.models import MarketInfo, OrderBook, OrderBookLevel, Ticker
from siavash.core.types import DecisionType
from siavash.data.universe import data_symbols, depth_tmn_within, select_universe, universe_record
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry

ROOT = Path(__file__).resolve().parents[3]
TS = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(ROOT / "config", env_file=None)


def _mi(sym: str) -> MarketInfo:
    b, q = sym.split("/")
    one = Decimal("1")
    return MarketInfo(
        symbol=sym,
        exchange_symbol=b + q,
        base=b,
        quote=q,
        price_tick=one,
        qty_step=Decimal("0.0001"),
        min_qty=one,
        min_notional=one,
        is_active=True,
    )


def _book(sym: str, mid: float, spread_pct: float, depth_tmn: float) -> OrderBook:
    half = mid * spread_pct / 200
    qty = Decimal(str(depth_tmn / 2 / mid))
    return OrderBook(
        symbol=sym,
        ts=TS,
        bids=[OrderBookLevel(price=Decimal(str(mid - half)), qty=qty)],
        asks=[OrderBookLevel(price=Decimal(str(mid + half)), qty=qty)],
    )


def _ticker(sym: str, vol: float) -> Ticker:
    return Ticker(symbol=sym, ts=TS, last=Decimal(1), bid=None, ask=None, volume_24h_quote=Decimal(str(vol)))


def test_select(cfg: AppConfig) -> None:
    # need depth >= 5 × 20 % × 5M = 5M TMN within ±0.5 %
    specs = {
        "BTC/TMN": (9e10, 0.05, 2e7),
        "ETH/TMN": (8e10, 0.1, 1e7),
        "SOL/TMN": (7e10, 0.5, 1e7),  # spread too wide
        "XRP/TMN": (6e10, 0.1, 1e6),  # too shallow
        "TON/TMN": (9.5e10, 0.05, 1e8),  # denylisted
        "DOGE/TMN": (5e10, 0.2, 6e6),
        "BTC/USDT": (9.9e10, 0.01, 1e8),  # USDT quote not tradable
    }
    markets = [_mi(s) for s in [*specs, "ADA/TMN", "USDT/TMN"]]
    reg = MarketRegistry.from_markets(markets)
    guard = SymbolGuard(ROOT / "config" / "denylist.txt", reg)
    tickers = {s: _ticker(s, v[0]) for s, v in specs.items()}
    tickers["USDT/TMN"] = _ticker("USDT/TMN", 1e12)
    books = {s: _book(s, 1000.0, v[1], v[2]) for s, v in specs.items()}
    selected, report = select_universe(markets, tickers, books, guard, ["LTC/TMN"], cfg)
    assert selected == ["BTC/TMN", "ETH/TMN", "DOGE/TMN", "LTC/TMN"]
    assert "spread" in report["SOL/TMN"] and "depth" in report["XRP/TMN"]
    assert "denylist" in report["TON/TMN"] and "not tradable" in report["BTC/USDT"]
    assert report["ADA/TMN"] == "no ticker" and "data-only" in report["USDT/TMN"]
    assert report["LTC/TMN"] == "kept: open position"


def test_max_markets_and_top_volume(cfg: AppConfig) -> None:
    syms = [f"C{i}/TMN" for i in range(15)]
    markets = [_mi(s) for s in syms]
    guard = SymbolGuard(ROOT / "config" / "denylist.txt", MarketRegistry.from_markets(markets))
    tickers = {s: _ticker(s, 1e9 * (20 - i)) for i, s in enumerate(syms)}
    books = {s: _book(s, 100.0, 0.05, 1e8) for s in syms}
    selected, report = select_universe(markets, tickers, books, guard, [], cfg)
    assert selected == syms[:8]
    assert "max_markets" in report[syms[8]] and "volume rank 11" in report[syms[10]]


def test_depth_and_data_symbols(cfg: AppConfig) -> None:
    b = _book("X/TMN", 1000.0, 0.1, 2e6)
    assert depth_tmn_within(b, 0.5) == pytest.approx(Decimal("2e6"), rel=Decimal("1e-6"))
    assert depth_tmn_within(b, 0.01) == 0
    ds = data_symbols(["ETH/TMN"], cfg)
    assert ds[:3] == ["ETH/TMN", "BTC/TMN", "USDT/TMN"] and "ETH/USDT" in ds and "BTC/USDT" in ds
    assert "ETH/USDT" not in data_symbols(["ETH/TMN"], cfg, available=["BTC/USDT"])


def test_universe_record() -> None:
    r = universe_record(["BTC/TMN"], {"BTC/TMN": "selected"})
    assert r.type is DecisionType.UNIVERSE_UPDATE and r.extra["selected"] == ["BTC/TMN"]
