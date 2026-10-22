"""Wallex REST endpoints, field names and response parsers (T-101).

✅ VERIFIED = True — paths, request bodies and response schemas come from the official Swagger files in
``docs/vendor/wallex/`` (``wallex-spot-services.swagger.json``, ``wallex-basic-services.swagger.json``).
Public endpoints were also OBSERVED with read-only requests on 2026-10-22 (samples in
``tests/fixtures/wallex/real/``). Where Swagger is silent, the item is marked UNKNOWN below and is
resolved by the human smoke test (T-108); the code then uses a conservative default.

This is the ONLY module that knows Wallex URLs, paths or field names.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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

VERIFIED = True

# Swagger host is api.wallex.com, the MCP server is on api.wallex.ir; the public API answered on
# api.wallex.ir (2026-10-22). Override with env WALLEX_BASE_URL; T-108 picks the final one.
BASE_URL = "https://api.wallex.ir"
AUTH_HEADER = "x-api-key"  # Swagger securityDefinitions.ApiKeyAuth


class Paths:
    """REST paths (Swagger). Only spot trading + read-only account endpoints are defined here."""

    MARKETS = "/hector/web/v1/markets"
    DEPTH = "/v1/depth"
    DEPTH_ALL = "/depth/all"
    TRADES = "/v1/trades"
    CANDLES = "/v1/udf/history"
    BALANCES = "/v1/account/balances"
    FEE = "/v1/account/fee"
    ORDERS = "/v1/account/orders"  # POST place, GET paginated history
    ORDER = "/v1/account/orders/{client_id}"  # GET status, DELETE cancel
    OPEN_ORDERS = "/v1/account/openOrders"


PRIVATE_PATHS = frozenset({Paths.BALANCES, Paths.FEE, Paths.ORDERS, Paths.ORDER, Paths.OPEN_ORDERS})

# UDF resolutions — Swagger says "e.g. 1, 5, 15, 60, D", but OBSERVED 2026-10-22:
#   "15" -> 1-minute bars (sparse: minutes without trades are missing), history >= 120 days
#   "60", "240", "720" -> 1-hour bars;  "1D" -> daily;  "1" -> no_data;
#   "5", "30", "120", "180", "360", "1W" -> {"s": "error", "errmsg": "Invalid resolution!"}
#   a "60" request returned at most ~700 bars (60-day range -> 696 bars).
# So 15m candles are aggregated from 1m bars and 4h candles from 1h bars.


class CandleSource:
    """How to obtain one timeframe: request ``resolution`` (bars of ``source_seconds``) and aggregate."""

    def __init__(self, resolution: str, source_seconds: int, max_bars: int) -> None:
        self.resolution = resolution
        self.source_seconds = source_seconds
        self.max_bars = max_bars  # page size used by the client (bars of the source resolution)


CANDLE_SOURCES: dict[Timeframe, CandleSource] = {
    Timeframe.M1: CandleSource("15", 60, 4320),
    Timeframe.M15: CandleSource("15", 60, 4320),
    Timeframe.H1: CandleSource("60", 3600, 600),
    Timeframe.H4: CandleSource("60", 3600, 600),
}
RESOLUTIONS: dict[Timeframe, str] = {tf: s.resolution for tf, s in CANDLE_SOURCES.items()}

ORDERS_PER_PAGE = 100  # Swagger: per_page max 100
ORDERS_MAX_PAGES = 50  # safety stop for history paging

# Orders are identified by the client id we send (``client_id``); Swagger has no exchange order id.
SUPPORTS_CLIENT_ORDER_ID = True
CLIENT_ID_MAX_LEN: int | None = None  # UNKNOWN (T-108)
# Swagger defines STOP_LIMIT / STOP_MARKET, but the bot only uses LIMIT and MARKET;
# stop-loss stays client side (section 4.5).
SUPPORTS_SPOT_STOP = False

# ---------------------------------------------------------------------------- field names
F: dict[str, str] = {
    # markets  (result.markets[])
    "markets_root": "markets",
    "mkt_symbol": "symbol",
    "base": "base_asset",
    "quote": "quote_asset",
    "price_precision": "price_precision",  # number of decimals -> tick = 10^-n
    "qty_precision": "amount_precision",  # number of decimals -> step = 10^-n
    "is_spot": "is_spot",
    "mkt_price": "price",
    "mkt_vol_quote": "quote_volume_24h",
    # depth (result.ask[] / result.bid[]);  depth/all: result{SYMBOL: {ask, bid}}
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
    # balances: result.balances{ASSET: {asset, value (total), locked}}
    "balances_root": "balances",
    "bal_asset": "asset",
    "bal_total": "value",
    "bal_locked": "locked",
    # fee: result{SYMBOL: {makerFeeRate, takerFeeRate}} (fractions, 0.002 = 0.2 %)
    "fee_maker": "makerFeeRate",
    "fee_taker": "takerFeeRate",
    # order request (requests.CreateOrderRequest; all values are strings)
    "req_symbol": "symbol",
    "req_side": "side",
    "req_type": "type",
    "req_price": "price",
    "req_qty": "quantity",
    "req_client_id": "client_id",
    # order (resources.OrderResource)
    "ord_client_id": "clientOrderId",
    "ord_symbol": "symbol",
    "ord_side": "side",
    "ord_type": "type",
    "ord_price": "price",
    "ord_qty": "origQty",
    "ord_filled": "executedQty",
    "ord_avg_price": "executedPrice",
    "ord_status": "status",
    "ord_active": "active",
    "ord_created": "created_at",
    "ord_updated": "updated_at",
    "ord_transact_time": "transactTime",
    "ord_fills": "fills",
    # fill (resources.TradeResource inside OrderResource.fills[])
    "fill_price": "price",
    "fill_qty": "quantity",
    "fill_fee": "fee",
    "fill_fee_asset": "feeAsset",
    "fill_is_buyer": "isBuyer",
    "fill_ts": "timestamp",
    # order history query (GET /v1/account/orders)
    "q_market": "market",
    "q_page": "page",
    "q_per_page": "per_page",
    "q_from": "from",
    "q_to": "to",
    # errors (resources.ErrorResponse): {success, code, message, result{field: [messages]}}
    "err_code": "code",
    "err_message": "message",
}

# Request enums: Swagger lists BUY/SELL and LIMIT/MARKET (upper case) for the order filters;
# the case accepted in the POST body is UNKNOWN until T-108.
SIDE_VALUES: dict[Side, str] = {Side.BUY: "BUY", Side.SELL: "SELL"}
TYPE_VALUES: dict[OrderType, str] = {OrderType.LIMIT: "LIMIT", OrderType.MARKET: "MARKET"}

# Status strings are UNKNOWN (Swagger: "status: string"); common spellings are mapped and anything
# else is derived from ``active`` + executed quantity (see ``_status``).
STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.NEW,
    "OPEN": OrderStatus.NEW,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "FILLED": OrderStatus.FILLED,
    "DONE": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "CANCELLED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}

# Error kinds understood by siavash.exchange.errors (codes are UNKNOWN: mapping by HTTP status + text)
ERR_AUTH = "auth"
ERR_RATE_LIMITED = "rate_limited"
ERR_INSUFFICIENT_BALANCE = "insufficient_balance"
ERR_INVALID_PRECISION = "invalid_precision"
ERR_BELOW_MINIMUM = "below_minimum"
ERR_MARKET_CLOSED = "market_closed"
ERR_ORDER_NOT_FOUND = "order_not_found"
ERR_SERVER = "server"
ERR_BAD_REQUEST = "bad_request"

_MESSAGE_HINTS: tuple[tuple[str, str], ...] = (  # keyword hints on the error message (UNVERIFIED)
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
    """Number of decimals -> step (``8`` -> ``0.00000001``)."""
    if decimals is None or decimals == "":
        return Decimal(default)
    return Decimal(1).scaleb(-int(decimals))


def _rows(result: Any) -> list[dict[str, Any]]:
    """Order lists: Swagger types ``result`` as ``{key: [OrderResource]}`` (key UNKNOWN) or a list."""
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    if isinstance(result, dict):
        if F["ord_symbol"] in result:  # a single order
            return [result]
        out: list[dict[str, Any]] = []
        for v in result.values():
            if isinstance(v, list):
                out.extend(x for x in v if isinstance(x, dict))
        return out
    return []


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


def order_path(client_order_id: str) -> str:
    """``/v1/account/orders/{client_id}`` (GET status, DELETE cancel)."""
    return Paths.ORDER.format(client_id=client_order_id)


def order_body(req: OrderRequest) -> dict[str, Any]:
    """POST body for ``Paths.ORDERS`` (``requests.CreateOrderRequest``: every value is a string)."""
    body: dict[str, Any] = {
        F["req_symbol"]: to_exchange(req.symbol),
        F["req_side"]: SIDE_VALUES[req.side],
        F["req_type"]: TYPE_VALUES[req.type],
        F["req_qty"]: str(req.qty),
        F["req_client_id"]: req.client_order_id,
    }
    if req.price is not None:
        body[F["req_price"]] = str(req.price)
    return body


def orders_history_params(
    since: datetime, page: int, symbol: str | None = None, until: datetime | None = None
) -> dict[str, Any]:
    """Query for ``GET Paths.ORDERS``; ``from``/``to`` are dates (``YYYY-MM-DD``, inclusive)."""
    p: dict[str, Any] = {
        F["q_from"]: since.astimezone(UTC).date().isoformat(),
        F["q_page"]: page,
        F["q_per_page"]: ORDERS_PER_PAGE,
    }
    if until is not None:
        p[F["q_to"]] = until.astimezone(UTC).date().isoformat()
    if symbol:
        p[F["q_market"]] = to_exchange(symbol)
    return p


# ---------------------------------------------------------------------------- parsers


def parse_market(raw: dict[str, Any], min_notional: Mapping[str, Decimal] | None = None) -> MarketInfo:
    """One entry of ``result.markets``. Swagger has no spot minimums: ``min_qty`` = one quantity step,
    ``min_notional`` from ``min_notional[quote]`` (config ``exchange.min_notional``)."""
    exchange_symbol = str(raw.get(F["mkt_symbol"]) or "")
    base = str(raw.get(F["base"]) or "").upper()
    quote = str(raw.get(F["quote"]) or "").upper()
    symbol = f"{base}/{quote}" if base and quote else to_canonical(exchange_symbol)
    base, quote = symbol.split("/")
    qty_step = _step(raw.get(F["qty_precision"]), "0.000001")
    spot = bool(raw.get(F["is_spot"], True))
    mins = DEFAULT_MIN_NOTIONAL if min_notional is None else min_notional
    return MarketInfo(
        symbol=symbol,
        exchange_symbol=exchange_symbol or to_exchange(symbol),
        base=base,
        quote=quote,
        price_tick=_step(raw.get(F["price_precision"]), "1"),
        qty_step=qty_step,
        min_qty=qty_step,
        min_notional=Decimal(str(mins.get(quote, 0))),
        is_active=spot,
        is_spot=spot,
    )


# Used only when no config is passed (tests, scripts); runtime passes ``cfg.base.exchange.min_notional``.
DEFAULT_MIN_NOTIONAL: dict[str, Decimal] = {"TMN": Decimal("100000"), "USDT": Decimal("5")}


def parse_markets(resp: Any, min_notional: Mapping[str, Decimal] | None = None) -> list[MarketInfo]:
    """``GET /hector/web/v1/markets`` -> markets (non-spot entries come back with ``is_spot=False``)."""
    return [parse_market(r, min_notional) for r in _result(resp)[F["markets_root"]]]


def parse_tickers(resp: Any, ts: datetime) -> dict[str, Ticker]:
    """Last price and 24h quote volume for every market (no top-of-book in this endpoint: bid/ask None)."""
    out: dict[str, Ticker] = {}
    for r in _result(resp)[F["markets_root"]]:
        try:
            m = parse_market(r)
            out[m.symbol] = Ticker(
                symbol=m.symbol,
                ts=ts,
                last=_dec(r.get(F["mkt_price"])),
                bid=None,
                ask=None,
                volume_24h_quote=_dec(r.get(F["mkt_vol_quote"]), Decimal("0")),
            )
        except (ValueError, KeyError):
            continue
    return out


class UdfError(ValueError):
    """``{"s": "error", "errmsg": ...}`` from the UDF endpoint (e.g. invalid resolution)."""


def parse_udf_bars(resp: Any) -> list[tuple[datetime, float, float, float, float, float]]:
    """UDF history -> ``[(ts_open, o, h, l, c, v)]`` sorted and de-duplicated; ``no_data`` -> []."""
    r = _result(resp)
    status = r.get(F["udf_status"])
    if status == "no_data":
        return []
    if status not in (None, "ok"):
        raise UdfError(str(r.get("errmsg") or status))
    t, o, h, lo, c, v = (r.get(F[k]) or [] for k in ("udf_t", "udf_o", "udf_h", "udf_l", "udf_c", "udf_v"))
    seen: dict[datetime, tuple[datetime, float, float, float, float, float]] = {}
    for i in range(len(t)):
        ts = _ts(t[i])
        vol = float(v[i]) if i < len(v) else 0.0
        seen[ts] = (ts, float(o[i]), float(h[i]), float(lo[i]), float(c[i]), vol)
    return [seen[k] for k in sorted(seen)]


def aggregate_bars(
    bars: list[tuple[datetime, float, float, float, float, float]], symbol: str, tf: Timeframe
) -> list[Candle]:
    """Bucket source bars into ``tf`` candles (bucket = floor(ts, tf)); empty buckets are skipped (gaps)."""
    out: dict[int, list[float]] = {}
    for ts, o, h, lo, c, v in bars:
        key = int(ts.timestamp()) // tf.seconds * tf.seconds
        b = out.get(key)
        if b is None:
            out[key] = [o, h, lo, c, v]
        else:
            b[1] = max(b[1], h)
            b[2] = min(b[2], lo)
            b[3] = c
            b[4] += v
    return [
        Candle(
            symbol=symbol,
            timeframe=tf,
            ts_open=datetime.fromtimestamp(k, tz=UTC),
            open=b[0],
            high=b[1],
            low=b[2],
            close=b[3],
            volume=b[4],
        )
        for k, b in sorted(out.items())
    ]


def parse_candles(resp: Any, symbol: str, tf: Timeframe) -> list[Candle]:
    """UDF history for ``CANDLE_SOURCES[tf]`` -> ``tf`` candles (aggregated when the source is finer)."""
    return aggregate_bars(parse_udf_bars(resp), symbol, tf)


def _book(r: dict[str, Any], symbol: str, ts: datetime) -> OrderBook:
    def levels(rows: list[dict[str, Any]]) -> list[OrderBookLevel]:
        return [OrderBookLevel(price=_dec(x[F["lvl_price"]]), qty=_dec(x[F["lvl_qty"]])) for x in rows or []]

    bids = sorted(levels(r.get(F["depth_bid"], [])), key=lambda lv: lv.price, reverse=True)
    asks = sorted(levels(r.get(F["depth_ask"], [])), key=lambda lv: lv.price)
    return OrderBook(symbol=symbol, ts=ts, bids=bids, asks=asks)


def parse_orderbook(resp: Any, symbol: str, ts: datetime) -> OrderBook:
    """``GET /v1/depth`` -> book (bids descending, asks ascending)."""
    return _book(_result(resp), symbol, ts)


def parse_all_orderbooks(resp: Any, ts: datetime) -> dict[str, OrderBook]:
    """``GET /depth/all`` -> ``{canonical symbol: book}``."""
    out: dict[str, OrderBook] = {}
    for key, r in (_result(resp) or {}).items():
        sym = to_canonical(str(key))
        out[sym] = _book(r or {}, sym, ts)
    return out


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
    """``GET /v1/account/balances`` -> ``{asset: Balance}`` with ``free = value − locked``."""
    root = _result(resp)[F["balances_root"]]
    items = root.items() if isinstance(root, dict) else ((x.get(F["bal_asset"]), x) for x in root)
    out: dict[str, Balance] = {}
    for key, x in items:
        asset = str(x.get(F["bal_asset"]) or key).upper()
        total = _dec(x.get(F["bal_total"]), Decimal("0"))
        locked = _dec(x.get(F["bal_locked"]), Decimal("0"))
        out[asset] = Balance(asset=asset, free=total - locked, locked=locked)
    return out


def parse_fees(resp: Any) -> dict[str, tuple[Decimal, Decimal]]:
    """``GET /v1/account/fee`` -> ``{canonical symbol: (maker_pct, taker_pct)}`` in **percent**."""
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for key, x in (_result(resp) or {}).items():
        if not isinstance(x, dict):
            continue
        maker = _dec(x.get(F["fee_maker"])) * 100
        taker = _dec(x.get(F["fee_taker"])) * 100
        out[to_canonical(str(key))] = (maker.normalize(), taker.normalize())
    return out


def _status(x: dict[str, Any], qty: Decimal, filled: Decimal) -> OrderStatus:
    raw = str(x.get(F["ord_status"]) or "").upper()
    if raw in STATUS_MAP:
        return STATUS_MAP[raw]
    active = x.get(F["ord_active"])
    if active is None or active:
        return OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.NEW
    return OrderStatus.FILLED if qty > 0 and filled >= qty else OrderStatus.CANCELED


def parse_order(raw: Any, fallback_client_order_id: str | None = None) -> Order:
    """``resources.OrderResource`` -> ``Order``. ``order_id`` = the client id (Wallex's order key)."""
    x = _result(raw)
    cid = str(x.get(F["ord_client_id"]) or fallback_client_order_id or "")
    if x.get(F["ord_created"]):
        created = _ts(x[F["ord_created"]])
    elif x.get(F["ord_transact_time"]):
        created = _ts(x[F["ord_transact_time"]])
    else:
        created = datetime.now(UTC)
    updated = _ts(x[F["ord_updated"]]) if x.get(F["ord_updated"]) else created
    qty = _dec(x.get(F["ord_qty"]), Decimal("0"))
    filled = _dec(x.get(F["ord_filled"]), Decimal("0"))
    avg = _opt_dec(x.get(F["ord_avg_price"]))
    price = _opt_dec(x.get(F["ord_price"]))
    return Order(
        order_id=cid or None,
        client_order_id=cid,
        symbol=to_canonical(str(x[F["ord_symbol"]])),
        side=Side(str(x[F["ord_side"]]).lower()),
        type=OrderType(str(x[F["ord_type"]]).lower()),
        price=price if price else None,
        qty=qty,
        filled_qty=filled,
        avg_fill_price=avg if avg and filled > 0 else None,
        status=_status(x, qty, filled),
        created_at=created,
        updated_at=updated,
    )


def parse_orders(resp: Any) -> list[Order]:
    """Open orders / order history -> orders."""
    return [parse_order(x) for x in _rows(_result(resp))]


parse_open_orders = parse_orders


def fill_id(client_order_id: str, ts: str, price: str, qty: str) -> str:
    """Fills have no id: ``sha1(clientOrderId|timestamp|price|quantity)``."""
    return hashlib.sha1(f"{client_order_id}|{ts}|{price}|{qty}".encode()).hexdigest()


def parse_order_fills(raw: Any) -> list[Fill]:
    """``OrderResource.fills[]`` -> ``Fill`` list (oldest first)."""
    x = _result(raw)
    cid = str(x.get(F["ord_client_id"]) or "")
    symbol = to_canonical(str(x[F["ord_symbol"]]))
    order_side = str(x.get(F["ord_side"]) or "").lower()
    out = []
    for t in x.get(F["ord_fills"]) or []:
        if order_side in ("buy", "sell"):
            side = Side(order_side)
        else:
            side = Side.BUY if t.get(F["fill_is_buyer"]) else Side.SELL
        ts_raw, price_raw, qty_raw = (str(t.get(F[k]) or "") for k in ("fill_ts", "fill_price", "fill_qty"))
        default_fee_asset = symbol.split("/")[0] if side is Side.BUY else symbol.split("/")[1]
        out.append(
            Fill(
                fill_id=fill_id(cid, ts_raw, price_raw, qty_raw),
                order_id=cid or None,
                client_order_id=cid,
                symbol=symbol,
                side=side,
                price=_dec(price_raw),
                qty=_dec(qty_raw),
                fee=_dec(t.get(F["fill_fee"]), Decimal("0")),
                fee_asset=str(t.get(F["fill_fee_asset"]) or default_fee_asset).upper(),
                ts=_ts(ts_raw),
                is_maker=None,  # Swagger exposes fee coefficients only; maker/taker flag UNKNOWN
            )
        )
    return sorted(out, key=lambda f: f.ts)


def parse_fills(resp: Any) -> list[Fill]:
    """All fills of every order in a list response, de-duplicated by ``fill_id``."""
    seen: dict[str, Fill] = {}
    for row in _rows(_result(resp)):
        for f in parse_order_fills(row):
            seen.setdefault(f.fill_id, f)
    return sorted(seen.values(), key=lambda f: f.ts)


def page_size(resp: Any) -> int:
    """Number of orders in one list page (paging stops on a short page)."""
    return len(_rows(_result(resp)))


def parse_error(status: int, body: Any) -> tuple[str, str]:
    """Map an HTTP error to ``(kind, message)``; ``kind`` is one of the ``ERR_*`` constants."""
    message = ""
    if isinstance(body, dict):
        parts = [str(body.get(F["err_message"]) or body.get("error") or "")]
        details = body.get("result")
        if isinstance(details, dict):  # validation errors: {field: [messages]}
            for msgs in details.values():
                parts.extend(str(m) for m in (msgs if isinstance(msgs, list) else [msgs]))
        message = "; ".join(p for p in parts if p)
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
