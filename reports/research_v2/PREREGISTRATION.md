# Research v2: pre-registration

Written and committed on 2026-10-22, **before** any v2 data was downloaded or any hypothesis was
tested. Parameters are in `config/research_v2.yaml`, committed together with this file. Anything changed
after this commit is a deviation and will be listed in `SUMMARY.md`.

`config/strategy.yaml` and `config/risk.yaml` are not changed. This is research only.

## Why

D-001 (`reports/diagnostics/SUMMARY.md`) found no robust edge at the expected cost of 0.4–0.7 %
round-trip fee + ~0.17 % slippage. The existing strategies have a gross edge of +0.2–0.4 % per trade
and hold for ~1.6 h. The hypotheses below aim at **larger moves per trade**, so that the cost is a
smaller share of the move. The D-001 hold-out (last 20 days of the 15 markets) is used up.

## Data split

- **DEV:** the 15 TMN markets already used (`data/processed`): ADA, AVAX, BCH, BNB, BTC, DOGE, DOT, ETH,
  LINK, LTC, SHIB, SOL, TRX, USDT, XRP (/TMN). After H4 (below), DOGE, TRX, XRP and USDT/TMN are not
  traded. USDT/TMN is used as data for the implied price.
- **HOLD-OUT:** different markets over the same calendar period. The rule is fixed now; the list is
  resolved after download and written into an addendum before any test:
  1. Spot TMN markets of `/hector/web/v1/markets` (snapshot `docs/vendor/wallex/samples/hector_markets_flags.json`,
     2026-10-22 10:22 UTC) not on `config/denylist.txt` and not among the 15 DEV markets.
  2. 24h quote volume ≥ 2.0 × 10⁹ TMN, and a spot X/USDT pair on Wallex (needed for H1/H2).
  3. Stablecoins are excluded for the same reason as USDT/TMN (USDC/TMN).
  4. After download: ≥ 50 % of 15m bars have a real candle over the period; and H4.
  - This currently gives 20 candidates: PEPE, PUMP, USOON, ARB, XAUT, S, SUI, UNI, APE, SAND, FIL, HYPE,
    COPXON, DYDX, SLVON, HBAR, GALA, CAKE, ENA, AAVE (/TMN).
  - The selection uses only volume, coverage and spread, never returns.
- **Period:** 120 days of 15m candles ending 2026-10-22 06:00 UTC (same end as D-001), for DEV and HOLD-OUT.
  The first 10 days are indicator warm-up. Candles come from the public `/v1/udf/history` via
  `scripts/download_data.py` and `scripts/validate_data.py`. Hold-out data goes to separate directories,
  so `data/processed` is unchanged.
- **The hold-out is evaluated once**, at the end. The script writes `reports/research_v2/holdout_evaluated.json`
  and refuses a second run.
- **Limitation, stated in advance:** the hold-out is a cross-section, not a later period. It tests
  whether a rule generalises to other markets, but it shares the same market-wide moves, e.g. the
  toman depreciation. It is not a test of a different regime. That needs new data after 2026-10-22.

## Costs

- Round-trip fees 0.4 %, 0.5 % and 0.7 % (maker = taker = rt / 2). The pass criteria are judged at **0.5 %**.
- Slippage is the existing `base.bt` model: one-way = assumed spread / 2 + 0.05 % on every taker leg.
  A TP exit is a resting limit and pays no slippage (same as T-401 / D-001).
- Once T-108 gives the account's real fee, the numbers are re-priced at that fee. This is the same
  trades priced again, not new trials.
- **Live fee filter applies:** expected move (= TP %) ≥ `fee_multiple` (2.5) × rt. At 0.7 % it blocks
  H1 with X = 1.5. The live system would block it too.

## Common rules

- 15m bars are put on a regular grid. A bar without a trade keeps the last close and has volume 0;
  OHLC = previous close, so a bar without a trade cannot trigger a stop.
- Signals use only bars closed at t. Entry is a taker order at the next 15m open + slippage.
- **One open position per variant × market.** A setup is skipped while the previous trade of that
  variant and market is still open.
- Same-bar SL and TP counts as SL.
- No regime filter: each hypothesis defines its own conditions.
- **H4, cost filter, applies to all hypotheses:**
  - only markets with a median top-of-book spread ≤ 0.15 %;
  - DOGE, TRX, XRP and USDT/TMN are excluded.
  - There is no historical order book. The median comes from 12 snapshots of the public
    `GET /depth/all`, 60 s apart, taken once after this commit. That is one time of day only, which is
    a stated limitation. Spread = (best ask − best bid) / mid.
