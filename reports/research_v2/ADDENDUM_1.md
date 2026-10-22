# Research v2: addendum 1 (spread measurement timing)

Written and committed on 2026-10-22 22:45 UTC, **before** any hypothesis was run. The only results
seen so far are the night spread snapshots below and the universe they give. No trades, returns or
signals have been computed.

## What happened

- The pre-registration (H4) asked for 12 snapshots of `GET /depth/all`, 60 s apart, "taken once after
  this commit". It did not fix the time of day.
- **First attempt:** `/depth/all` returned 503 on every snapshot, so nothing was recorded.
- **Second attempt, 2026-10-22 22:10–22:35 UTC (01:40–02:05 Tehran):**
  - `/depth/all` was still returning 503, so each snapshot used `GET /v1/depth` once per candidate
    market. This is the same public top-of-book, already in `endpoints.py`.
  - All 12 snapshots worked, for 35 markets.
  - Kept unchanged as `spreads_night.json`, `universe_night.json` and `UNIVERSE_night.md`.
- With the pre-registered ≤ 0.15 % cap, the night data leaves:
  - **DEV:** BTC, ETH, LINK;
  - **HOLD-OUT:** USOON, XAUT (two tokenized commodities).
- This is too small for criterion 6 (≥ 0.8 trades/day) or for a hold-out test of crypto rules to mean
  anything.
- The snapshots were taken in the middle of the night in Tehran, when the TMN books are thinnest. That
  is not when the system trades.

## Change (fixed now, before the new measurement)

1. **One new measurement decides the universe:** 12 snapshots, 60 s apart, starting
   **2026-10-23 08:00 UTC** (11:30 Tehran, inside local trading hours). It uses the same script
   (`scripts/measure_spreads.py`, `/depth/all` with the per-market `/v1/depth` fallback) and writes
   `spreads.json`.
2. **Threshold unchanged:** median spread ≤ 0.15 %. The H4 exclusions (DOGE, TRX, XRP, USDT/TMN) and
   every other universe rule stay the same.
3. **The daytime result is used whatever it gives**, even if the universe is as small as the night
   one, or smaller. There is no third measurement and no change to the threshold.
4. **If the daytime run fails** (fewer than 6 of 12 snapshots worked), the night measurement is used
   as is.
5. **Reporting:** `SUMMARY.md` reports both measurements and lists this addendum as a deviation
   (timing of the measurement, plus the per-market depth fallback).
6. No market is added or removed by hand. USOON's night median spread of 0.0001 % looks
   implausible, but it is reported as measured.
