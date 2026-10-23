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
| `MODE` | `paper` (default) or `live`. `siavash run` without `--mode` uses it (an explicit `--mode` wins and logs `engine_mode_flag_overrides_env`). `live` additionally needs `CONFIRM_LIVE=YES` and `competition_end_utc` |
| `CONFIRM_LIVE` | `YES` to allow live (human only) |
| `PATHS_ROOT` | persistent disk: relative `base.yaml > paths` (logs, state, data, models, reports, submission) are put under it. Liara: `/app/persist` |
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

At start the engine reads `GET /v1/account/fee` (read-only) and logs `account_fees` (maker / taker of every
traded market). The effective fee is the higher of the account rate and `config/risk.yaml > fees`. It
is never lower than the API, and a difference logs the warning `account_fees_differ`. If the call
fails, the config fees stay, with the error `account_fees_unavailable`. Fee entries that are not dicts or
have no usable maker/taker rate are skipped with the warning `fee_entries_skipped` (count plus a sample of
their keys); only a response with no valid entry counts as a failure. To inspect the response shape (keys
and types only) and the parsed rates for BTCTMN, ETHTMN and USDTTMN, run the read-only
`siavash fees --diagnose`. It uses the engine's `WALLEX_API_KEY` / `WALLEX_BASE_URL` (default
`https://api.wallex.ir`). The engine writes its identity to
`state/engine.json`: mode, git_sha, version_tag, config_hash, effective fees and the endgame config. The
dashboard shows that file.

### 5a. Liara go-live

The app is `siavash-starcoach-wallex` (platform docker), with its disk mounted at `/app/persist`. All commands are human
only. Run them from the repo root on the tagged commit.

`liara.json` names the same app and port and mounts the disk `siavash-data` at `/app/persist`. The commands
below still pass `--app` explicitly. `<tag>` is the release being deployed (e.g. `live-v1.4`).

**1. Environment** (Liara panel or CLI). Secrets (`WALLEX_API_KEY`, `LLM_API_KEY`) are set only in the
panel and never pasted into commands or chats. The dashboard has no login, so it needs no credentials; old
`DASHBOARD_USER` / `DASHBOARD_PASSWORD` entries are ignored and can be deleted.

```bash
liara env set MODE=live CONFIRM_LIVE=YES PATHS_ROOT=/app/persist DASHBOARD_HOST=0.0.0.0 PORT=8080 --app siavash-starcoach-wallex
liara env list --app siavash-starcoach-wallex
```

Verify: `env list` shows `MODE=live`, `CONFIRM_LIVE=YES`, `PATHS_ROOT=/app/persist` and a `PORT` equal to
the `--port` used below.

**2. Deploy** the tagged commit, baking in the commit sha and the release tag:

```bash
git checkout <tag>
liara deploy --app siavash-starcoach-wallex --platform docker --port 8080 --build-arg "GIT_SHA=$(git rev-parse --short HEAD)" --build-arg VERSION_TAG=<tag> --message <tag>
liara logs --app siavash-starcoach-wallex --since "10 minutes ago"
```

Verify, in the startup logs:
- `engine_mode` with `mode=live configured_mode=live` and `paths` under `/app/persist`;
- `account_fees` (and `account_fees_differ` if the account rate is not the config rate);
- every line carries `git_sha=<sha>`, `version_tag=<tag>` and `mode=live`.

If you see `refusing to start LIVE` instead, `CONFIRM_LIVE` is not `YES`. If you see
`competition_end_utc is empty`, the endgame end time is missing. In both cases the bot exits; it never
falls back to paper.

**3. Inside the container:**

```bash
liara shell --app siavash-starcoach-wallex
```

Then, in that shell:

```bash
echo "$MODE $CONFIRM_LIVE $PATHS_ROOT $GIT_SHA $VERSION_TAG"
df -h /app/persist
ls -l /app/persist
cat /app/persist/state/engine.json
tail -n 20 /app/persist/logs/decisions.jsonl
grep -c account_fees /app/persist/logs/system.log
grep fee_entries_skipped /app/persist/logs/system.log | tail -n 1
siavash fees --diagnose
siavash status
curl -s http://127.0.0.1:8080/health
```

Expected:
- `df` shows a separate volume for `/app/persist`, not the container's root filesystem. If it is the root
  filesystem, the disk is not mounted, and logs and state are lost on the next deploy.
- `engine.json` has `"mode": "live"`, the commit sha, `"version_tag": "<tag>"`, `fees.effective` and
  `endgame.competition_end_utc = 2026-10-26T19:30:00Z`.
- `/health` returns `{"status":"ok", ...}`.

