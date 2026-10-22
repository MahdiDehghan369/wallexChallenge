# Variant profiles — walk-forward on the dev period

Profiles: `config/profiles/*.yaml` (the live `config/strategy.yaml` is not modified). **5 profiles expanded to 14 variants** (grid points count as separate variants). Dev period 2026-06-25 → 2026-10-02 in 8 windows of 14 days; the last 20 days are the hold-out (see `holdout.md`).

Every number is a **live-like trade**: fee filter (`fee_multiple` × rt), the profile's regime filter, one open position per variant × symbol, net of fees (maker = taker = rt / 2) and of the assumed slippage on taker legs. Families with a grid are walk-forward: window w trades the variant that was best on windows < w (≥ 10 past trades). The OOS columns cover windows 1…7 (85 days) for every family, so families are comparable; window 0 is only used for learning.

- **a_h1_breakout** (9 variants): 1h close above the previous 20×1h high with 1h volume z > 1, 4h EMA20 > EMA50 and 4h ADX > 20; TP 3–5 %, SL 1.5–2.5 %, 48 h time stop, taker entry
- **b_maker** (1 variants): baseline S1/S2/S3 signals; resting limit entry at the signal close (filled only if traded through within 30 min), resting limit TP, taker SL / time stop
- **baseline** (1 variants): current strategy.yaml (S1 + S2 + S3 on 15m, taker entry at the next open)
- **c_s3_strict** (2 variants): S3 implied deviation only, z_entry 2.5 or 3.0 (other S3 parameters as in strategy.yaml)
- **d_trend_top3** (1 variants): long-only trend following on the top 3 TMN markets by turnover: 1h and 4h EMA20 > EMA50, 1h close above EMA20 and up, 1h ADX > 20; SL 2×ATR(1h), trailing stop 3×ATR, TP 10×ATR, 72 h time stop

## Round-trip fee 0.0 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 284 | 3.33 | 47.2 | 0.442 | 125.5 | 51.7 | 3/7 | tp_pct=5,sl_pct=2, tp_pct=4,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 1379 | 16.18 | 44.5 | -0.088 | -120.9 | 135.0 | 2/7 | — |
| baseline | 1 | 2441 | 28.63 | 57.9 | 0.095 | 231.8 | 48.6 | 6/7 | — |
| c_s3_strict | 2 | 796 | 9.34 | 63.4 | 0.194 | 154.3 | 18.5 | 7/7 | z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5 |
| d_trend_top3 | 1 | 107 | 1.26 | 46.7 | 0.607 | 64.9 | 15.9 | 3/7 | — |

## Round-trip fee 0.2 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 282 | 3.31 | 47.2 | 0.261 | 73.5 | 58.5 | 3/7 | tp_pct=5,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 1143 | 13.41 | 39.4 | -0.296 | -337.8 | 347.8 | 1/7 | — |
| baseline | 1 | 2055 | 24.11 | 53.0 | -0.074 | -152.0 | 189.5 | 3/7 | — |
| c_s3_strict | 2 | 672 | 7.88 | 57.0 | -0.010 | -6.9 | 35.3 | 3/7 | z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=2.5, z_entry=3, z_entry=2.5 |
| d_trend_top3 | 1 | 107 | 1.26 | 43.0 | 0.407 | 43.5 | 21.4 | 3/7 | — |

## Round-trip fee 0.3 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 282 | 3.31 | 46.5 | 0.161 | 45.3 | 61.9 | 3/7 | tp_pct=5,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 797 | 9.35 | 36.4 | -0.401 | -319.4 | 328.6 | 1/7 | — |
| baseline | 1 | 1479 | 17.35 | 51.5 | -0.113 | -167.7 | 200.2 | 2/7 | — |
| c_s3_strict | 2 | 297 | 3.48 | 53.5 | -0.084 | -24.9 | 30.6 | 2/7 | z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3 |
| d_trend_top3 | 1 | 107 | 1.26 | 42.1 | 0.307 | 32.8 | 24.2 | 3/7 | — |

