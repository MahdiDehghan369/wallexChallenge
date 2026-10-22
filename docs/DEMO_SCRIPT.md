# Demo video script (T-1304) — 6:45

Recording setup: terminal (large font) + browser with `reports/live/dashboard.html` and the reports.
Before recording: `make report`, `make dashboard`, and pick one real trade and one LLM rejection
(commands in scenes 3 and 4). Upload **before the deadline** and put the link in README.md.

| # | time | scene | on screen |
|---|---|---|---|
| 1 | 0:00–0:45 | Problem and thesis | README top, `docs/STRATEGY.md` section 1 |
| 2 | 0:45–1:45 | Architecture | README architecture diagram, `config/` files |
| 3 | 1:45–3:15 | One real trade end to end | `jq` on `logs/decisions.jsonl`, `orders.jsonl`, `fills.csv` |
| 4 | 3:15–4:00 | An LLM rejection + counterfactual | decision record, `reports/live/REPORT.md` counterfactual table |
| 5 | 4:00–4:45 | Risk and breakers | `config/risk.yaml`, `siavash status`, breaker records |
| 6 | 4:45–5:30 | Backtest / shadow / comparison | walk-forward report, consistency report |
| 7 | 5:30–6:15 | Live results and judge indicators | `reports/live/REPORT.md`, dashboard |
| 8 | 6:15–6:45 | Reproduce it | `make replay` |

## 1. Problem and thesis (45 s)

"Spot-only bot on Wallex TMN markets, 5 M TMN. Quant signals propose, AI filters, a hard risk layer decides.
Every decision is logged with a reason and can be replayed."

```bash
head -12 README.md
```

## 2. Architecture (60 s)

Walk through the diagram: candle → features → S1/S2/S3 → fee filter → ML → LLM → RiskManager → Router →
fills → exits. One Orchestrator for live, paper, backtest and replay. All parameters in `config/`.

```bash
ls config && grep -n "risk_per_trade_pct\|kill_switch" config/risk.yaml
```

## 3. One real trade (90 s)

```bash
# the first placed entry, with signal, ML, LLM and risk sections
jq -c 'select(.type=="ENTRY_CANDIDATE" and .outcome_action=="ORDER_PLACED")' logs/decisions.jsonl | head -1 | jq .
P=$(jq -r 'select(.type=="ENTRY_CANDIDATE" and .outcome_action=="ORDER_PLACED") | .position_id' logs/decisions.jsonl | head -1)
jq -c --arg p "$P" 'select(.position_id==$p)' logs/decisions.jsonl | jq '{type, ts, exit_reason, final_reason}'
grep "$P" logs/fills.csv
```

Point at: `signal.conditions` (why the strategy fired), `ml.p_win` + `top_features`, `llm_review.verdict`
and `size_multiplier`, `risk` (size, checks), then the order ids, the fills and the EXIT record with its reason.

## 4. LLM rejection and counterfactual (45 s)

```bash
jq -c 'select(.rejected_at=="LLM")' logs/decisions.jsonl | head -1 | jq '{ts, symbol, strategy, llm_review, final_reason}'
grep -A6 "Counterfactual" reports/live/REPORT.md
```

"Of N candidates the LLM rejected, X would have lost money, Y would have won" — the AI changes decisions.

## 5. Risk and breakers (45 s)

```bash
siavash status
jq -c 'select(.type=="BREAKER")' logs/decisions.jsonl | tail -3 | jq '{ts, final_reason}'
```

Show: 0.5 % risk per trade, 20 % per position, 50 % exposure, daily loss 2 %, kill-switch 5 %, cooldowns,
endgame timeline, SymbolGuard (spot TMN only, denylist).

## 6. Backtest, shadow, comparison (45 s)

```bash
sed -n '1,25p' reports/backtest/walkforward/REPORT.md
sed -n '1,20p' reports/consistency.md
```

Same engine for all; walk-forward windows, sensitivity heatmaps, rule-only vs +ML vs +LLM, honest
recommendation; backtest ↔ shadow agreement and real vs assumed slippage.

## 7. Live results (45 s)

```bash
make report
sed -n '1,25p' reports/live/REPORT.md
```

Open `reports/live/dashboard.html`. Read the judge indicators: Number of Trades, Trading Volume, Average
Trade Volume, Net Return %, Max Drawdown %, trades per day, API error rate, latency, AI statistics.

## 8. Reproduce it (30 s)

```bash
make replay ARGS="--logs logs --live-data data/live"
head -8 reports/replay.md
```

"Same data + cached LLM answers → the same decisions. Judges can run it without API keys."
