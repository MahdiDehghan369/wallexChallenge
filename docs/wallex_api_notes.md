# Wallex API notes

> **Status: UNVERIFIED** (`VERIFIED = False` in `src/siavash/exchange/endpoints.py`).
> No official documentation was available in `docs/vendor/wallex/` when this was written. Everything
> below comes from appendix H of `WALLEX_AGENT_PLAN.md` (public info that may be stale). Items marked
> **MOCK** are placeholders the code uses so it can be built and tested; they are *not* known facts.
> When the official docs (and real response samples) arrive, re-run T-101 (prompt F5 in the plan):
> update `endpoints.py`, this file and `tests/fixtures/wallex/*.json`, then set `VERIFIED = True`.

## Observed behaviour of the public API (read-only probes, 2026-10-22)

Unauthenticated GET requests from the dev machine succeeded; trimmed real responses are stored in
`tests/fixtures/wallex/real/` and parsed by the unit tests. Findings:

- `GET /v1/markets` → `{"result": {"symbols": {...}}, "message", "success"}` with 373 symbols. Each entry
  has `baseAsset`, `quoteAsset`, `stepSize` (quantity **decimals**, e.g. 8 for BTCTMN), `tickSize`
  (price decimals, 0 for TMN), `minQty` (e.g. `1e-08`), `minNotional` (e.g. `50000` TMN),
  `baseAssetPrecision`, `quotePrecision`, `isNew`, `isZeroFee`, `isMarketTypeEnable`, and
  `stats{bidPrice, askPrice, lastPrice, 24h_volume, 24h_quoteVolume, 24h_tmnVolume, 24h_highPrice, …}`
  (numbers as strings). There is **no** `isActive` field (parser treats missing as active).
- `GET /v1/depth?symbol=` → `result.ask[]` / `result.bid[]` with numeric `price`, `quantity` and string `sum`.
- `GET /v1/trades?symbol=` → `result.latestTrades[]` with `price`, `quantity`, `sum`, `isBuyOrder`, ISO `timestamp`.
- `GET /v1/udf/history` — **resolution semantics are not standard**:
  - `resolution=15` returns **1-minute** bars; minutes without trades are missing (sparse). Data goes back ≥ 120 days.
  - `resolution=60`, `240` and `720` all return **1-hour** bars; `1D` returns daily bars.
  - `resolution=1` → `{"s": "no_data"}`; `5, 30, 120, 180, 360, 1W` → `{"s": "error", "errmsg": "Invalid resolution!"}`.
  - a 60-day request at `60` returned only 696 bars (~29 days) → requests are capped (~700 bars).
  - illiquid markets have holes even at 1h (USDT/TMN had an 11 h gap).
  - **Consequence in code:** 15m candles are aggregated from 1m bars (`resolution=15`) and 4h from 1h bars
    (`endpoints.CANDLE_SOURCES`); pages are 4320 one-minute bars or 600 one-hour bars.

Private endpoints were **not** touched (no key, and placing orders is human-only).

## Base URL and authentication

| Item | Value | Source |
|---|---|---|
| Base URL | `https://api.wallex.ir` (overridable with env `WALLEX_BASE_URL`) | appendix H |
| Auth header | `X-API-Key: <key>` | appendix H |
| Key storage | only in `.env` on the bot server; masked in all logs | plan rule 9 |

## Endpoints

