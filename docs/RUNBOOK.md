# RUNBOOK — Siavash Wallex trading agent

Operator guide for the server that runs the bot. Every command in the code blocks below exists
(checked by `python scripts/check_runbook_commands.py`). **Live trading and real orders are human-only.**

## 1. Prerequisites and server setup

- Linux VPS (2 vCPU, 4 GB RAM, 20 GB disk is plenty), Docker + Docker Compose plugin, git, Python 3.11
  (only needed outside Docker).
- Clock in sync (systemd-timesyncd / chrony): candles, breakers and the endgame use UTC timestamps.

```bash
git clone <repo-url> siavash && cd siavash
make install
cp .env.example .env
chmod 600 .env
make test
```

## 2. Network (T-109 — to be filled in by the human)

> **Placeholder — fill in after testing from the final server.**
>
> | target | URL | reachable? | latency (ms) | via proxy? |
> |---|---|---|---|---|
> | Wallex API | `https://api.wallex.ir/hector/web/v1/markets` | | | no |
> | Wallex API (Swagger host) | `https://api.wallex.com/hector/web/v1/markets` | | | no |
> | BoxAPI (LLM) | `https://ai.boxapi.ir/ai/v1` | | | no |

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://api.wallex.ir/hector/web/v1/markets
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://api.wallex.com/hector/web/v1/markets
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://ai.boxapi.ir/ai/v1/models
```

If the LLM is only reachable through a proxy: set `HTTPS_PROXY` in `.env` and `llm.use_proxy: true` in
`config/base.yaml` (`exchange.use_proxy` stays `false`).

## 3. `.env` (secrets — never commit, never paste in chats/logs)

| variable | meaning |
|---|---|
| `MODE` | `paper` (default) or `live` — `live` additionally needs `CONFIRM_LIVE=YES` and `make live` |
| `WALLEX_API_KEY` | Wallex API key (header `x-api-key`); only on the bot server |
| `WALLEX_BASE_URL` | empty → `https://api.wallex.ir`; set to the host chosen in T-108 |
| `LLM_API_KEY` | BoxAPI key |
| `LLM_BASE_URL` | empty → `https://ai.boxapi.ir/ai/v1` (from `config/base.yaml`) |
| `LLM_MODEL` / `LLM_FALLBACK_MODEL` | empty → `gpt-5.4-mini` / `gpt-5-mini` |
| `HTTPS_PROXY` | only if the LLM needs a proxy (see section 2) |
| `ALERT_WEBHOOK_URL` | optional webhook for alerts (`alerts.backend: webhook`) |
| `VERSION_TAG` | set by `scripts/deploy.sh` (`live-vX.Y`) |

The LLM budget is 3 USD in total: caps live in `config/base.yaml > llm.budget` (20 calls/h, 200/day,
400k tokens/day). When a cap is reached the bot keeps trading with the deterministic fallbacks and
raises an `llm_budget` alert.

## 4. First checks and the first paper run

```bash
siavash llm-check
python scripts/smoke_test.py --save-samples docs/vendor/wallex/samples
python scripts/smoke_test.py --base-url https://api.wallex.com
make test-live
```

- `llm-check` writes `state/llm_capabilities.json` (tools / JSON mode support) — 2 tiny requests.
- The smoke test is read-only unless `--place-test-order` is given. It prints the **account fees**:
  copy them into `config/risk.yaml > fees` (CHANGELOG note), then re-run the research:

```bash
make download-data
make validate-data
make train
make walkforward
make backtest ARGS="--processed data/processed --llm off"
```

Paper trading (real market data, simulated fills, real LLM through the cache and budget):

```bash
make docker-build
make docker-up
make docker-logs
```

or without Docker: `make paper`. Let it run 12–24 h (shadow run, T-1002) before going live.

## 5. Go-live

Only after the go-live checklist (`docs/GO_LIVE_CHECKLIST.md`, T-1107) is complete and
`risk.endgame.competition_end_utc` is set (the bot refuses to start live without it). Then, as a human:

```bash
CONFIRM_LIVE=YES make live
```

The first live day runs at `initial_live_risk_scale` (0.5×) of normal risk.

## 6. Monitoring

```bash
siavash status
siavash status --json
tail -f logs/decisions.jsonl
tail -f logs/orders.jsonl
docker compose ps
```

- Logs (`logs/`): `decisions.jsonl` (every decision with reasons), `orders.jsonl`, `fills.csv`,
  `equity.csv`, `latency.jsonl`, `llm/calls.jsonl`, `regime.jsonl`.
- Health: the engine writes `state/heartbeat` every `loop.heartbeat_s`; the Docker healthcheck fails when
  it is stale. The watchdog restarts a stuck main loop.
- Alerts (log or webhook): breaker changes, kill-switch, entries/exits, API errors, LLM budget, startup.
- Judge metrics report → `reports/live/REPORT.md` (+ `metrics.json`, charts):

```bash
make report
siavash report --logs logs --out reports/live
```

## 7. Emergency procedures

| situation | action |
|---|---|
| stop opening positions | `make pause` (exits keep running) → later `make resume` |
| close everything now | `make flatten` (engine sells every position with reason OPERATOR) |
| kill-switch fired (drawdown ≥ 5 %) | bot is FLATTEN_AND_HALT; investigate, then `siavash resume --confirm` |
| daily loss ≥ 2 % | breaker pauses entries until the next Tehran trading day (automatic) |
| Wallex API down / errors | breaker pauses entries after 5 errors in 5 min; clears after 15 min clean; positions keep client-side SL checks |
| stale market data (> 180 s) | entries blocked automatically until data is fresh |
| LLM down / budget exhausted | automatic deterministic fallback (`status=FALLBACK`); no action needed |
| crash / container restart | on start the bot reconciles balances, open orders and positions before trading; check `siavash status` |

```bash
make pause
make resume
make flatten
siavash resume --confirm
docker compose restart
```

## 8. Upgrading the version during the challenge

1. Run the candidate as a shadow (paper mode, separate `logs/`/`state/` directories) next to live.
2. Compare the shadow with a backtest of the same period (→ `reports/consistency.md`):

```bash
make shadow-report ARGS="--logs logs-shadow --live-data data/live"
```

3. Deploy — the script checks a clean tree, runs lint + tests, writes the CHANGELOG "Versions" entry,
   tags `live-vX.Y`, rebuilds with the git sha and restarts:

```bash
scripts/deploy.sh --dry-run --version live-v1.1 --message "what changed"
scripts/deploy.sh --version live-v1.1 --message "what changed"
```

## 9. Endgame and settlement

Driven by `config/risk.yaml > endgame` relative to `competition_end_utc`:
no new entries 6 h before the end → passive unwind from 3 h → forced liquidation (market) from 1 h;
USDT is sold to TMN (`allow_usdt_liquidation`). Watch `siavash status` during the last hours; if needed,
`make flatten` manually.

## 10. Log backup

Hourly copy of `logs/` and `state/` to a second location (another disk or host):

```bash
crontab -e
```

Cron entry (adjust the destination):

```text
0 * * * * cd /opt/siavash && tar czf /backup/siavash-$(date -u +\%Y\%m\%d\%H).tgz logs state && rsync -a /backup/ backup-host:/srv/siavash-backup/
```
