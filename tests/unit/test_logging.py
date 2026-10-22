"""T-003 logging tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from siavash.logging import get_logger, mask_value, register_secrets, setup_logging
from siavash.runtime import get_runtime, init_runtime


def _records(log_dir: Path) -> list[dict]:
    for h in logging.getLogger().handlers:
        h.flush()
    lines = (log_dir / "system.log").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    init_runtime(mode="paper", config_hash="sha256:test")
    setup_logging("DEBUG", tmp_path, console=False)
    return tmp_path


def test_runtime_fields_in_every_record(log_dir: Path) -> None:
    log = get_logger("t")
    log.info("first", a=1)
    logging.getLogger("stdlib").warning("from stdlib")
    recs = _records(log_dir)
    assert len(recs) == 2
    rt = get_runtime()
    for r in recs:
        assert r["run_id"] == rt.run_id
        assert r["git_sha"]
        assert r["mode"] == "paper"
        assert r["config_hash"] == "sha256:test"


def test_sensitive_keys_masked(log_dir: Path) -> None:
    get_logger().info(
        "req", api_key="abcd1234", headers={"Authorization": "Bearer x", "X-Other": "ok"}, token="t"
    )
    r = _records(log_dir)[-1]
    assert r["api_key"] == "***"
    assert r["token"] == "***"
    assert r["headers"]["Authorization"] == "***"
    assert r["headers"]["X-Other"] == "ok"


def test_secret_env_values_masked(log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLEX_API_KEY", "WALLEX-REAL-KEY-998877")
    monkeypatch.setenv("LLM_API_KEY", "sk-llm-555444333")
    get_logger().info(
        "calling with WALLEX-REAL-KEY-998877",
        url="https://x/?k=sk-llm-555444333",
        nested=["sk-llm-555444333"],
    )
    raw = (log_dir / "system.log").read_text(encoding="utf-8")
    assert "WALLEX-REAL-KEY-998877" not in raw
    assert "sk-llm-555444333" not in raw
    assert "***" in raw


def test_registered_secret_masked() -> None:
    register_secrets(["registered-secret-xyz"])
    assert mask_value({"msg": "has registered-secret-xyz inside"}) == {"msg": "has *** inside"}
