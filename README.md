# Siavash — Wallex Spot Trading Agent

[![CI](../../actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)

Spot-only algorithmic trading agent for the Wallex challenge (TMN markets, 5,000,000 TMN capital).
Three quantitative strategies propose trades on every closed 15-minute candle; an ML scorer and an LLM
reviewer can only **filter or shrink** them; a hard risk layer (sizing, limits, breakers, kill-switch,
endgame) that the AI cannot bypass decides and executes. Every decision is logged with its reasons and can
be replayed exactly.

## Quick run for judges (no API key needed)

```bash
git clone <repo-url> siavash && cd siavash
make install
make backtest ARGS="--snapshot --llm off"
```

The last command backtests on the frozen data in `data/snapshot/` with the same engine that runs live and
writes `reports/backtest/<run_id>/REPORT.md` (equity, drawdown, trades, metrics, config and data hash).

## Architecture

```text
 Wallex REST (public + account)            BoxAPI LLM (OpenAI-compatible)
        │                                          │
 exchange/ client · endpoints · SymbolGuard        agents/ reviewer · analyst · reflector
        │                                          │      (cache · budget guard · fallback)
 data/ LiveDataService (candles, books) ──► features/ (15m/1h/4h, implied USDT price, regime)
        │                                          │
        │                       signals/ S1 trend breakout · S2 mean reversion · S3 implied deviation
        │                                          │
        │            engine/DecisionPipeline: fee filter → ML scorer → LLM reviewer → RiskManager
        │                                          │
 execution/ Router → Live / Paper / Backtest broker (same interface) → tracker → positions
        │                                          │
 risk/ breakers · kill-switch · endgame      reporting/ equity · metrics · judge report · replay
        └──────────────► logs/decisions.jsonl, orders.jsonl, fills.csv, equity.csv, llm/
```

One `Orchestrator` runs every mode; only the broker and the market-data source change
(live: Wallex, paper: real data + simulated fills, backtest/replay: historical data + simulated clock).

## Requirements

Python 3.11, `make`; Docker + Compose for the server. Secrets live only in `.env` (see `.env.example`
and [docs/RUNBOOK.md](docs/RUNBOOK.md#3-env-secrets--never-commit-never-paste-in-chatslogs)).

## Research and backtests

```bash
make download-data
make validate-data
make backtest ARGS="--processed data/processed --llm off"
make train
make walkforward
```

`python scripts/download_data.py --from-snapshot` restores `data/processed/` from the snapshot without
network access.

## Paper and live

```bash
make paper
siavash status
make report
```

Live trading is **human-only**: it requires the go-live checklist ([docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md)),
`competition_end_utc` in `config/risk.yaml` and `CONFIRM_LIVE=YES make live`. Operations, emergency
procedures and upgrades: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Replay and reports

```bash
make replay ARGS="--logs logs --live-data data/live"
make report
make shadow-report
```

- `siavash replay` re-runs the recorded period with the recorded LLM answers (no network) and reports the
  share of identical decisions (`reports/replay.md`).
- `make report` writes the judge indicators (`reports/live/REPORT.md`), including the AI counterfactual.

## Dashboard

A read-only monitoring dashboard with no login, plus `/health`, served on one port:

- **On Liara:** public at the app URL, https://siavash-starcoach-wallex.liara.run/. The engine starts it
  whenever env `PORT` is set, with `DASHBOARD_HOST=0.0.0.0`.
- **Locally:** `make dashboard-serve` serves it on `127.0.0.1:8080`. `make dashboard-demo` serves a
  synthetic dataset on `127.0.0.1:8090`.
- **Read-only:** no forms or trading controls, and no API keys or env values on any page.

Details: [docs/RUNBOOK.md](docs/RUNBOOK.md), section "Dashboard (read-only)".

## Repository layout

| path | content |
|---|---|
| `src/siavash/` | the agent (packages listed in the architecture above) |
| `config/` | `base.yaml`, `strategy.yaml`, `risk.yaml`, `denylist.txt` — every parameter lives here |
| `scripts/` | data download / validation, backtest, training, walk-forward, smoke test, preflight, deploy |
| `tests/` | unit tests (no network) and `integration/` live tests (human only) |
| `models/` | trained LightGBM models with metadata |
| `data/snapshot/` | frozen data for reproducible backtests |
| `reports/` | data quality, ML, backtest and walk-forward reports |
| `docs/` | Wallex API notes, RUNBOOK, go-live checklist, strategy document |

## Documents

- Strategy document for the judges: [docs/STRATEGY.md](docs/STRATEGY.md)
- Operations: [docs/RUNBOOK.md](docs/RUNBOOK.md) · API notes: [docs/wallex_api_notes.md](docs/wallex_api_notes.md)
- Reports: [reports/](reports/) · submission log package: `submission/` (built by `make export-submission`)
- Demo video: _link added after recording (T-1304)_
- Plan and task status: [WALLEX_AGENT_PLAN.md](WALLEX_AGENT_PLAN.md) · [PROGRESS.md](PROGRESS.md) · [CHANGELOG.md](CHANGELOG.md)
