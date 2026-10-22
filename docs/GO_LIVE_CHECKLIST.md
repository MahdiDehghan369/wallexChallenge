# Go-live checklist (T-1107)

Complete **every** item on the bot server, then commit this file with the boxes ticked.
Going live is a human action: `CONFIRM_LIVE=YES make live`.

## 1. Automatic checks

```bash
python scripts/preflight.py
```

Must end with `READY for live`. It checks: unit tests green · `endpoints.VERIFIED` · `competition_end_utc`
set and in the future · denylist loaded (345 symbols) · `initial_live_risk_scale ≤ 0.5` · every
`config/risk.yaml` limit within section 5 of the plan · shadow report ≥ 12 h without critical errors ·
ML model present and on the current feature set (if ML is enabled) · fresh heartbeat · clean git tree on a
`live-vX.Y` tag · API key only in `.env` (untracked, mode 600) · Wallex public API and LLM reachable.

- [ ] `python scripts/preflight.py` → READY (date/time: ________, tag: ________)

## 2. Before the start (human)

- [ ] T-108 smoke test done on this server; real account fees copied into `config/risk.yaml > fees` (CHANGELOG note)
- [ ] `risk.endgame.competition_end_utc` = official end time from the organiser (UTC)
- [ ] Network section of `docs/RUNBOOK.md` filled in (T-109)
- [ ] Shadow run ≥ 12 h reviewed (`reports/shadow/REPORT.md`, `reports/consistency.md`)
- [ ] Walk-forward report reviewed and final parameters decided (`reports/backtest/walkforward/REPORT.md`)
- [ ] `ml.enabled` decided from `reports/ml_walkforward.md` (keep `false` unless the report recommends it)

## 3. Fair play and operations (human)

- [ ] No team member trades on the challenge account through the Wallex app / website
- [ ] Team members do not coordinate trades on personal accounts in the same markets
- [ ] Log backup is active (hourly cron from RUNBOOK section 10) and a restore was tested once
- [ ] Alerts arrive (webhook / log) — test with `make pause` then `make resume`
- [ ] Someone is reachable during the first live hours and around the endgame

## 4. Start

- [ ] `CONFIRM_LIVE=YES make live` started at ________ (UTC) by ________
- [ ] `siavash status` shows breaker NORMAL and the expected equity
