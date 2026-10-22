# Wallex API notes

> **Status: UNVERIFIED** (`VERIFIED = False` in `src/siavash/exchange/endpoints.py`).
> No official documentation was available in `docs/vendor/wallex/` when this was written. Everything
> below comes from appendix H of `WALLEX_AGENT_PLAN.md` (public info that may be stale). Items marked
> **MOCK** are placeholders the code uses so it can be built and tested; they are *not* known facts.
> When the official docs (and real response samples) arrive, re-run T-101 (prompt F5 in the plan):
> update `endpoints.py`, this file and `tests/fixtures/wallex/*.json`, then set `VERIFIED = True`.

## Base URL and authentication

| Item | Value | Source |
|---|---|---|
| Base URL | `https://api.wallex.ir` (overridable with env `WALLEX_BASE_URL`) | appendix H |
| Auth header | `X-API-Key: <key>` | appendix H |
| Key storage | only in `.env` on the bot server; masked in all logs | plan rule 9 |

## Endpoints

| Use | Method | Path | Params / body | Response (as parsed) | Status |
|---|---|---|---|---|---|
| Markets + tickers | GET | `/v1/markets` | — | `result.symbols{SYMBOL: {baseAsset, quoteAsset, stats{…}, minQty?, minNotional?, precision?}}` | path: H; precision/stat names **MOCK** |
| Order book | GET | `/v1/depth` | `symbol=BTCTMN` | `result.ask[]`, `result.bid[]` each `{price, quantity, sum}` | H |
| Public trades | GET | `/v1/trades` | `symbol=BTCTMN` | `result.latestTrades[] {price, quantity, isBuyOrder, timestamp}` | H |
| Candles | GET | `/v1/udf/history` | `symbol, resolution, from, to` (unix s) | TradingView UDF `{s, t[], o[], h[], l[], c[], v[]}` | H; resolutions & max range **UNVERIFIED** |
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

Appendix H says the markets endpoint "probably" returns `minQty` / `minNotional` / precision.
The parser reads `minQty`, `minNotional` and two **MOCK** fields `stepSize` (quantity decimals) and
`tickSize` (price decimals); a value < 1 is treated as the step itself. Missing → step 1 TMN price /
0.000001 qty and minimums 0 (the risk layer still enforces config minimums). Verify before live.

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