## Round-trip fee 0.4 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 282 | 3.31 | 46.1 | 0.061 | 17.1 | 71.6 | 3/7 | tp_pct=5,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 572 | 6.71 | 34.4 | -0.497 | -284.1 | 285.8 | 0/7 | — |
| baseline | 1 | 1097 | 12.87 | 50.3 | -0.152 | -166.9 | 191.2 | 2/7 | — |
| c_s3_strict | 2 | 249 | 2.92 | 49.8 | -0.177 | -44.0 | 49.0 | 2/7 | z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3 |
| d_trend_top3 | 1 | 107 | 1.26 | 41.1 | 0.207 | 22.1 | 27.7 | 3/7 | — |

## Round-trip fee 0.5 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 282 | 3.31 | 45.0 | -0.039 | -11.1 | 85.0 | 3/7 | tp_pct=5,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 405 | 4.75 | 33.1 | -0.568 | -229.9 | 232.0 | 1/7 | — |
| baseline | 1 | 840 | 9.85 | 49.5 | -0.208 | -174.5 | 199.3 | 2/7 | — |
| c_s3_strict | 2 | 213 | 2.50 | 49.3 | -0.271 | -57.8 | 63.9 | 2/7 | z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3, z_entry=3 |
| d_trend_top3 | 1 | 107 | 1.26 | 39.3 | 0.107 | 11.4 | 31.5 | 2/7 | — |

## Round-trip fee 0.7 %

| family | variants | OOS trades | trades/day | win % | OOS mean net % | OOS cum % | max DD % | windows > 0 | picks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| a_h1_breakout | 9 | 281 | 3.30 | 43.4 | -0.252 | -70.9 | 112.4 | 2/7 | tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=4,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5, tp_pct=5,sl_pct=2.5 |
| b_maker | 1 | 56 | 0.66 | 23.2 | -0.909 | -50.9 | 61.4 | 2/7 | — |
| baseline | 1 | 101 | 1.18 | 48.5 | -0.123 | -12.5 | 56.6 | 2/7 | — |
| c_s3_strict | 2 | 0 | 0.00 | — | — | 0.0 | 0.0 | 0/7 | none, none, none, none, none, none, none |
| d_trend_top3 | 1 | 107 | 1.26 | 37.4 | -0.093 | -10.0 | 39.3 | 2/7 | — |

## Every variant, whole dev period (in-sample — for reference, not for selection)

Mean net % per trade [trades] at each round-trip fee; `gross` = before fees and slippage (fee filter off); `drift-adj.` = gross − market drift × bars held (see `edge_breakdown.md`); `hold h` = mean hours held.

