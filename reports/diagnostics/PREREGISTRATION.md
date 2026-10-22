# Hold-out pre-registration

Written before `python scripts/run_diagnostics.py --holdout` was run (2026-10-22). Nothing below is
changed after the run.

- Hold-out: last 20 days of the dataset (2026-10-02 06:00 → 2026-10-22 06:00 UTC). Not read by any dev
  report (`edge_breakdown.md`, `fee_sensitivity.md`, `variants.md`).
- Variants tried on dev: **14** (5 profiles: baseline 1, a_h1_breakout 9, b_maker 1, c_s3_strict 2,
  d_trend_top3 1), each at 6 round-trip fees (0.0 / 0.2 / 0.3 / 0.4 / 0.5 / 0.7 %), plus the ML filter
  on the baseline at the same 6 fees. No profile or parameter was changed after seeing dev results;
  the buy-and-hold benchmark and the drift-adjusted column were added as reporting only.
- Evaluated once on the hold-out, per fee: each family with the variant that had the best cumulative net
  return over the whole dev period at that fee; the ML filter (final dev model, top-q threshold from the
  dev OOF predictions).
- Expectation from dev: at RT ≥ 0.4 % no family except the long-only trend variants is positive, and
  their dev result is explained by market drift (drift-adjusted gross ≈ 0). A family counts as a
  **robust edge** only if it is positive net at the fee it would actually pay in **both** the dev
  walk-forward OOS windows **and** the hold-out, and its dev drift-adjusted gross covers that fee.
