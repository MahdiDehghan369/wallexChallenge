# Zero-fee markets (`is_zero_fee` of `/hector/web/v1/markets`)

Source: `docs/vendor/wallex/samples/hector_markets_flags.json` (469 markets, fetched 2026-10-22T10:22:58+00:00). Allowed = `is_spot`, quote in ['TMN'], not in `config/denylist.txt`.

- markets with `is_zero_fee: true`: **1**
  - USDS/USDT: excluded (quote USDT not allowed, denylist)
- **allowed spot TMN markets with zero fee: 0**

`is_zero_fee` is a market flag; whether it applies to API orders of this account is not documented — confirm with `/v1/account/fee` (T-108) before relying on it.
