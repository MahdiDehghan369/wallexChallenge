"""T-002 config tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from siavash.config import AppConfig, config_hash, load_config

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
ENV_VARS = (
    "MODE",
    "WALLEX_API_KEY",
    "WALLEX_BASE_URL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_FALLBACK_MODEL",
    "HTTPS_PROXY",
    "ALERT_WEBHOOK_URL",
    "VERSION_TAG",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ENV_VARS:
        monkeypatch.delenv(v, raising=False)


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, d)
    return d


def _patch_yaml(path: Path, **updates: object) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data.update(updates)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_load_repo_config() -> None:
    cfg = load_config(REPO_CONFIG, env_file=None)
    assert isinstance(cfg, AppConfig)
    assert cfg.mode == "paper"
    assert cfg.risk.max_position_pct == 20
    assert cfg.risk.regime_multipliers["PANIC"] == 0.0
    assert cfg.strategy.s1.sl_atr == 1.5
    assert cfg.strategy.ml.enabled is False
    assert cfg.llm.reviewer.enabled is True
    assert "S3_IMPLIED_DEV" in cfg.strategy.strategies.enabled
    assert cfg.base.loop.main_loop_s == 60
    assert cfg.risk.endgame.competition_end_utc is None


def test_env_overrides_yaml(cfg_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("MODE=backtest\nLLM_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "from-process")
    cfg = load_config(cfg_dir, env_file=env_file)
    assert cfg.mode == "backtest"
    assert cfg.llm.model == "from-process"


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("risk_per_trade_pct", 1.5, "risk_per_trade_pct"),
        ("max_position_pct", 30, "max_position_pct"),
        ("max_total_exposure_pct", 70, "max_total_exposure_pct"),
        ("kill_switch_drawdown_pct", 9, "kill_switch_drawdown_pct"),
        ("daily_loss_limit_pct", 4, "daily_loss_limit_pct"),
        ("llm_size_multiplier_range", [0.1, 1.0], "llm_size_multiplier_range"),
        ("llm_size_multiplier_range", [0.5, 1.2], "llm_size_multiplier_range"),
    ],
)
def test_out_of_range_rejected(cfg_dir: Path, field: str, value: object, fragment: str) -> None:
    _patch_yaml(cfg_dir / "risk.yaml", **{field: value})
    with pytest.raises(ValidationError) as ei:
        load_config(cfg_dir, env_file=None)
    assert fragment in str(ei.value)


def test_hash_equal_for_same_config(cfg_dir: Path) -> None:
    a = load_config(cfg_dir, env_file=None)
    b = load_config(cfg_dir, env_file=None)
    assert config_hash(a) == config_hash(b)
    assert config_hash(a).startswith("sha256:")


def test_hash_changes_with_param(cfg_dir: Path) -> None:
    a = config_hash(load_config(cfg_dir, env_file=None))
    _patch_yaml(cfg_dir / "risk.yaml", max_spread_pct=0.25)
    b = config_hash(load_config(cfg_dir, env_file=None))
    assert a != b


def test_secrets_not_in_hash_or_dump(cfg_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base_hash = config_hash(load_config(cfg_dir, env_file=None))
    monkeypatch.setenv("WALLEX_API_KEY", "super-secret-wallex-key-123456")
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-secret-abcdef")
    cfg = load_config(cfg_dir, env_file=None)
    assert cfg.secrets.wallex_api_key is not None
    assert cfg.secrets.wallex_api_key.get_secret_value() == "super-secret-wallex-key-123456"
    assert config_hash(cfg) == base_hash
    dumped = str(cfg.public_dump()) + repr(cfg) + str(cfg)
    assert "super-secret-wallex-key-123456" not in dumped
    assert "sk-llm-secret-abcdef" not in dumped


def test_endgame_placeholder_and_value(cfg_dir: Path) -> None:
    risk = yaml.safe_load((cfg_dir / "risk.yaml").read_text(encoding="utf-8"))
    risk["endgame"]["competition_end_utc"] = "YYYY-MM-DDTHH:MM:00Z"
    (cfg_dir / "risk.yaml").write_text(yaml.safe_dump(risk), encoding="utf-8")
    assert load_config(cfg_dir, env_file=None).risk.endgame.competition_end_utc is None
    risk["endgame"]["competition_end_utc"] = "2026-11-08T20:30:00Z"
    (cfg_dir / "risk.yaml").write_text(yaml.safe_dump(risk), encoding="utf-8")
    end = load_config(cfg_dir, env_file=None).risk.endgame.competition_end_utc
    assert end is not None and end.tzinfo is not None
