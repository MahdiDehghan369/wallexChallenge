# Changelog

All notable changes to this project. Every version that goes live gets a `live-vX.Y` tag
and an entry under "Versions".

## Unreleased

- Project skeleton (T-001).
- T-901: backtest engine (`siavash.backtest.engine`, `scripts/run_backtest.py`, `make backtest`).
- T-101 (F5): Wallex endpoints re-done from the official Swagger; `endpoints.VERIFIED = True`.
  Differences from the earlier appendix-H / mock assumptions:
  - markets: `GET /v1/markets` (undocumented) → `GET /hector/web/v1/markets` (`result.markets[]`,
    `amount_precision`/`price_precision`, `is_spot`). No spot minimums in Swagger → new config
    `exchange.min_notional` (TMN 100000, USDT 5); `min_qty` = one quantity step. Non-spot markets are
    parsed as inactive.
  - tickers: the markets endpoint has no bid/ask → `get_tickers()` has `bid/ask=None`, `get_ticker()`
    takes top-of-book from `/v1/depth`. New `get_all_orderbooks()` (`/depth/all`).
  - auth header `X-API-Key` → `x-api-key` (same header, documented spelling).
  - orders: no exchange order id; orders are keyed by `client_id` (`GET`/`DELETE /v1/account/orders/{client_id}`),
    `Order.order_id` = client id. Response is `OrderResource` (`active`, `executedQty`, `fills[]`, …).
    Request `side`/`type` now upper case (`BUY`/`LIMIT`). Cancel no longer sends a JSON body.
  - user trades: `/v1/account/trades` does not exist → built from order history `fills[]` (paged,
    `per_page=100`), `fill_id = sha1(clientOrderId|timestamp|price|quantity)`.
  - unknown order `status` strings are derived from `active` + executed quantity.
  - errors: `result{field: [messages]}` (422) is included in the message used for classification.
  - new `WallexAccount.get_fees()` (`GET /v1/account/fee`, rates converted to percent).
  - forbidden-path test also bans `iban|card-numbers|wallets`.

## Versions

_No live versions yet._