| variant | gross mean % [n] | drift-adj. % | hold h | RT 0.0 % | RT 0.2 % | RT 0.3 % | RT 0.4 % | RT 0.5 % | RT 0.7 % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| a_h1_breakout[tp_pct=3,sl_pct=1.5] | 0.255 [381] | -0.113 | 10.8 | 0.064 [381] | -0.136 [381] | -0.236 [381] | -0.336 [381] | -0.436 [381] | -0.636 [381] |
| a_h1_breakout[tp_pct=3,sl_pct=2] | 0.210 [375] | -0.249 | 13.2 | 0.024 [375] | -0.176 [375] | -0.276 [375] | -0.376 [375] | -0.476 [375] | -0.676 [375] |
| a_h1_breakout[tp_pct=3,sl_pct=2.5] | 0.295 [360] | -0.263 | 15.9 | 0.115 [360] | -0.085 [360] | -0.185 [360] | -0.285 [360] | -0.385 [360] | -0.585 [360] |
| a_h1_breakout[tp_pct=4,sl_pct=1.5] | 0.261 [356] | -0.201 | 13.3 | 0.061 [356] | -0.139 [356] | -0.239 [356] | -0.339 [356] | -0.439 [356] | -0.639 [356] |
| a_h1_breakout[tp_pct=4,sl_pct=2] | 0.234 [347] | -0.340 | 16.3 | 0.040 [347] | -0.160 [347] | -0.260 [347] | -0.360 [347] | -0.460 [347] | -0.660 [347] |
| a_h1_breakout[tp_pct=4,sl_pct=2.5] | 0.372 [331] | -0.317 | 19.5 | 0.184 [331] | -0.016 [331] | -0.116 [331] | -0.216 [331] | -0.316 [331] | -0.516 [331] |
| a_h1_breakout[tp_pct=5,sl_pct=1.5] | 0.431 [336] | -0.100 | 15.1 | 0.227 [336] | 0.027 [336] | -0.073 [336] | -0.173 [336] | -0.273 [336] | -0.473 [336] |
| a_h1_breakout[tp_pct=5,sl_pct=2] | 0.431 [324] | -0.230 | 18.6 | 0.231 [324] | 0.031 [324] | -0.069 [324] | -0.169 [324] | -0.269 [324] | -0.469 [324] |
| a_h1_breakout[tp_pct=5,sl_pct=2.5] | 0.551 [307] | -0.245 | 22.2 | 0.355 [307] | 0.155 [307] | 0.055 [307] | -0.045 [307] | -0.145 [307] | -0.345 [307] |
| b_maker | -0.031 [2049] · fill 50.1 % | -0.089 | 1.6 | -0.083 [1609] | -0.288 [1346] | -0.381 [930] | -0.485 [659] | -0.566 [468] | -1.037 [61] |
| baseline | 0.215 [3393] | 0.155 | 1.6 | 0.092 [2821] | -0.078 [2395] | -0.111 [1723] | -0.158 [1266] | -0.215 [971] | -0.196 [106] |
| c_s3_strict[z_entry=2.5] | 0.386 [909] | 0.321 | 1.7 | 0.212 [909] | 0.025 [850] | -0.044 [691] | -0.118 [534] | -0.187 [453] | — [0] |
| c_s3_strict[z_entry=3] | 0.416 [384] | 0.352 | 1.7 | 0.240 [384] | 0.044 [378] | -0.048 [337] | -0.141 [283] | -0.232 [242] | — [0] |
| d_trend_top3 | 0.709 [117] | 0.013 | 22.9 | 0.559 [117] | 0.359 [117] | 0.259 [117] | 0.159 [117] | 0.059 [117] | -0.141 [117] |

## d_trend_top3 by market (OOS windows 1…7, single variant)

| market | RT 0.0 %: mean [n] | RT 0.2 %: mean [n] | RT 0.3 %: mean [n] | RT 0.4 %: mean [n] | RT 0.5 %: mean [n] | RT 0.7 %: mean [n] |
|---|---:|---:|---:|---:|---:|---:|
| BTC/TMN | 0.774 [36] | 0.574 [36] | 0.474 [36] | 0.374 [36] | 0.274 [36] | 0.074 [36] |
| ETH/TMN | 0.313 [39] | 0.113 [39] | 0.013 [39] | -0.087 [39] | -0.187 [39] | -0.387 [39] |
| USDT/TMN | 0.778 [32] | 0.578 [32] | 0.478 [32] | 0.378 [32] | 0.278 [32] | 0.078 [32] |

## Benchmark: buy and hold over the same OOS windows (2026-07-09 → 2026-10-02)

Price change of each market (TMN quote; part of any long-only TMN return is toman depreciation). One round trip of fees only, so this is the cost-free reference a long-only variant must beat.

| market | buy & hold % |
|---|---:|
| LINK/TMN | 169.26 |
| AVAX/TMN | 143.99 |
| LTC/TMN | 130.29 |
| SOL/TMN | 124.59 |
| ETH/TMN | 124.07 |
| ADA/TMN | 115.74 |
| DOT/TMN | 112.49 |
| XRP/TMN | 99.09 |
| BTC/TMN | 98.40 |
| SHIB/TMN | 96.63 |
| BNB/TMN | 96.30 |
| BCH/TMN | 91.87 |
| DOGE/TMN | 88.61 |
| TRX/TMN | 45.19 |
| USDT/TMN | 43.08 |
| **equal-weight mean** | **105.31** |

