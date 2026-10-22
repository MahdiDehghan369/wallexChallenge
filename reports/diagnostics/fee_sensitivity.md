# Fee sensitivity — baseline strategies, with and without the ML filter

Dev period only (hold-out excluded). For each round-trip fee (maker = taker = rt / 2, plus the assumed slippage on taker legs) the T-402 dataset is **re-labelled** (TIME exits flip with the fee) and the T-403 LightGBM walk-forward (45 d train / 12 h embargo / 7 d test, `strategy.yaml > ml`) is re-run.

- **all candidates**: every S1/S2/S3 candidate (the ML dataset; overlapping, no filters)
- **live rules, no ML**: fee filter (`fee_multiple` × rt) + regime filter + one open position per symbol
- **ML θ (in-sample)**: best θ of the T-404 table — θ is chosen on the same OOF predictions it is scored on (optimistic)
- **ML nested**: in fold k the threshold is chosen on folds < k only (honest); compare with **no ML, same folds**

| RT fee % | candidates | mean net all % | label=1 % | live rules: trades | mean % | cum % | mean OOF AUC | ML θ | θ trades | θ mean % | no ML same folds: n | mean % | ML nested: n | mean % | cum % | max DD % |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.0 | 5881 | 0.104 | 56.9 | 2820 | 0.090 | 253.3 | 0.623 | 0.650 | 572 | 0.358 | 3013 | 0.115 | 2107 | 0.220 | 463.5 | 60.6 |
| 0.2 | 5881 | -0.096 | 53.8 | 2394 | -0.079 | -189.8 | 0.617 | 0.650 | 494 | 0.259 | 3013 | -0.085 | 560 | 0.197 | 110.0 | 22.1 |
| 0.3 | 5881 | -0.196 | 52.6 | 1723 | -0.116 | -199.9 | 0.617 | 0.700 | 270 | 0.130 | 3013 | -0.185 | 372 | 0.145 | 54.1 | 25.4 |
| 0.4 | 5881 | -0.296 | 51.1 | 1267 | -0.149 | -188.4 | 0.604 | 0.650 | 229 | 0.190 | 3013 | -0.285 | 372 | -0.004 | -1.5 | 43.2 |
| 0.5 | 5881 | -0.396 | 50.3 | 971 | -0.213 | -207.2 | 0.607 | 0.675 | 275 | 0.013 | 3013 | -0.385 | 433 | -0.120 | -52.0 | 85.0 |
| 0.7 | 5881 | -0.596 | 48.5 | 106 | -0.196 | -20.8 | 0.610 | 0.675 | 209 | -0.258 | 3013 | -0.585 | 392 | -0.320 | -125.3 | 132.7 |
