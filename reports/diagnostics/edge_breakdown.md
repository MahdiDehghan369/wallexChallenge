# Edge breakdown — gross returns before fees and slippage

Data: real 15m candles 2026-06-24 → 2026-10-02 (dev period; the last 20 days are a hold-out and are **not** in this report). Strategies S1/S2/S3 with the current `config/strategy.yaml`, every TMN market of the dataset minus the denylist, **no regime filter** (the regime is a column below), taker entry at the next 15m open, T-401 triple barrier (TP1 as target for S1).

- candidates: 5881 (overlapping); trades with one open position per symbol × strategy: **3542**
- **gross** = exit / entry − 1 without fees and slippage; win % = share of trades with gross > 0
- **BE fee** = break-even round-trip fee = mean gross (the fee at which the mean net return is 0 with zero slippage); **BE fee after slip.** = the same after the assumed spread / slippage of `base.bt` (negative = loses before any fee)
- t = mean / standard error; groups with < 30 trades are marked _(noise)_; trades of one symbol can still overlap across strategies, so t is optimistic
- **drift-adj.** = gross − (the market's mean 15m return over the dev period × bars held): what is left after the passive drift a long position collects. The dev period was a strong rally in TMN terms (toman depreciation + crypto up; see `variants.md` → buy and hold), so this is the part of the edge that does **not** need the rally to continue

**All trades:** n=3542, win 58.4 %, mean gross 0.223 %, t=14.61, drift-adj. 0.162 % (t=10.65), BE fee 0.223 %, BE fee after slippage 0.048 %, mean bars held 6.4

## By strategy

| strategy | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S2_MEAN_REVERSION | 389 | 46.5 | 0.432 | -0.116 | 7.04 | 0.341 | 0.432 | 0.241 |
| S3_IMPLIED_DEV | 1967 | 71.6 | 0.349 | 0.467 | 18.55 | 0.285 | 0.349 | 0.179 |
| S1_TREND_BREAKOUT | 1186 | 40.6 | -0.055 | -0.267 | -2.24 | -0.100 | -0.055 | -0.233 |

## By market

| symbol | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| LTC/TMN | 185 | 66.5 | 0.496 | 0.545 | 5.98 | 0.421 | 0.496 | 0.309 |
| AVAX/TMN | 223 | 60.1 | 0.379 | 0.356 | 5.96 | 0.304 | 0.379 | 0.189 |
| BCH/TMN | 207 | 57.0 | 0.370 | 0.416 | 4.42 | 0.290 | 0.370 | 0.174 |
| DOT/TMN | 195 | 50.3 | 0.304 | 0.176 | 3.46 | 0.243 | 0.304 | 0.107 |
| BNB/TMN | 198 | 68.7 | 0.265 | 0.440 | 6.49 | 0.209 | 0.265 | 0.082 |
| LINK/TMN | 251 | 51.8 | 0.248 | 0.110 | 4.34 | 0.157 | 0.248 | 0.047 |
| SOL/TMN | 272 | 66.9 | 0.243 | 0.480 | 5.62 | 0.175 | 0.243 | 0.070 |
| ADA/TMN | 265 | 52.8 | 0.239 | 0.250 | 3.29 | 0.175 | 0.239 | 0.043 |
| SHIB/TMN | 220 | 54.1 | 0.222 | 0.215 | 2.95 | 0.168 | 0.222 | 0.023 |
| ETH/TMN | 288 | 60.8 | 0.146 | 0.376 | 3.57 | 0.083 | 0.146 | 0.017 |
| DOGE/TMN | 258 | 57.4 | 0.142 | 0.415 | 2.75 | 0.096 | 0.142 | -0.047 |
| TRX/TMN | 253 | 55.3 | 0.136 | 0.139 | 2.70 | 0.094 | 0.136 | -0.065 |
| BTC/TMN | 289 | 66.1 | 0.120 | 0.285 | 4.17 | 0.070 | 0.120 | 0.016 |
| XRP/TMN | 277 | 58.8 | 0.119 | 0.431 | 2.15 | 0.069 | 0.119 | -0.065 |
| USDT/TMN | 161 | 45.3 | 0.023 | -0.141 | 0.80 | -0.015 | 0.023 | -0.080 |

## By regime (rule regime at signal time)

| regime | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RANGE | 1837 | 60.3 | 0.272 | 0.328 | 13.22 | 0.204 | 0.272 | 0.094 |
| HIGH_VOL | 479 | 63.0 | 0.239 | 0.444 | 5.03 | 0.185 | 0.239 | 0.073 |
| TREND_UP | 1226 | 53.8 | 0.142 | 0.226 | 5.64 | 0.090 | 0.142 | -0.032 |

## By hour of day (signal candle close)

| hour_utc_label | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 00 UTC (03:30 Tehran) | 124 | 69.4 | 0.562 | 0.497 | 4.60 | 0.497 | 0.562 | 0.392 |
| 01 UTC (04:30 Tehran) | 152 | 65.1 | 0.285 | 0.386 | 3.76 | 0.220 | 0.285 | 0.114 |
| 02 UTC (05:30 Tehran) | 178 | 61.2 | 0.257 | 0.378 | 4.31 | 0.199 | 0.257 | 0.077 |
| 03 UTC (06:30 Tehran) | 207 | 63.8 | 0.261 | 0.335 | 4.45 | 0.191 | 0.261 | 0.088 |
| 04 UTC (07:30 Tehran) | 163 | 57.1 | 0.182 | 0.277 | 2.52 | 0.122 | 0.182 | 0.001 |
| 05 UTC (08:30 Tehran) | 152 | 54.6 | 0.151 | 0.208 | 2.08 | 0.099 | 0.151 | -0.019 |
| 06 UTC (09:30 Tehran) | 169 | 63.9 | 0.329 | 0.458 | 5.04 | 0.271 | 0.329 | 0.168 |
| 07 UTC (10:30 Tehran) | 161 | 46.0 | 0.127 | -0.090 | 2.16 | 0.074 | 0.127 | -0.055 |
| 08 UTC (11:30 Tehran) | 192 | 64.6 | 0.358 | 0.506 | 6.50 | 0.295 | 0.358 | 0.193 |
| 09 UTC (12:30 Tehran) | 149 | 61.1 | 0.333 | 0.453 | 4.80 | 0.281 | 0.333 | 0.170 |
| 10 UTC (13:30 Tehran) | 129 | 53.5 | 0.160 | 0.206 | 2.16 | 0.099 | 0.160 | -0.016 |
| 11 UTC (14:30 Tehran) | 90 | 53.3 | 0.056 | 0.091 | 0.61 | 0.006 | 0.056 | -0.123 |
| 12 UTC (15:30 Tehran) | 120 | 55.0 | 0.037 | 0.187 | 0.37 | -0.012 | 0.037 | -0.138 |
| 13 UTC (16:30 Tehran) | 157 | 54.8 | 0.239 | 0.225 | 3.42 | 0.185 | 0.239 | 0.061 |
| 14 UTC (17:30 Tehran) | 195 | 58.5 | 0.241 | 0.325 | 3.53 | 0.180 | 0.241 | 0.067 |
| 15 UTC (18:30 Tehran) | 202 | 62.4 | 0.301 | 0.415 | 4.26 | 0.243 | 0.301 | 0.131 |
| 16 UTC (19:30 Tehran) | 170 | 60.6 | 0.273 | 0.428 | 4.17 | 0.206 | 0.273 | 0.103 |
| 17 UTC (20:30 Tehran) | 128 | 49.2 | -0.020 | -0.014 | -0.24 | -0.084 | -0.020 | -0.208 |
| 18 UTC (21:30 Tehran) | 147 | 55.8 | 0.103 | 0.173 | 1.55 | 0.039 | 0.103 | -0.077 |
| 19 UTC (22:30 Tehran) | 100 | 54.0 | 0.125 | 0.165 | 1.42 | 0.064 | 0.125 | -0.056 |
| 20 UTC (23:30 Tehran) | 101 | 57.4 | 0.208 | 0.248 | 2.31 | 0.145 | 0.208 | 0.033 |
| 21 UTC (00:30 Tehran) | 131 | 58.8 | 0.268 | 0.347 | 3.28 | 0.200 | 0.268 | 0.090 |
| 22 UTC (01:30 Tehran) | 117 | 49.6 | 0.026 | 0.000 | 0.33 | -0.039 | 0.026 | -0.164 |
| 23 UTC (02:30 Tehran) | 108 | 62.0 | 0.213 | 0.350 | 2.66 | 0.144 | 0.213 | 0.033 |

## By exit reason

| reason | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TP | 1725 | 98.1 | 0.865 | 0.733 | 54.39 | 0.819 | 0.865 | 0.749 |
| TIME | 677 | 55.7 | 0.137 | 0.055 | 6.09 | 0.011 | 0.137 | -0.101 |
| SL | 1140 | 0.0 | -0.697 | -0.562 | -49.84 | -0.743 | -0.697 | -0.925 |

## By strategy × regime

| strategy | regime | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S2_MEAN_REVERSION | RANGE | 300 | 48.0 | 0.460 | -0.103 | 6.93 | 0.368 | 0.460 | 0.271 |
| S2_MEAN_REVERSION | HIGH_VOL | 33 | 51.5 | 0.442 | 0.534 | 1.92 | 0.356 | 0.442 | 0.243 |
| S3_IMPLIED_DEV | HIGH_VOL | 284 | 75.4 | 0.382 | 0.515 | 7.24 | 0.329 | 0.382 | 0.225 |
| S3_IMPLIED_DEV | RANGE | 1151 | 71.4 | 0.350 | 0.461 | 14.68 | 0.282 | 0.350 | 0.177 |
| S3_IMPLIED_DEV | TREND_UP | 532 | 69.9 | 0.328 | 0.450 | 8.84 | 0.268 | 0.328 | 0.159 |
| S2_MEAN_REVERSION | TREND_UP | 56 | 35.7 | 0.278 | -0.235 | 1.43 | 0.187 | 0.278 | 0.077 |
| S1_TREND_BREAKOUT | TREND_UP | 638 | 42.0 | -0.024 | -0.249 | -0.76 | -0.067 | -0.024 | -0.201 |
| S1_TREND_BREAKOUT | HIGH_VOL | 162 | 43.8 | -0.052 | -0.313 | -0.57 | -0.102 | -0.052 | -0.226 |
| S1_TREND_BREAKOUT | RANGE | 386 | 36.8 | -0.107 | -0.281 | -2.87 | -0.154 | -0.107 | -0.290 |

## By strategy × market

| strategy | symbol | trades | win % | mean gross % | median gross % | t | drift-adj. gross % | BE fee % | BE fee after slip. % |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S2_MEAN_REVERSION | LTC/TMN _(noise)_ | 20 | 55.0 | 1.115 | 0.548 | 2.05 | 1.016 | 1.115 | 0.933 |
| S2_MEAN_REVERSION | ADA/TMN _(noise)_ | 14 | 50.0 | 1.039 | -0.071 | 1.60 | 0.908 | 1.039 | 0.823 |
| S2_MEAN_REVERSION | DOT/TMN | 31 | 61.3 | 0.980 | 1.012 | 3.80 | 0.897 | 0.980 | 0.793 |
| S3_IMPLIED_DEV | BCH/TMN | 100 | 76.0 | 0.708 | 1.264 | 7.47 | 0.624 | 0.708 | 0.523 |
| S3_IMPLIED_DEV | LTC/TMN | 115 | 78.3 | 0.610 | 0.864 | 8.02 | 0.527 | 0.610 | 0.424 |
| S2_MEAN_REVERSION | SOL/TMN _(noise)_ | 13 | 61.5 | 0.559 | 0.732 | 2.32 | 0.410 | 0.559 | 0.356 |
| S3_IMPLIED_DEV | AVAX/TMN | 106 | 70.8 | 0.548 | 0.982 | 5.74 | 0.462 | 0.548 | 0.358 |
| S2_MEAN_REVERSION | TRX/TMN _(noise)_ | 4 | 75.0 | 0.527 | 0.641 | 1.33 | 0.470 | 0.527 | 0.339 |
| S2_MEAN_REVERSION | BCH/TMN | 50 | 50.0 | 0.523 | -0.054 | 3.65 | 0.430 | 0.523 | 0.320 |
| S3_IMPLIED_DEV | SHIB/TMN | 128 | 69.5 | 0.511 | 0.751 | 5.93 | 0.449 | 0.511 | 0.323 |
| S3_IMPLIED_DEV | DOT/TMN | 100 | 64.0 | 0.511 | 1.237 | 4.12 | 0.443 | 0.511 | 0.325 |
| S2_MEAN_REVERSION | DOGE/TMN _(noise)_ | 20 | 50.0 | 0.447 | 0.317 | 2.06 | 0.367 | 0.447 | 0.234 |
| S3_IMPLIED_DEV | LINK/TMN | 118 | 66.1 | 0.438 | 0.836 | 4.90 | 0.337 | 0.438 | 0.243 |
| S3_IMPLIED_DEV | ADA/TMN | 141 | 67.4 | 0.395 | 0.625 | 4.42 | 0.322 | 0.395 | 0.208 |
| S3_IMPLIED_DEV | BNB/TMN | 123 | 83.7 | 0.368 | 0.484 | 7.87 | 0.313 | 0.368 | 0.197 |
| S2_MEAN_REVERSION | AVAX/TMN | 46 | 45.7 | 0.357 | -0.147 | 2.29 | 0.259 | 0.357 | 0.151 |
| S2_MEAN_REVERSION | BNB/TMN _(noise)_ | 19 | 63.2 | 0.352 | 0.359 | 2.16 | 0.244 | 0.352 | 0.154 |
| S2_MEAN_REVERSION | LINK/TMN | 51 | 41.2 | 0.344 | -0.131 | 2.68 | 0.225 | 0.344 | 0.129 |
| S2_MEAN_REVERSION | SHIB/TMN _(noise)_ | 24 | 45.8 | 0.318 | -0.178 | 1.28 | 0.241 | 0.318 | 0.109 |
| S3_IMPLIED_DEV | SOL/TMN | 177 | 78.5 | 0.311 | 0.533 | 6.69 | 0.248 | 0.311 | 0.152 |
| S3_IMPLIED_DEV | TRX/TMN | 174 | 64.9 | 0.288 | 0.301 | 4.99 | 0.244 | 0.288 | 0.092 |
| S2_MEAN_REVERSION | XRP/TMN _(noise)_ | 24 | 37.5 | 0.206 | -0.335 | 0.74 | 0.141 | 0.206 | -0.007 |
| S3_IMPLIED_DEV | ETH/TMN | 172 | 72.1 | 0.189 | 0.413 | 3.95 | 0.129 | 0.189 | 0.070 |
| S3_IMPLIED_DEV | DOGE/TMN | 173 | 64.7 | 0.174 | 0.473 | 2.97 | 0.125 | 0.174 | -0.008 |
| S3_IMPLIED_DEV | BTC/TMN | 174 | 78.2 | 0.154 | 0.296 | 4.74 | 0.107 | 0.154 | 0.058 |
| S1_TREND_BREAKOUT | AVAX/TMN | 71 | 53.5 | 0.141 | 0.143 | 1.54 | 0.097 | 0.141 | -0.036 |
| S3_IMPLIED_DEV | XRP/TMN | 166 | 68.7 | 0.139 | 0.514 | 2.30 | 0.087 | 0.139 | -0.036 |
| S2_MEAN_REVERSION | ETH/TMN _(noise)_ | 27 | 33.3 | 0.099 | -0.210 | 0.70 | 0.023 | 0.099 | -0.057 |
| S1_TREND_BREAKOUT | ETH/TMN | 89 | 47.2 | 0.077 | -0.279 | 0.91 | 0.013 | 0.077 | -0.062 |
| S2_MEAN_REVERSION | BTC/TMN _(noise)_ | 20 | 40.0 | 0.076 | -0.163 | 0.63 | -0.010 | 0.076 | -0.055 |
| S1_TREND_BREAKOUT | BTC/TMN | 95 | 49.5 | 0.066 | -0.143 | 1.13 | 0.018 | 0.066 | -0.047 |
| S1_TREND_BREAKOUT | XRP/TMN | 87 | 46.0 | 0.056 | -0.294 | 0.50 | 0.015 | 0.056 | -0.136 |
| S1_TREND_BREAKOUT | SOL/TMN | 82 | 42.7 | 0.045 | -0.382 | 0.49 | -0.021 | 0.045 | -0.151 |
| S1_TREND_BREAKOUT | USDT/TMN | 135 | 48.9 | 0.034 | -0.117 | 1.09 | -0.004 | 0.034 | -0.065 |
| S1_TREND_BREAKOUT | BNB/TMN | 56 | 37.5 | 0.008 | -0.200 | 0.11 | -0.029 | 0.008 | -0.194 |
| S1_TREND_BREAKOUT | LTC/TMN | 50 | 44.0 | -0.012 | -0.249 | -0.12 | -0.059 | -0.012 | -0.207 |
| S2_MEAN_REVERSION | USDT/TMN _(noise)_ | 26 | 26.9 | -0.035 | -0.186 | -0.51 | -0.071 | -0.035 | -0.155 |
| S1_TREND_BREAKOUT | DOGE/TMN | 65 | 40.0 | -0.037 | -0.299 | -0.33 | -0.065 | -0.037 | -0.236 |
| S1_TREND_BREAKOUT | ADA/TMN | 110 | 34.5 | -0.063 | -0.453 | -0.65 | -0.107 | -0.063 | -0.269 |
| S1_TREND_BREAKOUT | LINK/TMN | 82 | 37.8 | -0.085 | -0.260 | -1.10 | -0.143 | -0.085 | -0.287 |
| S1_TREND_BREAKOUT | TRX/TMN | 75 | 32.0 | -0.239 | -0.398 | -2.70 | -0.273 | -0.239 | -0.451 |
| S1_TREND_BREAKOUT | DOT/TMN | 64 | 23.4 | -0.347 | -0.346 | -3.89 | -0.387 | -0.347 | -0.566 |
| S1_TREND_BREAKOUT | SHIB/TMN | 68 | 27.9 | -0.358 | -0.557 | -2.66 | -0.388 | -0.358 | -0.571 |
| S1_TREND_BREAKOUT | BCH/TMN | 57 | 29.8 | -0.359 | -0.517 | -1.88 | -0.418 | -0.359 | -0.568 |
