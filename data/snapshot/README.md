# Data snapshot

Frozen copy of the data used for the reported backtests (T-206), so judges can reproduce results
without a Wallex API key.

- `processed/` — cleaned 15m / 1h candles (`data/processed/`, see `reports/data_quality.md`)
- `live/` — candles recorded during the challenge (`data/live/`), if any
- `MANIFEST.json` — sha256, rows and time range of every file (verify with `sha256sum`)

Created 2026-10-22T12:39:56.674718+00:00 from git `484ddbc` · 58 files · 10.6 MB.

Use it:

```bash
make backtest ARGS="--snapshot --llm off"
python scripts/download_data.py --from-snapshot
```
