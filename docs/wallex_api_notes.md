# Wallex API notes

> **Status: VERIFIED against the official Swagger** (`VERIFIED = True` in `src/siavash/exchange/endpoints.py`).
> Sources: `docs/vendor/wallex/wallex-spot-services.swagger.json` and
> `docs/vendor/wallex/wallex-basic-services.swagger.json` (Swagger wins over every older note).
> Public endpoints were additionally **observed** with read-only, unauthenticated requests
> (2026-10-22); trimmed real responses live in `tests/fixtures/wallex/real/`.
> Items marked **UNKNOWN** are not in Swagger: the code uses a conservative default and the human
> smoke test (T-108) must confirm them. Private endpoints have never been called by the agent.

## Base URL and authentication

| Item | Value | Source |
|---|---|---|
| Base URL | `https://api.wallex.ir` (env `WALLEX_BASE_URL` overrides) | Swagger host is `api.wallex.com`; MCP and the observed public API are on `api.wallex.ir`. **T-108 tests both** and sets the env var |
| Auth header | `x-api-key: <key>` | Swagger `securityDefinitions.ApiKeyAuth` |
| Public calls | sent **without** the key (they answered unauthenticated) | observed |
| Key storage | only in `.env` on the bot server; masked in all logs | plan rule 9 |

## Endpoints used by the bot

| Use | Method + path | Request | Response (as parsed) |
|---|---|---|---|
| Markets + last price | `GET /hector/web/v1/markets` | — | `result.markets[] {symbol, base_asset, quote_asset, price, quote_volume_24h, amount_precision, price_precision, is_spot, …}` |
| Order book | `GET /v1/depth` | `symbol=BTCTMN` | `result.ask[]`, `result.bid[]` each `{price, quantity, sum}` (numbers) |
| All books | `GET /depth/all` | — | `result{SYMBOL: {ask[], bid[]}}` (`get_all_orderbooks`) |
| Public trades | `GET /v1/trades` | `symbol=` | `result.latestTrades[] {symbol, price, quantity, sum, isBuyOrder, timestamp (ISO)}` |
| Candles | `GET /v1/udf/history` | `symbol, resolution, from, to` (unix s) | UDF `{s, t[], o[], h[], l[], c[], v[]}` (prices/volumes as strings) — see resolution quirks |
| Balances | `GET /v1/account/balances` | — | `result.balances{ASSET: {asset, value (total), locked, fiat, is_dust, …}}`; **free = value − locked** |
| Account fees | `GET /v1/account/fee` | — | `result{SYMBOL: {makerFeeRate, takerFeeRate, recent_days_sum}}`, rates are fractions (`0.002` = 0.2 %) → `WallexAccount.get_fees()` returns percent |
| Place order | `POST /v1/account/orders` | JSON, **all strings**: `symbol, side, type, quantity, client_id` (+ `price` for LIMIT) | `201` + `result: OrderResource` |
| Order status | `GET /v1/account/orders/{client_id}` | — | `result: OrderResource`; `404` if unknown |
| Cancel order | `DELETE /v1/account/orders/{client_id}` | — | `result: OrderResource` (Swagger also has `DELETE /v1/account/orders` with body `{clientOrderId}`; not used) |
| Open orders | `GET /v1/account/openOrders` | `symbol, page, per_page` | `result{<key>: [OrderResource]}` — the key name is **not documented**; the parser flattens every list in `result` |
| Order history | `GET /v1/account/orders` | `market, side, type, page, per_page (≤ 100), from, to (YYYY-MM-DD)` | same shape as open orders |

**`OrderResource`:** `clientOrderId, symbol, type, side, price, origQty, executedQty, executedPrice,
executedSum, executedPercent, fee, sum, status, active, stopPrice, stopPriceCondition, transactTime,
created_at, updated_at, fills[]`.
**`TradeResource` (each `fills[]` item):** `symbol, price, quantity, sum, fee, feeAsset, isBuyer,
timestamp, feeCoefficient, makerFeeCoefficient, takerFeeCoefficient`.

### Consequences in code

- **No exchange order id.** Orders are identified by the `client_id` we send; `Order.order_id` is set to
  the client id. `OrderIdMap` still records every submitted client id (used by reconcile).
- **No user-trades endpoint.** `get_my_trades(since)` pages `GET /v1/account/orders` (`per_page=100`,
  `from` = UTC date of `since − 1 day`, stop on a short page), adds open orders, takes each order's
  `fills[]`, and re-reads an order by client id when it shows an executed quantity but no `fills[]`.
  Fills have no id: `fill_id = sha1(clientOrderId|timestamp|price|quantity)`, which makes de-duplication
  stable across calls.
- **Ticker:** `get_tickers()` = one markets call (last price + 24h quote volume, **no bid/ask**);
  `get_ticker(symbol)` adds best bid/ask from `/v1/depth`.
