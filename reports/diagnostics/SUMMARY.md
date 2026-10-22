# Edge diagnostics: summary and recommendation

Diagnostic only: `config/strategy.yaml`, `config/risk.yaml` and the live code paths were not changed.
Data: real Wallex 15m candles, 2026-06-24 → 2026-10-22 (120 days), 15 allowed TMN markets.
Reproduce with `make diagnostics`. The hold-out was evaluated once, so a second `ARGS=--holdout` run refuses.

## Bottom line

**No robust edge found.** None of the 14 variants is profitable after realistic costs in both the
walk-forward out-of-sample windows of the dev period and the 20-day hold-out. That includes the current
strategies, the ML filter and the four new profiles. The results that look profitable fall into two groups:
- the long-only trend variants (a, d) only rode the rally in TMN prices;
- the ML filter is positive only at round-trip fees of 0.3 % or less, and on the hold-out only at 0.2 % or less, where it is marginal.

| Question | Answer |
|---|---|
| Is there a gross edge? | Small. S1/S2/S3 combined: **+0.22 %** per trade before fees and slippage; **+0.16 %** after removing market drift (3,542 dev trades). |
| What eats it? | The assumed spread and slippage alone (~0.17 % per round trip) leave **+0.05 %**. That +0.05 % is the break-even round-trip *fee*: any fee above it loses. |
| Which strategy carries it? | **S3 implied deviation** (+0.35 % gross, +0.29 % drift-adjusted, break-even fee after slippage 0.18 %) and S2 (+0.43 % / +0.34 % / 0.24 %, only 389 trades, median negative). **S1 trend breakout has no edge** (−0.06 % gross). |
| Fee sensitivity | Live rules (fee filter, regime filter, one position per market) are positive only at **0 % fee** (+0.09 %/trade). At 0.2 % fee: −0.08 %; at 0.4 %: −0.15 %; at 0.7 %: −0.20 %. |
| Does the ML filter fix it? | Only at low fees. Nested (honest) threshold on dev: +0.20 % at 0.2 % fee, +0.15 % at 0.3 %, −0.00 % at 0.4 %. Hold-out: +0.03 % at 0.2 %, −0.07 % at 0.3 %, −0.13 % at 0.4 %. |
| Zero-fee markets? | **None allowed.** 1 of 469 markets has `is_zero_fee: true`: USDS/USDT, which is USDT-quoted and on the denylist. |
| Best new variant | None. Variants a and d were positive on dev up to 0.4–0.5 % fee, but their edge is market drift (d: drift-adjusted +0.01 %; a: negative for all 9 grid points) and both **lost on the hold-out at every fee, 0 % included**. |

## 1. Edge breakdown ([edge_breakdown.md](edge_breakdown.md))

All figures are dev-period trades, gross (no fees, no slippage), with one open position per market × strategy.

- **By strategy:**
  - S3: 1,967 trades, 71.6 % win, +0.349 %, t = 18.6.
  - S2: 389 trades, 46.5 % win, +0.432 %, median −0.12 %.
  - S1: 1,186 trades, 40.6 % win, −0.055 %.
- **By market:** gross edge is highest in LTC, AVAX and BCH (+0.37 % to +0.50 %). It is lowest in USDT/TMN (+0.02 %), XRP, BTC and TRX (≈ +0.12 %). After the assumed slippage, 4 markets (DOGE, TRX, XRP, USDT) are ≤ 0 before any fee.
- **By regime:** RANGE +0.27 %, HIGH_VOL +0.24 %, TREND_UP +0.14 %. After slippage, TREND_UP is negative before fees.
- **By hour of day:** no stable pattern. 23 of 24 hours are positive gross, and the differences are within noise (n = 90–207 per hour). This is not a usable filter.
- **By exit reason:** TP trades average +0.87 % and SL trades −0.70 %. A 1.6 h average hold leaves little room for costs.

## 2. Fee sensitivity ([fee_sensitivity.md](fee_sensitivity.md))

Re-labelling was done per fee. In taker mode the simulator is unit-tested to give the same result as the T-401 labeller. The LightGBM walk-forward was re-run for every fee.

| RT fee | live rules, no ML: mean net / trade [n] | ML nested (honest) [n] | no ML, same folds | ML on hold-out [n] |
|---:|---:|---:|---:|---:|
| 0.0 % | +0.090 % [2,820] | +0.220 % [2,107] | +0.115 % | +0.084 % [760] |
| 0.2 % | −0.079 % [2,394] | +0.197 % [560] | −0.085 % | +0.031 % [185] |
| 0.3 % | −0.116 % [1,723] | +0.145 % [372] | −0.185 % | −0.065 % [223] |
| 0.4 % | −0.149 % [1,267] | −0.004 % [372] | −0.285 % | −0.132 % [89] |
| 0.5 % | −0.213 % [971] | −0.120 % [433] | −0.385 % | −0.079 % [114] |
| 0.7 % | −0.196 % [106] | −0.320 % [392] | −0.585 % | −0.319 % [116] |

- The ML filter does rank candidates: OOF AUC is 0.60–0.62 and it adds +0.1 to +0.3 % per trade over no filter.
- It cannot turn the strategies positive once the round-trip fee is above ~0.3 %.
- The ML numbers are per *candidate*, which overlap, so they are not live trades.
- The optimistic in-sample θ column of the T-404 method is in the report for comparison only.

## 3. Zero-fee markets ([zero_fee_markets.md](zero_fee_markets.md))

