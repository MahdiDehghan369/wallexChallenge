"""Go-live on Liara: env MODE reaches the engine, fees never below the account's, paths on the persistent disk."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs
from typer.testing import CliRunner

from siavash import cli
from siavash.config import load_config
from siavash.exchange.fees import apply_account_fees, merge_fees, traded_markets
from siavash.runtime import get_runtime, init_runtime, write_identity

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    for v in ("MODE", "CONFIRM_LIVE", "WALLEX_API_KEY", "LLM_API_KEY", "PORT", "PATHS_ROOT"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


@pytest.fixture
def engine_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake_engine(cfg: Any, mode: str) -> None:
        calls.append((mode, cfg.mode))

    monkeypatch.setattr(cli, "_run_engine", fake_engine)
    return calls


# ---------------------------------------------------------------- mode resolution (Liara stayed in paper)


def test_env_mode_live_still_needs_confirm_live(
    workdir: Path, engine_calls: list[Any], monkeypatch: Any
) -> None:
    monkeypatch.setenv("MODE", "live")
    res = runner.invoke(cli.app, ["run"])
    assert res.exit_code == 2 and "CONFIRM_LIVE" in res.output and engine_calls == []


def test_env_mode_live_reaches_engine(workdir: Path, engine_calls: list[Any], monkeypatch: Any) -> None:
    monkeypatch.setenv("MODE", "live")
    monkeypatch.setenv("CONFIRM_LIVE", "YES")
    res = runner.invoke(cli.app, ["run"])
    assert res.exit_code == 0, res.output
    assert engine_calls == [("live", "live")] and get_runtime().mode == "live"
    rec = json.loads((workdir / "logs" / "decisions.jsonl").read_text().splitlines()[-1])
    assert rec["extra"] == {"command": "run", "mode": "live"} and rec["mode"] == "live"


def test_default_mode_is_base_yaml_paper(workdir: Path, engine_calls: list[Any]) -> None:
    assert runner.invoke(cli.app, ["run"]).exit_code == 0
    assert engine_calls == [("paper", "paper")]


def test_explicit_flag_wins_and_warns(workdir: Path, engine_calls: list[Any], monkeypatch: Any) -> None:
    monkeypatch.setenv("MODE", "live")
    assert runner.invoke(cli.app, ["run", "--mode", "paper"]).exit_code == 0
    assert engine_calls == [("paper", "paper")]
    events = [json.loads(x) for x in (workdir / "logs" / "system.log").read_text().splitlines() if x.strip()]
    warn = next(e for e in events if e["event"] == "engine_mode_flag_overrides_env")
    assert warn["mode"] == "paper" and warn["configured_mode"] == "live"
    assert any(e["event"] == "engine_mode" for e in events)


def test_dockerfile_takes_mode_from_env() -> None:
    lines = [x.strip() for x in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()]
    assert 'CMD ["run"]' in lines and not any(x.startswith("CMD") and "--mode" in x for x in lines)
    assert 'ENTRYPOINT ["siavash"]' in lines
    assert lines.index("ARG VERSION_TAG") < next(
        i for i, x in enumerate(lines) if "VERSION_TAG=$VERSION_TAG" in x
    )
    assert "COPY Makefile CHANGELOG.md ./" in lines  # make report / export-submission in the container
    assert not any(
        x.strip() in ("Makefile", "CHANGELOG.md") for x in (ROOT / ".dockerignore").read_text().splitlines()
    )


# ---------------------------------------------------------------- fees (never below the account's)

API = {
    "BTC/TMN": (Decimal("0.2"), Decimal("0.3")),
    "USDT/TMN": (Decimal("0.1"), Decimal("0.35")),
    "BTC/USDT": (Decimal("0.9"), Decimal("0.9")),  # not traded (quote USDT): ignored
}


def _cfg(maker: float | None, taker: float) -> Any:
    cfg = load_config(ROOT / "config", env_file=None)
    fees = cfg.risk.fees.model_copy(update={"maker_pct": maker, "taker_pct": taker})
    return cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"fees": fees})})


def test_traded_markets_are_allowed_quotes_only() -> None:
    m = traded_markets(load_config(ROOT / "config", env_file=None))
    assert "BTC/TMN" in m and "USDT/TMN" in m and not any(s.endswith("/USDT") for s in m)


def test_higher_account_fees_are_used_and_logged() -> None:
    with capture_logs() as logs:
        cfg, info = merge_fees(_cfg(0.25, 0.25), API)
    assert (cfg.risk.fees.maker_pct, cfg.risk.fees.taker_pct) == (0.25, 0.35)  # maker cfg 0.25 > api 0.2
    assert info["source"] == "account" and set(info["account"]) == {"BTC/TMN", "USDT/TMN"}
    events = {e["event"]: e for e in logs}
    assert "account_fees" in events and events["account_fees_differ"]["log_level"] == "warning"


def test_lower_account_fees_never_lower_config() -> None:
    cfg, info = merge_fees(_cfg(0.5, 0.5), API)
    assert (cfg.risk.fees.maker_pct, cfg.risk.fees.taker_pct) == (0.5, 0.5) and info["source"] == "config"
    cfg2, _ = merge_fees(_cfg(None, 0.25), API)  # maker None = taker
    assert cfg2.risk.fees.maker_pct == 0.25 and cfg2.risk.fees.taker_pct == 0.35


def test_equal_fees_no_warning() -> None:
    with capture_logs() as logs:
        merge_fees(_cfg(0.2, 0.35), API)
    assert not any(e["event"] == "account_fees_differ" for e in logs)


def test_fee_api_failure_keeps_config() -> None:
    async def boom() -> Any:
        raise RuntimeError("down")

    with capture_logs() as logs:
        cfg, info = asyncio.run(apply_account_fees(_cfg(0.25, 0.25), boom))
    assert cfg.risk.fees.taker_pct == 0.25 and info["error"] == "RuntimeError"
    assert any(e["event"] == "account_fees_unavailable" and e["log_level"] == "error" for e in logs)


def test_no_traded_market_in_api_keeps_config() -> None:
    cfg, info = merge_fees(_cfg(0.25, 0.25), {"BTC/USDT": (Decimal("1"), Decimal("1"))})
    assert cfg.risk.fees.taker_pct == 0.25 and info["source"] == "config"


def test_engine_identity_file(tmp_path: Path) -> None:
    init_runtime(mode="live", config_hash="sha256:x")
    p = write_identity(tmp_path / "state" / "engine.json", fees={"source": "account"})
    rec = json.loads(p.read_text())
    assert rec["mode"] == "live" and rec["config_hash"] == "sha256:x" and rec["fees"] == {"source": "account"}
    assert {"git_sha", "version_tag", "started_at", "pid", "run_id"} <= set(rec)


# ---------------------------------------------------------------- paths on the persistent disk (Liara)


def test_paths_root_prefixes_relative_paths(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATHS_ROOT", "/app/persist")
    p = load_config("config", env_file=None).base.paths
    assert p.logs == "/app/persist/logs" and p.state == "/app/persist/state"
    assert p.reports == "/app/persist/reports" and p.submission == "/app/persist/submission"
    monkeypatch.delenv("PATHS_ROOT")
    assert load_config("config", env_file=None).base.paths.logs == "logs"


def test_report_defaults_follow_paths(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist = workdir / "persist"
    (persist / "logs").mkdir(parents=True)
    monkeypatch.setenv("PATHS_ROOT", str(persist))
    res = runner.invoke(cli.app, ["report"])
    assert res.exit_code == 0, res.output
    assert (persist / "reports" / "live" / "REPORT.md").exists()


def _export_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "export_submission", ROOT / "scripts" / "export_submission.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["export_submission"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_export_submission_defaults_follow_paths(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    persist = workdir / "persist"
    (persist / "logs").mkdir(parents=True)
    (persist / "logs" / "decisions.jsonl").write_text('{"decision_id": "d1"}\n')
    (persist / "reports" / "live").mkdir(parents=True)
    (persist / "reports" / "live" / "REPORT.md").write_text("# live\n")
    monkeypatch.setenv("PATHS_ROOT", str(persist))
    ex = _export_module()
    assert ex.main(["--env-file", str(workdir / "no.env")]) == 0
    out = persist / "submission"
    assert (out / "logs" / "decisions.jsonl").exists() and (out / "reports" / "live" / "REPORT.md").exists()
    assert (out / "MANIFEST.md").exists() and (out / "SHA256SUMS").exists()
