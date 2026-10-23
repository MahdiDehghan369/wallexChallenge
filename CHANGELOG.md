# Changelog

All notable changes to this project. Every version that goes live gets a `live-vX.Y` tag
and an entry under "Versions".

## Unreleased

- Project skeleton (T-001).
- Read-only monitoring dashboard (`siavash dashboard`, `make dashboard-serve`, `src/siavash/dashboard/`).
  - **Server:** one Starlette server serves `/health` and the dashboard on one port. `siavash run`
    starts it in a thread when env `PORT` is set.
  - **Access:** `/health` is public. Every other path needs HTTP Basic auth; the dashboard is disabled
    while `DASHBOARD_PASSWORD` is unset. All routes are GET-only, and SQLite is opened with `mode=ro`.
  - **UI:** Persian RTL, light and dark mode, Chart.js 4.4.1 from cdnjs, refreshes every 30 s.
  - **Config:** new `base.yaml > dashboard` block, plus env `DASHBOARD_USER`, `DASHBOARD_PASSWORD`,
    `DASHBOARD_HOST` and `PORT`.
  - **Compose:** publishes the port on the host's loopback only.
  - **Dependencies:** new explicit deps `starlette`, `uvicorn`, `jinja2` and `markdown-it-py`.
- Dashboard fix: `/ai` crashed on Liara with a TypeError (`int + str`). The cause was `calls.jsonl` token
  counts stored as strings.
  - **Number parsing:** every number read from logs or the store now goes through `to_int` / `to_float`
    in `dashboard/data.py`. Null or missing values become 0 or "—"; malformed values do too, with one
    `dashboard_bad_number` warning per field.
  - **Fault isolation:** each page is built from sections, and a section that fails shows an inline
    "data unavailable" box. A failure outside any section gives a page-level box with status 200, so
    no page returns a 500.
  - **Decision tail:** trades now read exit reasons from the tolerant decisions tail.
  - **git_sha:** the System page shows the record's and the running process's git_sha side by side. The
    Dockerfile now uses `ARG GIT_SHA` with no default and `ENV GIT_SHA=$GIT_SHA`. Liara builds must pass
    `--build-arg GIT_SHA=...` (RUNBOOK).
- Liara go-live fix: the container stayed in paper although Liara had `MODE=live` and `CONFIRM_LIVE=YES`.
  - **Root cause:** the Dockerfile ran `CMD ["run", "--mode", "paper"]`. On top of that, `siavash run`
    defaulted `--mode` to `"paper"`, and `_cfg()` wrote that value over the env `MODE` that `load_config`
    had already applied.
  - **Fix:** `run` without `--mode` now uses env `MODE`, then `base.yaml`, and the Dockerfile runs
    `CMD ["run"]`. An explicit `--mode` still wins, with the warning `engine_mode_flag_overrides_env`.
  - **Safety checks unchanged:** `CONFIRM_LIVE=YES` is checked on the effective mode, and live still
    refuses to start without `competition_end_utc`. There is no paper fallback anywhere.
  - **git_sha:** `unknown` meant the image was built without `--build-arg GIT_SHA`. The Dockerfile also
    takes `ARG VERSION_TAG` now.
  - **Config:** `config/risk.yaml > endgame.competition_end_utc` = `2026-10-26T19:30:00Z` (23:00 Tehran).
    Tests now pin that value.
  - **Account fees at startup** (`siavash.exchange.fees`, read-only `GET /v1/account/fee`): logs `account_fees`
    for the traded markets. The effective `risk.fees` is the higher of the config and the account rate,
    never lower, and a difference logs `account_fees_differ`. If the API fails, the config fees stay.
  - **Engine identity:** the engine writes `state/engine.json`: mode, git_sha, version_tag, config_hash,
    started_at, effective fees and the endgame config.
  - **Dashboard:** shows the engine's real mode (header badge), its git_sha, version_tag and fees, and the
    endgame phase and its start times in Tehran time and UTC. These are on the System page, with a KPI on
    the overview.
  - **Persistent disk:** new env `PATHS_ROOT` (Liara: `/app/persist`) prefixes the relative `base.yaml > paths`.
    New path `paths.submission`. `siavash report` and `scripts/export_submission.py` default to the
    config paths, so on Liara they write to `/app/persist/reports` and `/app/persist/submission`.
  - **Image:** now contains `make`, the `Makefile` and `CHANGELOG.md`, so `make report` and
    `make export-submission` work inside the container.
  - **Docs:** new RUNBOOK section "5a. Liara go-live".
- D-001: edge diagnostics (`siavash.research`, `scripts/run_diagnostics.py`, `make diagnostics`,
  research-only profiles in `config/profiles/`, reports in `reports/diagnostics/`). No change to
  `config/strategy.yaml`, `config/risk.yaml` or any live / paper / backtest code path.
- T-1302 (draft): `docs/STRATEGY.md` rendered with `fill_strategy_numbers.py --draft` (new flag: DRAFT
  banner). The template now has a D-001 diagnostics section (no robust edge, S3 only gross edge, S1 none,
  ML in-sample, LLM not evaluated: empty cache), all numbers read from the reports.
- T-904 report review: the walk-forward report now shows per-strategy trade counts per sensitivity cell,
  flags parameters that cannot affect any trade (`inert`, e.g. `s3.z_entry` when the S3 TP cap
  `s3.tp_pct` < the fee-filter move), labels the summed-return drawdown `sum_dd_pct` and adds a compounded
  `equity_dd_pct` (`max_position_pct` per trade), marks the ML θ table and the `+ML` variants IN-SAMPLE
  when the model's training range overlaps the period (decision source: `reports/diagnostics/SUMMARY.md`;
  no more "consider enabling ML"), counts analyst / reviewer LLM statuses and cache rows per variant, and
  flags variants below `ml.min_trades_per_day`. New `--reuse-runs` re-reads existing engine logs. No
  strategy / risk config change.
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
- T-501 (plan revision): LLM provider is BoxAPI (`base_url https://ai.boxapi.ir/ai/v1`, models
  `gpt-5.4-mini` / `gpt-5-mini`, no proxy) in `config/base.yaml > llm` (env still overrides).
  New `siavash.agents.budget.LLMBudget` + config `llm.budget` (20 calls/h, 200 calls/day, 400k tokens/day,
  3000 input tokens/call): counters persist in the state store (Tehran hour/day), cache hits are free,
  `BudgetExceeded` → reviewer/analyst deterministic fallback + `llm_budget` alert, never retried on the
  fallback model. New `siavash llm-check` writes `state/llm_capabilities.json`; `LLMClient` drops tools
  when unsupported and uses `response_format=json_object` only when supported (otherwise a JSON-only
  instruction + JSON extraction). `calls.jsonl` now logs `prompt_tokens` / `completion_tokens`.

## Versions

_No live versions yet._