The source is `/hector/web/v1/markets` (public), all 469 markets, saved as `docs/vendor/wallex/samples/hector_markets_flags.json`. The only zero-fee market is USDS/USDT, which is excluded twice: its quote is not TMN, and it is on the denylist. **No allowed spot TMN market has zero fee.**

## 4. Variant profiles ([variants.md](variants.md), [holdout.md](holdout.md))

All variants are live-like trades: fee filter, the profile's regime filter, one position per variant × market, fees plus slippage on taker legs. "OOS" is the sum over dev windows 1–7 (85 days). Grid families are picked walk-forward from earlier windows only.

| profile | variants | OOS mean @ 0.2 % | OOS mean @ 0.4 % | drift-adj. gross | hold-out @ 0.0 % | hold-out @ 0.4 % | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline (current strategy.yaml) | 1 | −0.074 % | −0.152 % | +0.155 % | +0.072 % | −0.221 % | no edge after costs |
| a) 1h breakout + 4h filter, TP 3–5 %, SL 1.5–2.5 % | 9 | +0.261 % | +0.061 % | −0.10 to −0.34 % | −0.867 % | −1.267 % | rally only, fails hold-out |
| b) maker entries + maker TP | 1 | −0.296 % | −0.497 % | −0.089 % | −0.057 % | −0.425 % | **worse** than taker (adverse selection) |
| c) S3 only, z 2.5 / 3.0 | 2 | −0.010 % | −0.177 % | +0.32 / +0.35 % | +0.093 % | −0.379 % | real gross edge, smaller than costs |
| d) long-only trend, top-3 liquid (BTC, ETH, USDT /TMN) | 1 | +0.407 % | +0.207 % | +0.013 % | −0.305 % | −0.705 % | rally only, fails hold-out |

- **Market context matters.** Over the dev OOS windows, buy and hold returned +43 % (USDT/TMN) to +169 % (LINK/TMN), +105 % equal-weight: toman depreciation plus a crypto rally. Over the hold-out it returned −6.9 % equal-weight. Any long-only TMN result from the dev period has to be read against that.
- **Maker variant (b):** only 50 % of resting entries fill, and the fills are mostly the setups where price kept falling. That costs ~0.17 % per trade compared with taker entry, which is more than the slippage it saves. Maker would only pay off with a much lower maker fee than taker fee, which is unknown until T-108.
- **S3 stricter z (c):** z = 3.0 slightly beats 2.5 gross. But the TP cap of 1.5 % means the fee filter removes every S3 trade at 0.7 % fee, and at 0.3–0.5 % the edge is below costs.

## 5. Overfitting control and number of trials

- **Variants tried: 14** (5 profiles; grid points counted separately). Each was evaluated at 6 fees, giving 84 variant × fee results, plus 6 ML walk-forwards. No profile or parameter was changed after seeing results.
  - Added after seeing dev results, as reporting only: the drift-adjusted column and the buy-and-hold benchmark.
  - Written before the hold-out ran: [PREREGISTRATION.md](PREREGISTRATION.md).
- **Walk-forward only:** windows of 14 days; selections (grid points, ML threshold) come from earlier windows or folds only. Dev setups whose time-stop window reaches into the hold-out are purged.
- **Hold-out:** 2026-10-02 06:00 → 2026-10-22 06:00 UTC, evaluated once (`holdout_evaluated.json`). With 14 variants, a single positive result would have needed to survive this. None did at a fee above 0.2 %.
- **Caveat on earlier project work:** T-404 (`ml.threshold: 0.675`) and the background walk-forward (T-904) used all 120 days, including these 20. Nothing in this diagnostic was tuned on them, but those earlier reports are not hold-out clean.

## Limitations

- Slippage and spread are the `base.bt` assumptions (0.03–0.15 % spread + 0.05 %), not measured. The real values (shadow T-1002 consistency report) can move every number above by ~±0.1 %.
- Maker fills are modelled pessimistically (a fill needs a trade strictly through the price; no TP on the fill bar). There is no portfolio-level position cap, and returns are additive percentages per trade, not an equity curve.
- 99 dev days in one market regime (a strong rally) and 20 hold-out days in another (a decline). This is too little data to rule out an edge in other conditions, and too little to claim one.

## Recommendation

1. **Do not treat any variant as profitable, and do not adopt a profile into `strategy.yaml`.** Keep `ml.enabled: false`. Do not switch to maker entries.
2. **Get the real numbers first (T-108):** the account fee from `/v1/account/fee`, and real spreads from shadow trading.
   - The decision rule is simple. The strategies need a round-trip fee **plus real slippage below ~0.2 %**, or ~0.3 % with the ML filter, and even then the hold-out was only marginally positive.
   - With the current 0.35 / 0.35 % assumption (0.7 % round trip), the fee filter already blocks almost everything. That is the correct behaviour.
3. **If you trade the challenge anyway, treat it as risk and cost control, not alpha:**
   - Keep the current hard limits and the fee filter, which automatically removes trades that cannot cover the fee.
   - Use minimum size.
   - The least-bad signal is S3 on narrow-spread markets.
   - Expect roughly break-even to slightly negative results before luck.
4. **Next honest test:** the hold-out is now used up. The next out-of-sample data is what accumulates from 2026-10-22 on (shadow / paper). After T-108, set the real fees in `risk.yaml` and re-run `make diagnostics` on new data. Judge only the new days.
