# AGENTS.md — Siavash Wallex Trading Agent

## Source of truth
WALLEX_AGENT_PLAN.md (repo root). Before any work read its sections:
"دستورالعمل اجرایی برای Coding Agent", 1, 3, 4, 5, 7, 8 (incl. 8.3 Contracts), 9.4.
Then read the full text of the task(s) you will implement in section 10.

## How to pick work
- Prompt names a task ID -> do only that task.
- Prompt says OWNER=A or OWNER=B -> autopilot, only that owner's tasks.
- Otherwise -> autopilot over all AGENT / AGENT+HUMAN tasks.
Autopilot: open PROGRESS.md, take the first TODO task in table 9.4 order whose
dependencies are DONE; implement; run its acceptance commands + `make lint test`;
mark DONE and commit "T-xxx: <title>"; repeat. Run `python scripts/check_progress.py`
to list next available tasks (after T-010 exists).
HUMAN tasks -> mark WAITING_HUMAN with exact instructions, continue with others.
Stuck after 3 serious attempts -> mark BLOCKED with the error, continue with others.
Running out of context -> leave the repo green, update PROGRESS.md, stop.

## Absolute rules
1. Never enable MODE=live, never place real orders, never run scripts/smoke_test.py or `make live`.
2. Never guess Wallex endpoints/fields. Use only src/siavash/exchange/endpoints.py and
   docs/wallex_api_notes.md. Unknown -> mock it, note it in PROGRESS.md.
3. Spot only. No futures/margin/leverage/withdraw/transfer/deposit code paths.
4. Every order passes SymbolGuard inside the broker/client layer and carries a decision_id.
5. The LLM never places orders and can only lower risk (size_multiplier <= 1).
6. No hardcoded parameters: everything from config/*.yaml.
7. Do not raise limits in config/risk.yaml above the plan's section 5 values.
8. Do not change src/siavash/core/* unless unavoidable; if you do, fix all users,
   keep tests green, and add "CONTRACT CHANGE:" to CHANGELOG.md.
9. Never log or commit secrets (.env, API keys).
10. No lookahead: features/signals at time t only use data closed at or before t.

## Conventions
- Python 3.11, full type hints, pydantic v2, async I/O with httpx, structlog JSON logs.
- UTC everywhere internally; Tehran time only for "trading day" boundaries in reports/breakers.
- Decimal for prices/qty/money in exchange & accounting layers; float in features/ML.
- Canonical symbols "BASE/QUOTE" (e.g. BTC/TMN); exchange format only inside exchange/.
- Unit tests in tests/unit (no network). Live tests in tests/integration with @pytest.mark.live.
- Use tests/fakes (FakeClock, InMemoryMarketData, FakeBroker, synthetic data) instead of network.

## Definition of done (every task)
1. All acceptance items of the task pass; `make lint` and `make test` pass.
2. Only the task's output files (plus tests, PROGRESS.md, CHANGELOG.md if needed) changed.
3. Docs touched by the change are updated.
4. PROGRESS.md updated; one commit "T-xxx: <title>".