- `implied = (X/USDT close) × (USDT/TMN close)` on the same 15m bar.
  `dev_pct = (P_tmn / implied − 1) × 100`. Each leg must have a real candle within the last 4 bars,
  otherwise there is no signal on that bar.

## Hypotheses (23 variants in total)

### H1: large implied deviation (9 variants)

- Enter long when `dev_pct ≤ −X` at a 15m close, X ∈ {1.5, 2.0, 2.5} %.
- Exits:
  - TP: +X % from the fill;
  - SL ∈ {1.0, 1.5, 2.0} %;
  - signal exit: `dev_pct ≥ −0.3` at a 15m close → exit at the next open (taker + slippage);
  - time stop: 6 h.
- Expected move for the fee filter = X.

### H2: capitulation bounce (12 variants)

Checked on every 15m close. With `r(k)` = close / close k bars ago − 1, for k ∈ {4, 8, 12, 16} (1–4 h):

- for some k: `r_tmn(k) ≤ −Y %` **and** `r_tmn(k) − r_usdt(k) ≤ −2 %`, where r_usdt is the X/USDT pair over the same k bars;
- **and** the max 15m volume z-score (20-bar rolling mean / std, TMN pair) over the last 4 bars ≥ 2;
- Y ∈ {5, 6, 8} %, TP ∈ {3, 4} %, SL ∈ {2, 3} %, time stop 12 h.

### H3: multi-day range breakout (2 variants)

Checked on every 1h close (1h and 4h bars are built from the 15m grid):

- 1h close > the highest high of the previous 72 1h bars;
- **and** 1h volume z-score (20 bars) ≥ 2;
- **and** 4h EMA50 (closed 4h bars) > its value one 4h bar earlier;
- TP ∈ {4, 6} %, SL 2 %, time stop 48 h.

## Metrics, per hypothesis and per variant

- n, win %, **mean gross** (before fees and slippage), and mean net at 0.4 / 0.5 / 0.7 %.
- **Drift-adjusted gross:** gross − the market's buy-and-hold return per 15m bar × bars held. The rate is
  the mean 15m close-to-close return of that market over the evaluation period (DEV or HOLD-OUT); this
  is the D-001 definition.
  - Subtracting the same market's return over the *exact* same window would be ≈ 0 by construction
    for a long-only trade in that market, so it cannot be the metric.
  - As a secondary check, also reported: gross − the equal-weight return of the traded universe over
    the trade's exact entry → exit window.
- **Walk-forward (DEV):**
  - 14-day windows.
  - In window w each hypothesis trades the variant with the best cumulative net over windows < w at
    that fee, with ≥ 10 past trades. Window 0 is learning only.
  - Reported: OOS mean net per trade, share of OOS windows with positive net, trades per day across
    the DEV universe, and drift-adjusted gross of the OOS trades.
- **HOLD-OUT:**
  - Per hypothesis, the variant with the best cumulative net over the whole DEV period at each fee
    (the pass check uses the 0.5 % pick).
  - Reported: n, mean net per trade, trades per day, and **max equity drawdown**. The drawdown comes
    from a compounding portfolio: 20 % of equity per trade at entry, at most 5 open at once (later
    setups are skipped), P&L booked at exit.

## Pass criteria (all required, at RT 0.5 %)

1. DEV walk-forward OOS mean net per trade **> +0.15 %**.
2. Positive net in **≥ 60 %** of the OOS 14-day windows.
3. Drift-adjusted gross of those OOS trades **> +0.30 %** per trade.
4. HOLD-OUT mean net per trade **> 0**.
5. HOLD-OUT max equity drawdown **< 5 %** (20 % position size).
6. **≥ 0.8 trades per day** across the DEV universe (OOS). This is how "~1 trade per day" is read.

- A hypothesis passes only if all six hold.
- 23 variants are tried, so roughly one would look good on DEV by chance. That is why criteria 4–5
  are required.
- If none passes, the summary says **"no robust edge found"**.

## Outputs

- `reports/research_v2/SUMMARY.md`: a table per hypothesis (n, mean gross, drift-adjusted, net at each
  fee, window win rate, hold-out result, verdict), the number of variants tried (23), deviations from
  this document, and a recommendation.
- `PROGRESS.md` updated.