- **Spot only:** markets with `is_spot = false` are parsed with `is_spot = is_active = False`, so the
  `SymbolGuard` rejects them. Margin/leverage fields are never read.

## Forbidden endpoints (never defined in code)

`/v1/account/wallets/*`, `/v1/account/ibans`, `/v1/account/money-withdrawal`, `/v1/account/card-numbers`
exist in `wallex-basic-services.swagger.json`. They are deliberately absent from `endpoints.py`;
`tests/unit/test_no_forbidden_paths.py` fails if such keywords appear in `src/`.

## UNKNOWN (to confirm in the T-108 smoke test)

| Item | Default in code |
|---|---|
| Spot minimum order quantity / value | not in Swagger. `min_qty` = one quantity step; `min_notional` from `config/base.yaml > exchange.min_notional` (`TMN: 100000`, `USDT: 5`). The undocumented `/v1/markets` showed `minNotional = 50000` for BTCTMN (sample kept as `tests/fixtures/wallex/real/v1_markets_undocumented.json`) |
| Case of `side` / `type` in the POST body | upper case `BUY`/`SELL`, `LIMIT`/`MARKET` (the enums Swagger lists for the order filters) |
| `status` strings | common spellings are mapped (`NEW, OPEN, PARTIALLY_FILLED, FILLED, DONE, CANCELED, CANCELLED, REJECTED, EXPIRED`); anything else is derived from `active` + `executedQty` |
| `client_id` length limit | the bot sends `SIAV-<26-char ULID>-<suffix>` (~33 chars). If a limit is found, set `CLIENT_ID_MAX_LEN` and `DECISION_ID_MAX_LEN = 16` in `siavash/core/ids.py` |
| Key of the order lists inside `result` | parser is key-agnostic |
| History `from`/`to` timezone | treated as UTC dates, queried one day early and filtered by fill timestamp |
| Maker/taker flag of a fill | `Fill.is_maker = None` (only fee coefficients are given) |
| Error codes | mapped by HTTP status (401/403 auth, 429 rate limit, 5xx server, 404 not found) and keywords in `message` + `result{field: [messages]}` (422 validation errors) |
| Rate limits | conservative `config/base.yaml > exchange.rate_limit`: public 5 req/s (burst 10), private 2 req/s (burst 4) |
| Base host | see table above |

## UDF resolution quirks (observed, contradict the Swagger example list)

Swagger describes `resolution` as "e.g. 1, 5, 15, 60, D". Observed on 2026-10-22:

- `15` returns **1-minute** bars; minutes without trades are missing (sparse). History ≥ 120 days.
- `60`, `240` and `720` all return **1-hour** bars; `1D` returns daily bars.
- `1` → `{"s": "no_data"}`; `5, 30, 120, 180, 360, 1W` → `{"s": "error", "errmsg": "Invalid resolution!"}`.
- a 60-day request at `60` returned only 696 bars → requests are capped (~700 bars).
- illiquid markets have holes even at 1h.

So 15m candles are aggregated from 1m bars (`resolution=15`) and 4h from 1h bars
(`endpoints.CANDLE_SOURCES`); pages are 4320 one-minute or 600 one-hour bars.

## Precision

`amount_precision` / `price_precision` are numbers of decimals → `qty_step = 10^-amount_precision`,
`price_tick = 10^-price_precision` (BTCTMN: 8 and 0). Quantities are rounded down, BUY prices down and
SELL prices up (`MarketRegistry`).

## Order types; stop orders

Swagger defines `LIMIT, MARKET, STOP_LIMIT, STOP_MARKET`. The bot only sends LIMIT and MARKET
(`SUPPORTS_SPOT_STOP = False`): stop-losses stay client side, checked every `loop.price_guard_s` (section 4.5).

## Symbol format

Exchange symbols are concatenated (`BTCTMN`, `USDTTMN`, `BTCUSDT`). Inside the bot every symbol is
canonical `BASE/QUOTE`; conversion only via `siavash.core.symbols` within `siavash.exchange`.

## Fees

`GET /v1/account/fee` returns the account's real maker/taker rates (Swagger example: 0.2 % / 0.2 %).
`config/risk.yaml > fees` still holds pessimistic 0.35 % / 0.35 %, which makes the fee filter require
an expected move ≥ 2.5 × 0.70 = 1.75 % (rejects every S3 candidate). **Human (T-108):** call the fee
endpoint once (read-only) and copy the real rates into `risk.yaml` (with a CHANGELOG note).

## WebSocket

Not in the provided docs; the bot polls REST (`loop.poll_seconds`, `loop.book_poll_seconds`).

## MCP

`docs/vendor/wallex_mcp/README.md`: remote MCP over HTTP at `https://api.wallex.ir/mcp`, same API key
(env `WALLEX_API_KEY`). Only read-only MCP tools may be exposed to the LLM agents (T-107 allow-list).
