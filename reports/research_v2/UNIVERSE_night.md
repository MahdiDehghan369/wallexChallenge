# Research v2: resolved universe (addendum to the pre-registration)

Resolved 2026-10-22 22:38 UTC by `scripts/run_research_v2.py universe`, **before** any hypothesis was run. It uses only the pre-registered rules: volume, bar coverage, measured spread and the H4 list. Spreads come from `spreads.json`, i.e. `GET /depth/all` snapshots.

- period: 2026-06-24 06:00 → 2026-10-22 06:00 UTC; setups count from 2026-07-04 06:00 (warm-up)
- **DEV traded (3):** BTC/TMN, ETH/TMN, LINK/TMN
- **HOLD-OUT traded (2):** USOON/TMN, XAUT/TMN

| market | split | median spread % | 15m coverage | 24h quote vol (1e9 TMN) | X/USDT | used | reason |
|---|---|---:|---:|---:|---|---|---|
| ADA/TMN | dev | 0.741 | 1.00 | 7.52 | yes | no | median spread 0.741 % > 0.15 |
| AVAX/TMN | dev | 0.532 | 1.00 | 3.84 | yes | no | median spread 0.532 % > 0.15 |
| BCH/TMN | dev | 0.192 | 1.00 | 2.31 | yes | no | median spread 0.192 % > 0.15 |
| BNB/TMN | dev | 0.400 | 1.00 | 55.17 | yes | no | median spread 0.400 % > 0.15 |
| BTC/TMN | dev | 0.055 | 1.00 | 113.81 | yes | **yes** | — |
| DOGE/TMN | dev | 0.189 | 1.00 | 40.51 | yes | no | excluded (H4 list / stablecoin); median spread 0.189 % > 0.15 |
| DOT/TMN | dev | 0.687 | 1.00 | 1.27 | yes | no | median spread 0.687 % > 0.15 |
| ETH/TMN | dev | 0.130 | 1.00 | 76.93 | yes | **yes** | — |
| LINK/TMN | dev | 0.109 | 1.00 | 2.91 | yes | **yes** | — |
| LTC/TMN | dev | 0.505 | 1.00 | 8.21 | yes | no | median spread 0.505 % > 0.15 |
| SHIB/TMN | dev | 0.471 | 1.00 | 5.53 | yes | no | median spread 0.471 % > 0.15 |
| SOL/TMN | dev | 0.281 | 1.00 | 29.06 | yes | no | median spread 0.281 % > 0.15 |
| TRX/TMN | dev | 0.349 | 1.00 | 12.64 | yes | no | excluded (H4 list / stablecoin); median spread 0.349 % > 0.15 |
| USDT/TMN | dev | 0.045 | 0.99 | 58.94 | no | no | excluded (H4 list / stablecoin) |
| XRP/TMN | dev | 0.135 | 1.00 | 36.90 | yes | no | excluded (H4 list / stablecoin) |
| AAVE/TMN | holdout | 0.575 | 1.00 | 2.12 | yes | no | median spread 0.575 % > 0.15 |
| APE/TMN | holdout | 0.662 | 1.00 | 7.86 | yes | no | median spread 0.662 % > 0.15 |
| ARB/TMN | holdout | 0.588 | 1.00 | 33.83 | yes | no | median spread 0.588 % > 0.15 |
| CAKE/TMN | holdout | 0.943 | 1.00 | 2.60 | yes | no | median spread 0.943 % > 0.15 |
| COPXON/TMN | holdout | 2.166 | 1.00 | 4.14 | yes | no | median spread 2.166 % > 0.15 |
| DYDX/TMN | holdout | 1.871 | 0.99 | 3.58 | yes | no | median spread 1.871 % > 0.15 |
| ENA/TMN | holdout | 0.612 | 1.00 | 2.27 | yes | no | median spread 0.612 % > 0.15 |
| FIL/TMN | holdout | 0.669 | 1.00 | 5.07 | yes | no | median spread 0.669 % > 0.15 |
| GALA/TMN | holdout | 1.699 | 1.00 | 2.61 | yes | no | median spread 1.699 % > 0.15 |
| HBAR/TMN | holdout | 0.494 | 1.00 | 2.67 | yes | no | median spread 0.494 % > 0.15 |
| HYPE/TMN | holdout | 0.308 | 1.00 | 4.32 | yes | no | median spread 0.308 % > 0.15 |
| PEPE/TMN | holdout | 0.446 | 1.00 | 123.49 | yes | no | median spread 0.446 % > 0.15 |
| PUMP/TMN | holdout | 0.320 | 1.00 | 90.34 | yes | no | median spread 0.320 % > 0.15 |
| S/TMN | holdout | 0.375 | 1.00 | 12.82 | yes | no | median spread 0.375 % > 0.15 |
| SAND/TMN | holdout | 0.395 | 1.00 | 5.08 | yes | no | median spread 0.395 % > 0.15 |
| SLVON/TMN | holdout | 0.985 | 1.00 | 2.92 | yes | no | median spread 0.985 % > 0.15 |
| SUI/TMN | holdout | 1.471 | 1.00 | 8.36 | yes | no | median spread 1.471 % > 0.15 |
| UNI/TMN | holdout | 0.706 | 1.00 | 8.10 | yes | no | median spread 0.706 % > 0.15 |
| USOON/TMN | holdout | 0.000 | 1.00 | 71.91 | yes | **yes** | — |
| XAUT/TMN | holdout | 0.101 | 1.00 | 33.51 | yes | **yes** | — |