| Use | Method | Path | Params / body | Response (as parsed) | Status |
|---|---|---|---|---|---|
| Markets + tickers | GET | `/v1/markets` | — | `result.symbols{SYMBOL: {baseAsset, quoteAsset, stepSize, tickSize, minQty, minNotional, stats{bidPrice, askPrice, lastPrice, 24h_quoteVolume, …}}}` | **observed** |
| Order book | GET | `/v1/depth` | `symbol=BTCTMN` | `result.ask[]`, `result.bid[]` each `{price, quantity, sum}` | **observed** |
| Public trades | GET | `/v1/trades` | `symbol=BTCTMN` | `result.latestTrades[] {price, quantity, isBuyOrder, timestamp}` | **observed** |
| Candles | GET | `/v1/udf/history` | `symbol, resolution, from, to` (unix s, inclusive) | TradingView UDF `{s, t[], o[], h[], l[], c[], v[]}` | **observed** (see resolution quirks above) |
| Balances | GET | `/v1/account/balances` | — | `result.balances{ASSET: {asset, value, locked}}` | path: H; payload **MOCK** |
| Place order | POST | `/v1/account/orders` | `symbol, side (buy/sell), type (limit/market), price, quantity, client_id` | order object | path/body: H ("احتمالی"); response **MOCK** |
| Cancel order | DELETE | `/v1/account/orders` | `orderId` or `clientOrderId` | order object | path: H; body **MOCK** |
| Order status | GET | `/v1/account/orders/{id}` | — | order object | path: H; payload **MOCK** |
| Open orders | GET | `/v1/account/openOrders` | `symbol=` (optional) | `result.orders[]` | path: H; payload **MOCK** |
| User trades | GET | `/v1/account/trades` | `symbol=` | `result.AccountLatestTrades[]` | path: H; payload **MOCK** |
| User fee | GET | `/v1/account/fee` | — | not parsed yet | H says "احتمالی" |

**MOCK order object:** `{orderId, clientOrderId, symbol, type, side, price, origQty, executedQty,
executedPrice, status (NEW|PARTIALLY_FILLED|FILLED|CANCELED|REJECTED|EXPIRED), created_at, updated_at}`.

**MOCK user trade:** `{id, orderId, clientOrderId, symbol, side, price, quantity, fee, feeAsset, timestamp, isMaker}`.

## Error codes

Unknown. The code maps errors mainly by HTTP status (`parse_error`): 401/403 → auth, 429 → rate
limited (respect `Retry-After`), 5xx → server; for 4xx it looks for keywords in `message`
(insufficient/balance, precision, minimum, closed, not found) — the **MOCK** body is
`{"success": false, "code": …, "message": "…"}`.

## Rate limits

Unknown. Conservative defaults in `config/base.yaml > exchange.rate_limit`: public 5 req/s (burst 10),
private 2 req/s (burst 4). Order placement is never retried automatically (T-802 handles idempotency).

## Symbol format

Exchange symbols are concatenated (`BTCTMN`, `USDTTMN`, `BTCUSDT`). Inside the bot every symbol is
canonical `BASE/QUOTE`; conversion only via `siavash.core.symbols` within `siavash.exchange`.

## Price / quantity precision and minimums

Observed: `stepSize` = quantity decimals, `tickSize` = price decimals, plus `minQty` and
`minNotional` (BTCTMN: 8, 0, 1e-08, 50000). The parser converts decimals to steps (a value < 1 would
be treated as the step itself). Whether the order endpoint enforces exactly these values is still
unverified — the smoke test (T-108) should confirm.

## Order types; spot stop orders

Appendix H lists `limit` and `market`. Whether spot **stop** orders exist is unknown
(`SUPPORTS_SPOT_STOP = None`), so stop-losses are enforced client-side every 10 s (section 4.5).

## `client_order_id`

Unknown whether the API accepts a client id and what its length limit is
(`SUPPORTS_CLIENT_ORDER_ID = None`, `CLIENT_ID_MAX_LEN = None`). The bot always sends `client_id` and
keeps its own `order_id ↔ client_order_id` map in `state/` (T-105 `OrderIdMap`), so it works either way.
If a length limit is confirmed, set `DECISION_ID_MAX_LEN = 16` in `siavash/core/ids.py` and note it here.

## WebSocket

Not used. Unknown whether one exists; the bot polls REST (`loop.poll_seconds`, `loop.book_poll_seconds`).

## Fees

Unknown for the challenge account. `config/risk.yaml > fees` holds pessimistic 0.35 % maker and taker.
With these values the fee filter needs an expected move ≥ 2.5 × 0.70 = 1.75 %, which rejects every S3
candidate (target capped at 1.5 %). **Human:** read the real maker/taker fees from the panel into
`docs/vendor/wallex/fees.txt`; the agent then updates `risk.yaml`.

## MCP

No Wallex MCP docs in `docs/vendor/wallex_mcp/`. T-107 will use the native read-only tool backend
(`llm.tools_backend: auto` → `native`) until a server definition is provided.
