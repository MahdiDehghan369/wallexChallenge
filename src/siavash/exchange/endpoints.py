"""Wallex REST endpoints, field names and response parsers (T-101).

⚠️ VERIFIED = False — built from appendix H of WALLEX_AGENT_PLAN.md (public, possibly stale info).
No official docs were present in ``docs/vendor/wallex/``. Parts that appendix H does not describe
(balances, order and user-trade payloads, error bodies) use a **MOCK SCHEMA** defined only here so
that the rest of the code can be built and tested against fixtures. When official docs arrive,
only this file, ``docs/wallex_api_notes.md`` and ``tests/fixtures/wallex/*.json`` must change.

This is the ONLY module that knows Wallex URLs, paths or field names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from siavash.core.models import (
    Balance,
    Candle,
    Fill,
    MarketInfo,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    PublicTrade,
    Ticker,
)
from siavash.core.symbols import to_canonical, to_exchange
from siavash.core.types import OrderStatus, OrderType, Side, Timeframe

VERIFIED = False

BASE_URL = "https://api.wallex.ir"
AUTH_HEADER = "X-API-Key"  # appendix H


class Paths:
    """REST paths (appendix H)."""

    MARKETS = "/v1/markets"
    DEPTH = "/v1/depth"
    TRADES = "/v1/trades"
    CANDLES = "/v1/udf/history"
    BALANCES = "/v1/account/balances"
    ORDERS = "/v1/account/orders"  # POST place, DELETE cancel
    ORDER = "/v1/account/orders/{order_id}"  # GET status
    OPEN_ORDERS = "/v1/account/openOrders"
    MY_TRADES = "/v1/account/trades"
    FEE = "/v1/account/fee"  # "احتمالی" in appendix H


PRIVATE_PATHS = frozenset(
    {Paths.BALANCES, Paths.ORDERS, Paths.ORDER, Paths.OPEN_ORDERS, Paths.MY_TRADES, Paths.FEE}
)

# TradingView-UDF resolutions — UNVERIFIED (allowed values and max range must be confirmed)
RESOLUTIONS: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M15: "15",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
}
CANDLES_MAX_BARS = 500  # UNVERIFIED conservative page size; client paginates
TRADES_MAX = 100  # UNVERIFIED

# client order id support — UNKNOWN (appendix H: body field "client_id" is "احتمالی")
SUPPORTS_CLIENT_ORDER_ID: bool | None = None
CLIENT_ID_MAX_LEN: int | None = None
# spot stop orders — UNKNOWN; stop-loss is enforced client side (section 4.5)
SUPPORTS_SPOT_STOP: bool | None = None

# ---------------------------------------------------------------------------- field names
# Public endpoints: names from appendix H. Fields marked MOCK are placeholders (not documented).
F: dict[str, str] = {
    # markets  (result.symbols{SYMBOL: {...}})
    "markets_root": "symbols",
    "base": "baseAsset",
    "quote": "quoteAsset",
    "stats": "stats",
    "stat_bid": "bidPrice",  # MOCK name (H: "bid")
    "stat_ask": "askPrice",  # MOCK name (H: "ask")
    "stat_last": "lastPrice",  # MOCK name (H: "last")
    "stat_vol_quote": "24h_quoteVolume",  # MOCK name (H: "24h volume")
    "min_qty": "minQty",  # H: "احتمالاً"
    "min_notional": "minNotional",  # H: "احتمالاً"
    "qty_precision": "stepSize",  # MOCK: number of decimals for quantity
    "price_precision": "tickSize",  # MOCK: number of decimals for price
    "is_active": "isActive",  # MOCK (missing -> treated as active)
    # depth (result.ask[] / result.bid[])
    "depth_ask": "ask",
    "depth_bid": "bid",
    "lvl_price": "price",
    "lvl_qty": "quantity",
    # trades (result.latestTrades[])
    "trades_root": "latestTrades",
    "trade_price": "price",
    "trade_qty": "quantity",
    "trade_is_buy": "isBuyOrder",
    "trade_ts": "timestamp",
    # candles (UDF)
    "udf_status": "s",
    "udf_t": "t",
    "udf_o": "o",
    "udf_h": "h",
    "udf_l": "l",
    "udf_c": "c",
    "udf_v": "v",
    # balances — MOCK SCHEMA: result.balances{ASSET: {asset, value, locked}}
    "balances_root": "balances",
    "bal_asset": "asset",
    "bal_total": "value",
    "bal_locked": "locked",
    # order — MOCK SCHEMA (request body names from appendix H)
    "req_symbol": "symbol",
    "req_side": "side",
    "req_type": "type",
    "req_price": "price",
    "req_qty": "quantity",
    "req_client_id": "client_id",
    "ord_id": "orderId",
    "ord_client_id": "clientOrderId",
    "ord_symbol": "symbol",
    "ord_side": "side",
    "ord_type": "type",
    "ord_price": "price",
    "ord_qty": "origQty",
    "ord_filled": "executedQty",
    "ord_avg_price": "executedPrice",
    "ord_status": "status",
    "ord_created": "created_at",
    "ord_updated": "updated_at",
    "open_orders_root": "orders",
    # user trades — MOCK SCHEMA: result.AccountLatestTrades[]
    "my_trades_root": "AccountLatestTrades",
    "fill_id": "id",
    "fill_order_id": "orderId",
    "fill_client_id": "clientOrderId",
    "fill_symbol": "symbol",
    "fill_side": "side",
    "fill_price": "price",
    "fill_qty": "quantity",
    "fill_fee": "fee",
    "fill_fee_asset": "feeAsset",
    "fill_ts": "timestamp",
    "fill_is_maker": "isMaker",
    # errors — MOCK SCHEMA: {"success": false, "code": int, "message": str}
    "err_code": "code",
    "err_message": "message",
}

# MOCK status strings -> OrderStatus
STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "CANCELLED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}

# Error kinds understood by siavash.exchange.errors (mapping is UNVERIFIED, mostly HTTP-status based)
ERR_AUTH = "auth"
ERR_RATE_LIMITED = "rate_limited"
ERR_INSUFFICIENT_BALANCE = "insufficient_balance"
ERR_INVALID_PRECISION = "invalid_precision"
ERR_BELOW_MINIMUM = "below_minimum"
ERR_MARKET_CLOSED = "market_closed"
ERR_ORDER_NOT_FOUND = "order_not_found"
ERR_SERVER = "server"
ERR_BAD_REQUEST = "bad_request"

_MESSAGE_HINTS: tuple[tuple[str, str], ...] = (  # MOCK keyword hints on the error message
    ("insufficient", ERR_INSUFFICIENT_BALANCE),
    ("balance", ERR_INSUFFICIENT_BALANCE),
    ("precision", ERR_INVALID_PRECISION),
    ("minimum", ERR_BELOW_MINIMUM),
    ("min notional", ERR_BELOW_MINIMUM),
    ("closed", ERR_MARKET_CLOSED),
    ("not found", ERR_ORDER_NOT_FOUND),
)


# ---------------------------------------------------------------------------- helpers


def _result(resp: Any) -> Any:
    if isinstance(resp, dict) and "result" in resp:
        return resp["result"]
    return resp


def _dec(v: Any, default: Decimal | None = None) -> Decimal:
    if v is None or v == "":
        if default is None:
            raise ValueError("missing decimal value")
        return default
    try:
        return Decimal(str(v))
    except InvalidOperation as e:
        raise ValueError(f"invalid decimal {v!r}") from e


def _opt_dec(v: Any) -> Decimal | None:
    return None if v is None or v == "" else _dec(v)


def _ts(v: Any) -> datetime:
    """ISO-8601 string or unix seconds/milliseconds -> aware UTC datetime."""
    if isinstance(v, datetime):
        return v.astimezone(UTC) if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, int | float) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
        x = float(v)
        if x > 1e12:
            x /= 1000.0
        return datetime.fromtimestamp(x, tz=UTC)
    if isinstance(v, str):
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    raise ValueError(f"invalid timestamp {v!r}")


def _step(decimals: Any, default: str) -> Decimal:
    if decimals is None or decimals == "":
        return Decimal(default)
    d = Decimal(str(decimals))
    if d >= 1 or d == 0:  # number of decimals
        return Decimal(1).scaleb(-int(d))
    return d  # already a step size


# ---------------------------------------------------------------------------- request builders


def candles_params(symbol: str, tf: Timeframe, start: datetime, end: datetime) -> dict[str, Any]:
    """Query params for ``Paths.CANDLES``."""
    return {
        "symbol": to_exchange(symbol),
        "resolution": RESOLUTIONS[tf],
        "from": int(start.timestamp()),
        "to": int(end.timestamp()),
    }


def symbol_params(symbol: str) -> dict[str, str]:
    """``{"symbol": "BTCTMN"}``."""
    return {"symbol": to_exchange(symbol)}


def order_body(req: OrderRequest) -> dict[str, Any]:
    """POST body for ``Paths.ORDERS`` (names from appendix H; ``client_id`` support unknown)."""
    body: dict[str, Any] = {
        F["req_symbol"]: to_exchange(req.symbol),
        F["req_side"]: req.side.value,
        F["req_type"]: req.type.value,
        F["req_qty"]: str(req.qty),
        F["req_client_id"]: req.client_order_id,
    }
    if req.price is not None:
        body[F["req_price"]] = str(req.price)
    return body


def cancel_body(order_id: str | None = None, client_order_id: str | None = None) -> dict[str, Any]:
    """DELETE body for ``Paths.ORDERS`` (MOCK field names)."""
    if order_id:
        return {F["ord_id"]: order_id}
    if client_order_id:
        return {F["ord_client_id"]: client_order_id}
    raise ValueError("order_id or client_order_id required")


# ---------------------------------------------------------------------------- parsers


def parse_market(exchange_symbol: str, raw: dict[str, Any]) -> MarketInfo:
    """One entry of ``result.symbols``."""
    base = str(raw.get(F["base"]) or "").upper()
    quote = str(raw.get(F["quote"]) or "").upper()
    symbol = f"{base}/{quote}" if base and quote else to_canonical(exchange_symbol)
    active = raw.get(F["is_active"])
    return MarketInfo(
        symbol=symbol,
        exchange_symbol=exchange_symbol,
        base=symbol.split("/")[0],
        quote=symbol.split("/")[1],
        price_tick=_step(raw.get(F["price_precision"]), "1"),
        qty_step=_step(raw.get(F["qty_precision"]), "0.000001"),
        min_qty=_dec(raw.get(F["min_qty"]), Decimal("0")),
        min_notional=_dec(raw.get(F["min_notional"]), Decimal("0")),
        is_active=True if active is None else bool(active),
        is_spot=True,
    )


def parse_markets(resp: Any) -> list[MarketInfo]:
    """``GET /v1/markets`` -> markets."""
    root = _result(resp)[F["markets_root"]]
    items = root.items() if isinstance(root, dict) else ((r.get("symbol", ""), r) for r in root)
    return [parse_market(k, v) for k, v in items]


def parse_ticker(exchange_symbol: str, raw: dict[str, Any], ts: datetime) -> Ticker:
    """Ticker from the ``stats`` block of a market entry."""
    st = raw.get(F["stats"]) or {}
    m = parse_market(exchange_symbol, raw)
    return Ticker(
        symbol=m.symbol,
        ts=ts,
        last=_dec(st.get(F["stat_last"])),
        bid=_opt_dec(st.get(F["stat_bid"])),
        ask=_opt_dec(st.get(F["stat_ask"])),
        volume_24h_quote=_dec(st.get(F["stat_vol_quote"]), Decimal("0")),
    )


def parse_tickers(resp: Any, ts: datetime) -> dict[str, Ticker]:
    """All tickers from ``GET /v1/markets``."""
    root = _result(resp)[F["markets_root"]]
    out: dict[str, Ticker] = {}
    for k, v in root.items():
        try:
            t = parse_ticker(k, v, ts)
        except ValueError:
            continue
        out[t.symbol] = t
    return out


def parse_candles(resp: Any, symbol: str, tf: Timeframe) -> list[Candle]:
    """UDF history -> candles (sorted, de-duplicated). ``s == "no_data"`` -> []."""
    r = _result(resp)
    if r.get(F["udf_status"]) not in (None, "ok"):
        return []
    t, o, h, lo, c, v = (r.get(F[k]) or [] for k in ("udf_t", "udf_o", "udf_h", "udf_l", "udf_c", "udf_v"))
    seen: dict[datetime, Candle] = {}
    for i in range(len(t)):
        ts = _ts(t[i])
        seen[ts] = Candle(
            symbol=symbol,
            timeframe=tf,
            ts_open=ts,
            open=float(o[i]),
            high=float(h[i]),
            low=float(lo[i]),
            close=float(c[i]),
            volume=float(v[i]) if i < len(v) else 0.0,
        )
    return [seen[k] for k in sorted(seen)]


def parse_orderbook(resp: Any, symbol: str, ts: datetime) -> OrderBook:
    """``GET /v1/depth`` -> book (bids descending, asks ascending)."""
    r = _result(resp)

    def levels(rows: list[dict[str, Any]]) -> list[OrderBookLevel]:
        return [OrderBookLevel(price=_dec(x[F["lvl_price"]]), qty=_dec(x[F["lvl_qty"]])) for x in rows or []]

    bids = sorted(levels(r.get(F["depth_bid"], [])), key=lambda lv: lv.price, reverse=True)
    asks = sorted(levels(r.get(F["depth_ask"], [])), key=lambda lv: lv.price)
    return OrderBook(symbol=symbol, ts=ts, bids=bids, asks=asks)


def parse_trades(resp: Any, symbol: str) -> list[PublicTrade]:
    """``GET /v1/trades`` -> public trades (oldest first)."""
    rows = _result(resp).get(F["trades_root"], [])
    out = []
    for x in rows:
        is_buy = x.get(F["trade_is_buy"])
        out.append(
            PublicTrade(
                symbol=symbol,
                ts=_ts(x[F["trade_ts"]]),
                price=_dec(x[F["trade_price"]]),
                qty=_dec(x[F["trade_qty"]]),
                side=None if is_buy is None else (Side.BUY if is_buy else Side.SELL),
            )
        )
    return sorted(out, key=lambda tr: tr.ts)


def parse_balance(resp: Any) -> dict[str, Balance]:
    """MOCK SCHEMA balances -> ``{asset: Balance}`` with ``free = total − locked``."""
    root = _result(resp)[F["balances_root"]]
    items = root.values() if isinstance(root, dict) else root
    out: dict[str, Balance] = {}
    for x in items:
        asset = str(x[F["bal_asset"]]).upper()
        total = _dec(x.get(F["bal_total"]), Decimal("0"))
        locked = _dec(x.get(F["bal_locked"]), Decimal("0"))
        out[asset] = Balance(asset=asset, free=total - locked, locked=locked)
    return out


def parse_order(raw: Any, fallback_client_order_id: str | None = None) -> Order:
    """MOCK SCHEMA order -> ``Order``."""
    x = _result(raw)
    status_raw = str(x.get(F["ord_status"], "NEW")).upper()
    created = _ts(x[F["ord_created"]]) if x.get(F["ord_created"]) else datetime.now(UTC)
    updated = _ts(x[F["ord_updated"]]) if x.get(F["ord_updated"]) else created
    order_id = x.get(F["ord_id"])
    return Order(
        order_id=None if order_id is None else str(order_id),
        client_order_id=str(x.get(F["ord_client_id"]) or fallback_client_order_id or ""),
        symbol=to_canonical(str(x[F["ord_symbol"]])),
        side=Side(str(x[F["ord_side"]]).lower()),
        type=OrderType(str(x[F["ord_type"]]).lower()),
        price=_opt_dec(x.get(F["ord_price"])),
        qty=_dec(x[F["ord_qty"]]),
        filled_qty=_dec(x.get(F["ord_filled"]), Decimal("0")),
        avg_fill_price=_opt_dec(x.get(F["ord_avg_price"])) or None,
        status=STATUS_MAP.get(status_raw, OrderStatus.NEW),
        created_at=created,
        updated_at=updated,
    )


def parse_open_orders(resp: Any) -> list[Order]:
    """MOCK SCHEMA open orders list."""
    r = _result(resp)
    rows = r.get(F["open_orders_root"], []) if isinstance(r, dict) else r
    return [parse_order(x) for x in rows]


def parse_fill(raw: dict[str, Any]) -> Fill:
    """MOCK SCHEMA user trade -> ``Fill``."""
    symbol = to_canonical(str(raw[F["fill_symbol"]]))
    fee_asset = raw.get(F["fill_fee_asset"]) or symbol.split("/")[1]
    order_id = raw.get(F["fill_order_id"])
    is_maker = raw.get(F["fill_is_maker"])
    return Fill(
        fill_id=str(raw[F["fill_id"]]),
        order_id=None if order_id is None else str(order_id),
        client_order_id=str(raw.get(F["fill_client_id"]) or ""),
        symbol=symbol,
        side=Side(str(raw[F["fill_side"]]).lower()),
        price=_dec(raw[F["fill_price"]]),
        qty=_dec(raw[F["fill_qty"]]),
        fee=_dec(raw.get(F["fill_fee"]), Decimal("0")),
        fee_asset=str(fee_asset).upper(),
        ts=_ts(raw[F["fill_ts"]]),
        is_maker=None if is_maker is None else bool(is_maker),
    )


def parse_fills(resp: Any) -> list[Fill]:
    """MOCK SCHEMA user trades list."""
    rows = _result(resp).get(F["my_trades_root"], [])
    return sorted((parse_fill(x) for x in rows), key=lambda f: f.ts)


def parse_error(status: int, body: Any) -> tuple[str, str]:
    """Map an HTTP error to ``(kind, message)``; ``kind`` is one of the ``ERR_*`` constants."""
    message = ""
    if isinstance(body, dict):
        message = str(body.get(F["err_message"]) or body.get("error") or "")
    elif body is not None:
        message = str(body)
    low = message.lower()
    if status in (401, 403):
        return ERR_AUTH, message
    if status == 429:
        return ERR_RATE_LIMITED, message
    if status >= 500:
        return ERR_SERVER, message
    for hint, kind in _MESSAGE_HINTS:
        if hint in low:
            return kind, message
    if status == 404:
        return ERR_ORDER_NOT_FOUND, message
    return ERR_BAD_REQUEST, message
