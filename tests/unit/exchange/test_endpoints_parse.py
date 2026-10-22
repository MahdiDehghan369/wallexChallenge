"""T-101: every parser works on the fixtures (Swagger schemas) and on real public samples."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from siavash.core.models import OrderRequest
from siavash.core.types import OrderStatus, OrderType, Side, Timeframe
from siavash.exchange import endpoints as ep
from tests.unit.exchange.conftest import load

TS = datetime(2026, 11, 5, 9, 15, tzinfo=UTC)
COID = "SIAV-01JBXYZABCDEFGHJKMNPQRSTVW-E1"


def test_flags() -> None:
    assert ep.VERIFIED is True
    assert ep.BASE_URL.startswith("https://")
    assert ep.AUTH_HEADER == "x-api-key"
    assert ep.SUPPORTS_SPOT_STOP is False


def test_parse_markets() -> None:
    markets = {m.symbol: m for m in ep.parse_markets(load("markets"))}
    btc = markets["BTC/TMN"]
    assert btc.exchange_symbol == "BTCTMN" and btc.base == "BTC" and btc.quote == "TMN"
    assert btc.price_tick == Decimal("1") and btc.qty_step == Decimal("0.000001")
    assert btc.min_qty == btc.qty_step
    assert btc.min_notional == Decimal("100000") and btc.is_active and btc.is_spot
    assert markets["BTC/USDT"].price_tick == Decimal("0.01")
    assert markets["1BBABYDOGE/TMN"].qty_step == Decimal("1")
    assert markets["XRP/TMN"].is_active is False and markets["XRP/TMN"].is_spot is False


def test_min_notional_from_config() -> None:
    markets = {m.symbol: m for m in ep.parse_markets(load("markets"), {"TMN": Decimal("250000")})}
    assert markets["BTC/TMN"].min_notional == Decimal("250000")
    assert markets["BTC/USDT"].min_notional == Decimal("0")  # quote missing from the mapping


def test_parse_tickers() -> None:
    t = ep.parse_tickers(load("markets"), TS)
    assert t["BTC/TMN"].last == Decimal("7350000000")
    assert t["BTC/TMN"].bid is None and t["BTC/TMN"].ask is None  # top-of-book comes from /v1/depth
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
    books = ep.parse_all_orderbooks({"result": {"BTCTMN": load("depth")["result"]}, "success": True}, TS)
    assert books["BTC/TMN"].best_bid == Decimal("7349000000")


def test_parse_trades() -> None:
    tr = ep.parse_trades(load("trades"), "BTC/TMN")
    assert len(tr) == 3 and tr[0].ts < tr[-1].ts
    assert tr[-1].side is Side.BUY and tr[0].side is Side.BUY and tr[1].side is Side.SELL


def test_parse_balance() -> None:
    b = ep.parse_balance(load("balances"))
    assert b["TMN"].free == Decimal("4000000") and b["TMN"].locked == Decimal("1000000")
    assert b["TMN"].total == Decimal("5000000") and b["BTC"].free == Decimal("0.000150")


def test_parse_fees() -> None:
    fees = ep.parse_fees(load("fee"))
    assert fees["BTC/TMN"] == (Decimal("0.2"), Decimal("0.2"))
    assert "BTC/USDT" in fees


def test_parse_order() -> None:
    o = ep.parse_order(load("order"))
    assert o.order_id == COID and o.client_order_id == COID and o.symbol == "BTC/TMN"
    assert o.side is Side.BUY and o.type is OrderType.LIMIT and o.status is OrderStatus.PARTIALLY_FILLED
    assert o.filled_qty == Decimal("0.000050") and o.qty == Decimal("0.000136")
    assert o.avg_fill_price == Decimal("7349000000")
    opens = ep.parse_open_orders(load("open_orders"))
    assert len(opens) == 2 and opens[1].client_order_id == "manual-555"


@pytest.mark.parametrize(
    ("status", "active", "executed", "expected"),
    [
        ("WEIRD", True, "0", OrderStatus.NEW),
        ("WEIRD", True, "0.5", OrderStatus.PARTIALLY_FILLED),
        ("WEIRD", False, "1", OrderStatus.FILLED),
        ("WEIRD", False, "0.5", OrderStatus.CANCELED),
        ("canceled", False, "0", OrderStatus.CANCELED),
    ],
)
def test_unknown_status_derived_from_active(
    status: str, active: bool, executed: str, expected: OrderStatus
) -> None:
    raw = {
        "clientOrderId": "c",
        "symbol": "BTCTMN",
        "type": "MARKET",
        "side": "SELL",
        "origQty": "1",
        "executedQty": executed,
        "status": status,
        "active": active,
    }
    assert ep.parse_order(raw).status is expected


def test_parse_fills_from_orders() -> None:
    fills = ep.parse_fills(load("orders_history"))
    assert len(fills) == 2 and fills[0].ts < fills[1].ts  # 2nd order has no fills[]
    assert fills[0].fee_asset == "BTC" and fills[0].client_order_id == COID and fills[0].side is Side.BUY
    assert fills[0].fill_id == ep.fill_id(COID, "2026-11-05T09:15:30Z", "7349000000", "0.000050")
    assert len({f.fill_id for f in fills}) == 2
    assert ep.parse_order_fills(load("order"))[0].fill_id == fills[0].fill_id  # same fill -> same id


def test_real_public_samples() -> None:
    """Samples captured from the live public API (tests/fixtures/wallex/real/)."""
    markets = {m.symbol: m for m in ep.parse_markets(load("real/markets"))}
    btc = markets["BTC/TMN"]
    assert btc.qty_step == Decimal("0.00000001") and btc.price_tick == Decimal("1")
    assert btc.min_notional > 0 and btc.min_qty > 0 and btc.is_active and btc.is_spot
    assert any(not m.is_spot for m in markets.values())
    tickers = ep.parse_tickers(load("real/markets"), TS)
    assert tickers["BTC/TMN"].last > 0 and tickers["BTC/TMN"].volume_24h_quote > 0
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
    with pytest.raises(ep.UdfError, match="Invalid resolution"):
        ep.parse_udf_bars(load("real/udf_invalid_resolution"))
    assert ep.parse_udf_bars({"s": "no_data"}) == []


def test_parse_error() -> None:
    kind, msg = ep.parse_error(422, load("error_insufficient"))
    assert kind == ep.ERR_INSUFFICIENT_BALANCE and "Insufficient balance" in msg
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
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "0.0001",
        "client_id": "SIAV-X-E1",
        "price": "7349000000",
    }
    assert all(isinstance(v, str) for v in body.values())
    assert ep.order_path("SIAV-X-E1") == "/v1/account/orders/SIAV-X-E1"
    p = ep.candles_params("BTC/TMN", Timeframe.H1, TS, TS)
    assert p["resolution"] == "60" and p["symbol"] == "BTCTMN" and p["from"] == int(TS.timestamp())
    q = ep.orders_history_params(TS, 2, "BTC/TMN")
    assert q == {"from": "2026-11-05", "page": 2, "per_page": 100, "market": "BTCTMN"}


def test_no_forbidden_account_paths() -> None:
    paths = [v for k, v in vars(ep.Paths).items() if not k.startswith("_")]
    for bad in ("wallet", "iban", "withdraw", "card"):
        assert not any(bad in p for p in paths)
