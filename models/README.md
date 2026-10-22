# Models

Each trained model lives in `models/lgbm_v{N}/` (written by `make train`, see `src/siavash/ml/registry.py`):

| file | content |
|---|---|
| `model.txt` | LightGBM booster (fixed params from `config/strategy.yaml > ml.lgbm`, seed 42, deterministic, 1 thread) |
| `calibrator.pkl` | isotonic calibrator fit on the walk-forward out-of-fold predictions |
| `metadata.json` | created_at, feature_set_version, features, data source / range / hash, OOF metrics, threshold, `recommend_enabled`, seed, git_sha |

Rules:
- `load_model()` refuses a model whose `feature_set_version` differs from `FEATURE_SET_VERSION` in
  `src/siavash/features/feature_set.py` (changing a feature definition requires a new version and retraining).
- The bot only uses a model when `strategy.yaml > ml.enabled: true` **and** `ml.model_version` names it.
  The walk-forward report (`reports/ml_walkforward.md`) states whether enabling is recommended.
- Versions are never overwritten; a new training run creates `lgbm_v{N+1}`.
