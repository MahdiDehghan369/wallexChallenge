# Final hold-out (evaluated once)

Hold-out: setups from 2026-10-02 06:00 to 2026-10-22 06:00 UTC (20 days, never used before this run). For each family the variant is the one with the best cumulative net return over the **whole dev period** at the same fee; the ML filter is the final model (fit on all dev candidates) with the top-q threshold chosen on the dev OOF predictions. Setups whose time-stop window runs past the data end are dropped.

| RT fee % | family | variant | trades | trades/day | win % | mean net % | cum % | max DD % |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 0.0 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 29.4 | -0.867 | -29.5 | 31.6 |
| 0.0 | b_maker | — | 292 | 14.60 | 45.9 | -0.057 | -16.7 | 21.8 |
| 0.0 | baseline | — | 473 | 23.65 | 55.0 | 0.072 | 34.1 | 19.5 |
| 0.0 | c_s3_strict | z_entry=2.5 | 156 | 7.80 | 57.1 | 0.093 | 14.5 | 11.8 |
| 0.0 | d_trend_top3 | — | 18 | 0.90 | 27.8 | -0.305 | -5.5 | 9.5 |
| 0.2 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 26.5 | -1.067 | -36.3 | 38.0 |
| 0.2 | b_maker | — | 238 | 11.90 | 37.0 | -0.257 | -61.3 | 63.9 |
| 0.2 | baseline | — | 390 | 19.50 | 48.7 | -0.098 | -38.2 | 44.3 |
| 0.2 | c_s3_strict | z_entry=2.5 | 148 | 7.40 | 48.6 | -0.102 | -15.2 | 22.7 |
| 0.2 | d_trend_top3 | — | 18 | 0.90 | 27.8 | -0.505 | -9.1 | 12.1 |
| 0.3 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 26.5 | -1.167 | -39.7 | 41.2 |
| 0.3 | b_maker | — | 176 | 8.80 | 31.8 | -0.370 | -65.1 | 66.7 |
| 0.3 | baseline | — | 288 | 14.40 | 45.5 | -0.174 | -50.1 | 52.9 |
| 0.3 | c_s3_strict | z_entry=3 | 70 | 3.50 | 40.0 | -0.272 | -19.1 | 22.4 |
| 0.3 | d_trend_top3 | — | 18 | 0.90 | 27.8 | -0.605 | -10.9 | 13.4 |
| 0.4 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 26.5 | -1.267 | -43.1 | 44.4 |
| 0.4 | b_maker | — | 131 | 6.55 | 31.3 | -0.425 | -55.7 | 56.6 |
| 0.4 | baseline | — | 223 | 11.15 | 45.3 | -0.221 | -49.3 | 54.5 |
| 0.4 | c_s3_strict | z_entry=3 | 61 | 3.05 | 37.7 | -0.379 | -23.1 | 25.7 |
| 0.4 | d_trend_top3 | — | 18 | 0.90 | 27.8 | -0.705 | -12.7 | 14.8 |
| 0.5 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 26.5 | -1.367 | -46.5 | 47.6 |
| 0.5 | b_maker | — | 99 | 4.95 | 34.3 | -0.463 | -45.9 | 47.4 |
| 0.5 | baseline | — | 170 | 8.50 | 44.1 | -0.280 | -47.7 | 53.9 |
| 0.5 | c_s3_strict | z_entry=3 | 57 | 2.85 | 36.8 | -0.475 | -27.1 | 29.4 |
| 0.5 | d_trend_top3 | — | 18 | 0.90 | 27.8 | -0.805 | -14.5 | 16.3 |
| 0.7 | a_h1_breakout | tp_pct=5,sl_pct=2.5 | 34 | 1.70 | 26.5 | -1.567 | -53.3 | 54.0 |
| 0.7 | b_maker | — | 19 | 0.95 | 31.6 | -0.398 | -7.6 | 10.3 |
| 0.7 | baseline | — | 34 | 1.70 | 44.1 | -0.185 | -6.3 | 16.0 |
| 0.7 | c_s3_strict | z_entry=2.5 | 0 | 0.00 | — | — | 0.0 | 0.0 |
| 0.7 | d_trend_top3 | — | 18 | 0.90 | 16.7 | -1.016 | -18.3 | 19.5 |

### d_trend_top3 by market (hold-out)

| RT fee % | market | trades | mean net % | cum % |
|---:|---|---:|---:|---:|
| 0.0 | BTC/TMN | 6 | -0.206 | -1.2 |
| 0.0 | ETH/TMN | 2 | -0.866 | -1.7 |
| 0.0 | USDT/TMN | 10 | -0.251 | -2.5 |
| 0.2 | BTC/TMN | 6 | -0.406 | -2.4 |
| 0.2 | ETH/TMN | 2 | -1.066 | -2.1 |
| 0.2 | USDT/TMN | 10 | -0.451 | -4.5 |
| 0.3 | BTC/TMN | 6 | -0.506 | -3.0 |
| 0.3 | ETH/TMN | 2 | -1.166 | -2.3 |
| 0.3 | USDT/TMN | 10 | -0.551 | -5.5 |
| 0.4 | BTC/TMN | 6 | -0.606 | -3.6 |
| 0.4 | ETH/TMN | 2 | -1.266 | -2.5 |
| 0.4 | USDT/TMN | 10 | -0.651 | -6.5 |
| 0.5 | BTC/TMN | 6 | -0.706 | -4.2 |
| 0.5 | ETH/TMN | 2 | -1.366 | -2.7 |
| 0.5 | USDT/TMN | 10 | -0.751 | -7.5 |
| 0.7 | BTC/TMN | 6 | -0.906 | -5.4 |
| 0.7 | ETH/TMN | 2 | -1.566 | -3.1 |
| 0.7 | USDT/TMN | 10 | -0.971 | -9.7 |

### Benchmark: buy and hold over the hold-out

| market | buy & hold % |
|---|---:|
| USDT/TMN | 3.78 |
| TRX/TMN | 2.36 |
| BTC/TMN | -0.18 |
| BNB/TMN | -1.17 |
| DOT/TMN | -4.32 |
| ETH/TMN | -7.34 |
| BCH/TMN | -8.01 |
| XRP/TMN | -8.65 |
| ADA/TMN | -10.21 |
| SOL/TMN | -10.49 |
| LTC/TMN | -10.62 |
| AVAX/TMN | -11.38 |
| SHIB/TMN | -11.80 |
| LINK/TMN | -12.20 |
| DOGE/TMN | -13.70 |
| **equal-weight mean** | **-6.93** |

## ML filter on the hold-out (baseline candidates, no fee / regime filter, overlapping)

| RT fee % | candidates | mean net no ML % | keep q | kept | mean net ML % | cum ML % |
|---:|---:|---:|---:|---:|---:|---:|
| 0.0 | 997 | 0.001 | 0.7 | 760 | 0.084 | 64.1 |
| 0.2 | 997 | -0.199 | 0.2 | 185 | 0.031 | 5.7 |
| 0.3 | 997 | -0.299 | 0.2 | 223 | -0.065 | -14.5 |
| 0.4 | 997 | -0.399 | 0.1 | 89 | -0.132 | -11.7 |
| 0.5 | 997 | -0.499 | 0.1 | 114 | -0.079 | -9.0 |
| 0.7 | 997 | -0.699 | 0.1 | 116 | -0.319 | -37.0 |