**4. Dashboard:** open `https://siavash-starcoach-wallex.liara.run/system`.
- The header badge shows `حالت: live`.
- The "engine" column shows the commit sha and `<tag>`.
- The endgame table shows the phases in Tehran time: NO_NEW 2026-10-26 17:00, UNWIND 20:00, FORCE 22:00
  and DONE 23:00.

**5. Reports and the submission package** run in the same `liara shell`, and write to the persistent disk
through `PATHS_ROOT`:

```bash
siavash report
make report
make export-submission
ls -l /app/persist/reports/live /app/persist/submission
```

The dashboard has no "refresh report" button, because it is read-only. Re-run `siavash report`, and the
Reports page shows the new `reports/live/REPORT.md` on its next refresh.

**Emergency** (same shell): `siavash pause`, `siavash flatten --confirm` and `siavash status`. They act on
`/app/persist/state/state.sqlite`, which the engine reads.

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

### Dashboard (read-only)

A Persian web dashboard with six pages, refreshed every `dashboard.refresh_s` (30 s):
- overview;
- opportunities, with the full decision JSON per row;
- positions and trades;
- AI: LLM calls, budget, regime history, counterfactual;
- reports: renders `reports/**/*.md`;
- system: heartbeat, API errors and latency, git_sha, config_hash.

It only reads `logs/`, `state/state.sqlite` (opened with `mode=ro`), `data/live/` and `reports/`. It has no
buttons or endpoints that place or cancel orders, pause, resume, or change config. To act, use the CLI
commands in section 7.

- **Access:** no login. Every page is public wherever the server is bound. On Liara that is the app URL,
  so anyone with the link can see positions, PnL, decisions and the engine identity. No page shows API
  keys or env values.
- **`/health`:** unauthenticated, on the same port. It returns `200 {"status":"ok"}` while
  `state/heartbeat` is younger than `loop.watchdog_timeout_s`, otherwise `503`.
- **Bind address:** `base.yaml > dashboard` gives `127.0.0.1:8080`. Env `DASHBOARD_HOST` and `PORT`
  override it.
- **Inside the engine:** `siavash run` starts the server in a background thread only when `PORT` is
  set.

**Local / VPS host (no Docker):**

```bash
make dashboard-serve
siavash dashboard
```

**Preview on synthetic data** (no engine, no real logs). `make dashboard-demo` writes a demo dataset into a
new temp dir and serves it on `http://127.0.0.1:8090`. Pass options with
`ARGS`, e.g. `make dashboard-demo ARGS="--port 9000 --dir /tmp/siavash-demo"`.

```bash
make dashboard-demo
```

**UI behaviour:**
- Pages refresh in place every `dashboard.refresh_s` seconds: the script re-GETs the same page and swaps
  the content. Without JavaScript the page reloads instead.
- The System health tiles turn green, amber or red using `base.yaml > dashboard.health`.
- The theme toggle (dark by default) is remembered in the browser only.

**VPS with Docker:**
- Compose sets `PORT` (default 8080, `DASHBOARD_PORT` in `.env` changes it) and publishes the port on
  the host's **loopback only** (`127.0.0.1:8080`).
- Inside the container the server listens on `0.0.0.0` so the port mapping can reach it. It is still
  not reachable from outside the VPS.
- Open it through an SSH tunnel from your own machine, then browse `http://127.0.0.1:8080`:

```bash
ssh -N -L 8080:127.0.0.1:8080 user@your-vps
curl -s http://127.0.0.1:8080/health
```

**Liara:**
- In the app's environment settings, set `PORT` to the app's port and `DASHBOARD_HOST=0.0.0.0`. The
  dashboard starts whenever `PORT` is set.
- Point the platform health check at `/health`.
- The dashboard is public at the app URL, `https://siavash-starcoach-wallex.liara.run/`, with no login.
- Only use `0.0.0.0` there, behind Liara's HTTPS. Never expose the plain-HTTP port of a VPS directly.
- Deploy with the commit sha as a build arg, or `git_sha` shows `unknown`: `.git` is not in the image,
  so the `GIT_SHA` build arg is the only source.

```bash
liara deploy --app <app> --platform docker --port 8080 --build-arg "GIT_SHA=$(git rev-parse --short HEAD)"
```

- The System page shows two `git_sha` columns:
  - the last decision record: the build that made that decision;
  - the running process.

  Right after a redeploy, only the process column changes, until the engine logs a new decision.
- A page section that cannot read its data shows an inline "data unavailable" box naming the section
  and the error type. The details are in the app log as `dashboard_section_failed` /
  `dashboard_bad_number`.

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
