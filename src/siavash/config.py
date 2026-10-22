"""Configuration system (T-002).

Load order: ``config/base.yaml`` + ``config/strategy.yaml`` + ``config/risk.yaml`` -> environment
(``.env`` file, then process env; env wins). Secrets live only in ``AppConfig.secrets`` and are
never part of :func:`config_hash` or a JSON dump.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ModeName = Literal["backtest", "paper", "live", "replay"]
RegimeName = Literal["TREND_UP", "RANGE", "HIGH_VOL", "PANIC"]
StrategyName = Literal["S1_TREND_BREAKOUT", "S2_MEAN_REVERSION", "S3_IMPLIED_DEV"]


class _C(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- base.yaml


class PathsConfig(_C):
    logs: str = "logs"
    state: str = "state"
    data: str = "data"
    models: str = "models"
    reports: str = "reports"


class LoopConfig(_C):
    price_guard_s: float = Field(10, gt=0)
    main_loop_s: float = Field(60, gt=0)
    poll_seconds: float = Field(20, gt=0)
    book_poll_seconds: float = Field(15, gt=0)
    buffer_bars: int = Field(500, ge=50)
    reconcile_interval_min: float = Field(5, gt=0)
    equity_interval_s: float = Field(300, gt=0)
    watchdog_timeout_s: float = Field(180, gt=0)
    heartbeat_s: float = Field(30, gt=0)
    analyst_interval_min: float = Field(60, gt=0)


class RateLimitConfig(_C):
    public_per_s: float = Field(5, gt=0)
    public_burst: int = Field(10, ge=1)
    private_per_s: float = Field(2, gt=0)
    private_burst: int = Field(4, ge=1)


class ExchangeConfig(_C):
    use_proxy: bool = False
    rate_limit: RateLimitConfig = RateLimitConfig()
    timeout_s: float = Field(10, gt=0)
    max_retries: int = Field(4, ge=1, le=10)
    backoff_min_s: float = Field(0.5, gt=0)
    backoff_max_s: float = Field(8, gt=0)
    ticker_cache_s: float = Field(5, ge=0)


class ExecutionConfig(_C):
    entry_timeout_s: float = Field(90, gt=0)
    aggressive_timeout_s: float = Field(30, gt=0)
    max_slippage_pct: float = Field(0.15, ge=0, le=2)
    exit_max_slippage_pct: float = Field(0.3, ge=0, le=5)
    exit_max_attempts: int = Field(3, ge=1)
    order_poll_seconds: float = Field(5, gt=0)
    submit_lookup_attempts: int = Field(3, ge=1)
    submit_lookup_interval_s: float = Field(2, ge=0)


class PaperConfig(_C):
    fill_check_seconds: float = Field(5, gt=0)
    initial_balances: dict[str, Decimal] = Field(default_factory=lambda: {"TMN": Decimal("5000000")})


class BacktestConfig(_C):
    assumed_spread_pct: dict[str, float] = Field(default_factory=lambda: {"default": 0.15})
    extra_slippage_pct: float = Field(0.05, ge=0)

    def spread_for(self, symbol: str) -> float:
        """Assumed spread (percent) for ``symbol``; falls back to ``default``."""
        return self.assumed_spread_pct.get(symbol, self.assumed_spread_pct.get("default", 0.15))


class DataConfig(_C):
    max_ffill_bars: int = Field(3, ge=0)
    outlier_jump_pct: float = Field(20.0, gt=0)


class AlertsConfig(_C):
    backend: Literal["log_only", "webhook"] = "log_only"
    min_interval_s: float = Field(3, ge=0)


class BaseConfig(_C):
    mode: ModeName = "paper"
    timezone: str = "Asia/Tehran"
    paths: PathsConfig = PathsConfig()
    loop: LoopConfig = LoopConfig()
    exchange: ExchangeConfig = ExchangeConfig()
    execution: ExecutionConfig = ExecutionConfig()
    paper: PaperConfig = PaperConfig()
    bt: BacktestConfig = BacktestConfig()
    data: DataConfig = DataConfig()
    alerts: AlertsConfig = AlertsConfig()


# --------------------------------------------------------------------------- llm (base.yaml > llm)


class AnalystConfig(_C):
    enabled: bool = True


class ReviewerConfig(_C):
    enabled: bool = True
    allow_tools: bool = False
    timeout_s: float = Field(20, gt=0)


class LLMConfig(_C):
    analyst: AnalystConfig = AnalystConfig()
    reviewer: ReviewerConfig = ReviewerConfig()
    timeout_s: float = Field(20, gt=0)
    max_retries: int = Field(2, ge=0)
    max_tool_calls: int = Field(3, ge=0, le=10)
    tools_backend: Literal["auto", "mcp", "native"] = "auto"
    use_proxy: bool = True
    cache_mode: Literal["live", "replay", "record_only"] = "live"
    fallback_size_multiplier: float = Field(0.5, ge=0.25, le=1.0)
    fallback_ml_edge: float = Field(0.05, ge=0)
    # از env (غیر محرمانه)
    base_url: str | None = None
    model: str | None = None
    fallback_model: str | None = None


# --------------------------------------------------------------------------- strategy.yaml


def _all_strategies() -> list[StrategyName]:
    return ["S1_TREND_BREAKOUT", "S2_MEAN_REVERSION", "S3_IMPLIED_DEV"]


def _default_regime_multipliers() -> dict[RegimeName, float]:
    return {"TREND_UP": 1.0, "RANGE": 0.7, "HIGH_VOL": 0.5, "PANIC": 0.0}


class StrategiesToggle(_C):
    enabled: list[StrategyName] = Field(default_factory=_all_strategies)


class UniverseConfig(_C):
    candidates: list[str] = Field(default_factory=list)
    max_markets: int = Field(8, ge=1, le=30)
    top_volume_n: int = Field(10, ge=1)
    depth_multiple: float = Field(5, ge=0)
    depth_band_pct: float = Field(0.5, gt=0)
    refresh_hours: float = Field(6, gt=0)
    always_keep: list[str] = Field(default_factory=lambda: ["BTC/TMN", "USDT/TMN"])


class RegimeRules(_C):
    panic_btc_ret_1h_pct: float = -3.0
    panic_btc_ret_4h_pct: float = -5.0
    high_vol_rv_percentile: float = Field(90, ge=0, le=100)
    trend_adx_min: float = 20
    trend_breadth_min: float = Field(55, ge=0, le=100)


def _default_enabled_by_regime() -> dict[RegimeName, list[StrategyName]]:
    return {
        "TREND_UP": ["S1_TREND_BREAKOUT", "S3_IMPLIED_DEV"],
        "RANGE": ["S2_MEAN_REVERSION", "S3_IMPLIED_DEV"],
        "HIGH_VOL": ["S3_IMPLIED_DEV"],
        "PANIC": [],
    }


class RegimeConfig(_C):
    rules: RegimeRules = RegimeRules()
    enabled_strategies: dict[RegimeName, list[StrategyName]] = Field(
        default_factory=_default_enabled_by_regime
    )
    panic_exit_min_profit_pct: float = 0.3


class S1Config(_C):
    donchian_n: int = Field(20, ge=2)
    adx_min: float = 20
    vol_z_min: float = 1.0
    sl_atr: float = Field(1.5, gt=0)
    tp1_atr: float = Field(2.0, gt=0)
    tp1_fraction: float = Field(0.5, gt=0, le=1)
    tp_atr: float = Field(4.0, gt=0)
    trail_atr: float = Field(1.5, gt=0)
    time_stop_min: int = Field(720, gt=0)


class S2Config(_C):
    bb_n: int = Field(20, ge=2)
    bb_k: float = Field(2.0, gt=0)
    rsi_max: float = 30
    adx_max: float = 20
    min_4h_ema200_dist_pct: float = -2.0
    sl_atr: float = Field(1.2, gt=0)
    time_stop_min: int = Field(360, gt=0)


class S3Config(_C):
    z_window_bars: int = Field(96, ge=10)
    z_entry: float = Field(2.0, gt=0)
    min_usdt_ret_1h_pct: float = -1.5
    tp_pct: float = Field(1.5, gt=0)
    sl_pct: float = Field(1.2, gt=0)
    time_stop_min: int = Field(180, gt=0)


class MLConfig(_C):
    enabled: bool = False
    model_version: str | None = None
    threshold: float = Field(0.55, ge=0, le=1)
    train_days: int = Field(45, ge=1)
    test_days: int = Field(7, ge=1)
    embargo_hours: float = Field(12, ge=0)
    min_trades_per_day: float = Field(3, ge=0)
    lgbm: dict[str, Any] = Field(default_factory=dict)


class StrategyConfig(_C):
    strategies: StrategiesToggle = StrategiesToggle()
    fee_multiple: float = Field(2.5, ge=1)
    universe: UniverseConfig = UniverseConfig()
    regime: RegimeConfig = RegimeConfig()
    s1: S1Config = S1Config()
    s2: S2Config = S2Config()
    s3: S3Config = S3Config()
    ml: MLConfig = MLConfig()
    reviewer_key_features: list[str] = Field(default_factory=list, max_length=30)


# --------------------------------------------------------------------------- risk.yaml


class CooldownLosses(_C):
    losses: int = Field(2, ge=1)
    minutes: float = Field(60, ge=0)


class EndgameConfig(_C):
    competition_end_utc: datetime | None = None
    no_new_entries_hours_before: float = Field(6, ge=0)
    start_unwind_hours_before: float = Field(3, ge=0)
    force_liquidate_hours_before: float = Field(1, ge=0)
    unwind_passive_timeout_s: float = Field(30, gt=0)

    @field_validator("competition_end_utc", mode="before")
    @classmethod
    def _placeholder_is_none(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and (not v.strip() or v.strip().upper().startswith("YYYY"))):
            return None
        return v

    @field_validator("competition_end_utc")
    @classmethod
    def _must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("competition_end_utc must include a timezone (use ...Z)")
        return v

    @model_validator(mode="after")
    def _ordered(self) -> EndgameConfig:
        if not (
            self.no_new_entries_hours_before
            >= self.start_unwind_hours_before
            >= self.force_liquidate_hours_before
        ):
            raise ValueError("endgame hours must satisfy no_new_entries >= start_unwind >= force_liquidate")
        return self


class BreakersConfig(_C):
    api_errors_window_min: float = Field(5, gt=0)
    api_errors_threshold: int = Field(5, ge=1)
    api_errors_clear_min: float = Field(15, ge=0)
    stale_data_seconds: float = Field(180, gt=0)


class FeesConfig(_C):
    maker_pct: float | None = Field(0.35, ge=0, le=2)
    taker_pct: float = Field(0.35, ge=0, le=2)


class RiskConfig(_C):
    capital_base_tmn: Decimal = Field(Decimal("5000000"), gt=0)
    risk_per_trade_pct: float = Field(0.5, gt=0, le=1.0)
    max_position_pct: float = Field(20, gt=0, le=25)
    max_total_exposure_pct: float = Field(50, gt=0, le=60)
    max_concurrent_positions: int = Field(3, ge=1, le=10)
    max_positions_per_market: int = Field(1, ge=1, le=1)
    daily_loss_limit_pct: float = Field(2.0, gt=0, le=3)
    kill_switch_drawdown_pct: float = Field(5.0, gt=0, le=8)
    cooldown_after_consecutive_losses: CooldownLosses = CooldownLosses()
    cooldown_after_exit_minutes: float = Field(30, ge=0)
    max_trades_per_hour: int = Field(4, ge=1)
    max_trades_per_day: int = Field(25, ge=1)
    max_spread_pct: float = Field(0.3, gt=0)
    regime_multipliers: dict[RegimeName, float] = Field(default_factory=_default_regime_multipliers)
    llm_size_multiplier_range: tuple[float, float] = (0.25, 1.0)
    initial_live_risk_scale: float = Field(0.5, ge=0, le=1)
    cash_buffer_fraction: float = Field(0.98, gt=0, le=1)
    allowed_quotes: list[str] = Field(default_factory=lambda: ["TMN"])
    allow_usdt_liquidation: bool = True
    endgame: EndgameConfig = EndgameConfig()
    breakers: BreakersConfig = BreakersConfig()
    fees: FeesConfig = FeesConfig()

    @field_validator("regime_multipliers")
    @classmethod
    def _mult_in_unit(cls, v: dict[str, float]) -> dict[str, float]:
        for k, m in v.items():
            if not 0.0 <= m <= 1.0:
                raise ValueError(f"regime_multipliers[{k}]={m} must be within [0, 1]")
        return v

    @field_validator("llm_size_multiplier_range")
    @classmethod
    def _llm_range(cls, v: tuple[float, float]) -> tuple[float, float]:
        lo, hi = v
        if not (0.25 <= lo <= hi <= 1.0):
            raise ValueError(f"llm_size_multiplier_range={list(v)} must be a subset of [0.25, 1.0]")
        return v


# --------------------------------------------------------------------------- env / app


class Secrets(BaseModel):
    """Secret values; never hashed, never dumped by :meth:`AppConfig.public_dump`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    wallex_api_key: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    alert_webhook_url: SecretStr | None = None

    def raw_values(self) -> list[str]:
        """Plain secret strings (for log masking)."""
        out = []
        for v in (self.wallex_api_key, self.llm_api_key, self.alert_webhook_url):
            if v is not None and v.get_secret_value():
                out.append(v.get_secret_value())
        return out


