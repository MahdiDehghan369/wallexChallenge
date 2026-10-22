"""T-101: every parser works on the fixtures."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side, Timeframe
from siavash.exchange import endpoints as ep
from tests.unit.exchange.conftest import load

TS = datetime(2026, 11, 5, 9, 15, tzinfo=UTC)


def test_flags() -> None:
    assert ep.VERIFIED is False
    assert ep.BASE_URL.startswith("https://")


def test_parse_markets() -> None:
    markets = {m.symbol: m for m in ep.parse_markets(load("markets"))}
    btc = markets["BTC/TMN"]
    assert btc.exchange_symbol == "BTCTMN" and btc.base == "BTC" and btc.quote == "TMN"
    assert btc.price_tick == Decimal("1") and btc.qty_step == Decimal("0.000001")
    assert btc.min_notional == Decimal("100000") and btc.is_active and btc.is_spot
    assert markets["BTC/USDT"].price_tick == Decimal("0.01")
    assert markets["1BBABYDOGE/TMN"].qty_step == Decimal("1")
    assert markets["XRP/TMN"].is_active is False


def test_parse_tickers() -> None:
    t = ep.parse_tickers(load("markets"), TS)
    assert t["BTC/TMN"].last == Decimal("7350000000")
    assert t["BTC/TMN"].bid == Decimal("7349000000") and t["BTC/TMN"].ask == Decimal("7351000000")
    assert t["USDT/TMN"].volume_24h_quote == Decimal("300000000000")


def test_parse_candles() -> None:
    c = ep.parse_candles(load("candles"), "BTC/TMN", Timeframe.M15)
    assert len(c) == 3  # duplicate removed
    assert [x.ts_open for x in c] == sorted(x.ts_open for x in c)
    assert c[0].ts_open == datetime.fromtimestamp(1793000700, tz=UTC)
    assert c[-1].close == 7350000000.0 and c[0].volume == 1.25
    assert ep.parse_candles({"s": "no_data"}, "BTC/TMN", Timeframe.M15) == []


def test_parse_orderbook() -> None:
    ob = ep.parse_orderbook(load("depth"), "BTC/TMN", TS)
    assert ob.best_bid == Decimal("7349000000") and ob.best_ask == Decimal("7351000000")
    assert [b.price for b in ob.bids] == sorted((b.price for b in ob.bids), reverse=True)
    assert ob.spread_pct is not None and ob.spread_pct < 0.1


def test_parse_trades() -> None:
    tr = ep.parse_trades(load("trades"), "BTC/TMN")
    assert len(tr) == 3 and tr[0].ts < tr[-1].ts
    assert tr[-1].side is Side.BUY and tr[0].side is Side.BUY and tr[1].side is Side.SELL


def test_parse_balance() -> None:
    b = ep.parse_balance(load("balances"))
    assert b["TMN"].free == Decimal("4000000") and b["TMN"].locked == Decimal("1000000")
    assert b["TMN"].total == Decimal("5000000") and b["BTC"].free == Decimal("0.000150")


def test_parse_order() -> None:
    o = ep.parse_order(load("order"))
    assert o.order_id == "987654321" and o.symbol == "BTC/TMN"
    assert o.side is Side.BUY and o.type is OrderType.LIMIT and o.status is OrderStatus.PARTIALLY_FILLED
    assert o.filled_qty == Decimal("0.000050") and o.qty == Decimal("0.000136")
    opens = ep.parse_open_orders(load("open_orders"))
    assert len(opens) == 2 and opens[1].client_order_id == ""


def test_parse_fills() -> None:
    fills = ep.parse_fills(load("my_trades"))
    assert [f.fill_id for f in fills] == ["t-1001", "t-1002"]
    assert fills[0].fee_asset == "BTC" and fills[0].is_maker is True
    assert fills[0].client_order_id.startswith("SIAV-")


def test_real_public_samples() -> None:
    """Samples captured from the live public API on 2026-10-22 (tests/fixtures/wallex/real/)."""
    markets = {m.symbol: m for m in ep.parse_markets(load("real/markets"))}
    btc = markets["BTC/TMN"]
    assert btc.qty_step == Decimal("0.00000001") and btc.price_tick == Decimal("1")
    assert btc.min_notional > 0 and btc.min_qty > 0 and btc.is_active
    tickers = ep.parse_tickers(load("real/markets"), TS)
    assert tickers["BTC/TMN"].bid is not None and tickers["BTC/TMN"].volume_24h_quote > 0
    ob = ep.parse_orderbook(load("real/depth"), "BTC/TMN", TS)
    assert ob.best_bid is not None and ob.best_ask is not None and ob.best_bid < ob.best_ask
    tr = ep.parse_trades(load("real/trades"), "BTC/TMN")
    assert len(tr) == 5 and all(t.side is not None for t in tr)
    m1 = ep.parse_udf_bars(load("real/udf_res15_1m"))
    assert len(m1) == 60 and all((b[0].timestamp() % 60) == 0 for b in m1)
    c15 = ep.parse_candles(load("real/udf_res15_1m"), "BTC/TMN", Timeframe.M15)
    assert 1 <= len(c15) <= 60 and all(c.ts_open.minute % 15 == 0 for c in c15)
    h1 = ep.parse_candles(load("real/udf_res60_1h"), "BTC/TMN", Timeframe.H1)
    assert len(h1) == 12


def test_udf_errors() -> None:
    import pytest

    with pytest.raises(ep.UdfError, match="Invalid resolution"):
        ep.parse_udf_bars(load("real/udf_invalid_resolution"))
    assert ep.parse_udf_bars({"s": "no_data"}) == []


def test_parse_error() -> None:
    assert ep.parse_error(400, load("error_insufficient"))[0] == ep.ERR_INSUFFICIENT_BALANCE
    assert ep.parse_error(401, {})[0] == ep.ERR_AUTH
    assert ep.parse_error(429, None)[0] == ep.ERR_RATE_LIMITED
    assert ep.parse_error(503, "oops")[0] == ep.ERR_SERVER
    assert ep.parse_error(404, {})[0] == ep.ERR_ORDER_NOT_FOUND
    assert ep.parse_error(400, {"message": "bad"})[0] == ep.ERR_BAD_REQUEST


def test_request_builders() -> None:
    req = OrderRequest(
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("0.0001"),
        price=Decimal("7349000000"),
        client_order_id="SIAV-X-E1",
        decision_id="X",
        purpose="entry",
    )
    body = ep.order_body(req)
    assert body == {
        "symbol": "BTCTMN",
        "side": "buy",
        "type": "limit",
        "quantity": "0.0001",
        "client_id": "SIAV-X-E1",
        "price": "7349000000",
    }
    p = ep.candles_params("BTC/TMN", Timeframe.H1, TS, TS)
    assert p["resolution"] == "60" and p["symbol"] == "BTCTMN" and p["from"] == int(TS.timestamp())