class EnvSettings(BaseSettings):
    """Environment variables listed in ``.env.example``."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)
    mode: ModeName | None = None
    wallex_api_key: SecretStr | None = None
    wallex_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_fallback_model: str | None = None
    https_proxy: str | None = None
    alert_webhook_url: SecretStr | None = None
    version_tag: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _empty_is_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and v.strip() == "" else v


class AppConfig(_C):
    base: BaseConfig = BaseConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()
    llm: LLMConfig = LLMConfig()
    wallex_base_url: str | None = None
    https_proxy: str | None = None
    secrets: Secrets = Field(default_factory=Secrets, repr=False)

    @property
    def mode(self) -> ModeName:
        """Effective run mode (env ``MODE`` overrides ``base.yaml``)."""
        return self.base.mode

    def public_dump(self) -> dict[str, Any]:
        """JSON-able dump of everything except secrets (for ``config_used.yaml``, hashing, logs)."""
        return self.model_dump(mode="json", exclude={"secrets"})


def config_hash(cfg: AppConfig) -> str:
    """``"sha256:<hex>"`` over the canonical JSON of the config without secrets."""
    payload = json.dumps(cfg.public_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return data


def load_config(
    path: str | os.PathLike[str] = "config", env_file: str | os.PathLike[str] | None = ".env"
) -> AppConfig:
    """Load ``AppConfig`` from ``<path>/{base,strategy,risk}.yaml`` then env (env wins).

    Raises ``pydantic.ValidationError`` with a field path and message on out-of-range values.
    """
    root = Path(path)
    base_raw = _read_yaml(root / "base.yaml")
    llm_raw = base_raw.pop("llm", {}) or {}
    strategy_raw = _read_yaml(root / "strategy.yaml")
    risk_raw = _read_yaml(root / "risk.yaml")

    env_path = Path(env_file) if env_file else None
    env = EnvSettings(_env_file=env_path if env_path and env_path.exists() else None)  # type: ignore[call-arg]

    if env.mode is not None:
        base_raw["mode"] = env.mode
    for key, val in (
        ("base_url", env.llm_base_url),
        ("model", env.llm_model),
        ("fallback_model", env.llm_fallback_model),
    ):
        if val is not None:
            llm_raw[key] = val

    return AppConfig(
        base=BaseConfig.model_validate(base_raw),
        strategy=StrategyConfig.model_validate(strategy_raw),
        risk=RiskConfig.model_validate(risk_raw),
        llm=LLMConfig.model_validate(llm_raw),
        wallex_base_url=env.wallex_base_url,
        https_proxy=env.https_proxy,
        secrets=Secrets(
            wallex_api_key=env.wallex_api_key,
            llm_api_key=env.llm_api_key,
            alert_webhook_url=env.alert_webhook_url,
        ),
    )
