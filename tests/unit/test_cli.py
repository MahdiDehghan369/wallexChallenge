"""T-1104: CLI (typer CliRunner)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from siavash.cli import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(ROOT / "config", tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CONFIRM_LIVE", raising=False)
    for v in ("MODE", "WALLEX_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def _decisions(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / "logs" / "decisions.jsonl").read_text().splitlines()]


def test_live_requires_confirmation(workdir: Path) -> None:
    res = runner.invoke(app, ["run", "--mode", "live"])
    assert res.exit_code == 2 and "CONFIRM_LIVE" in res.output
    assert runner.invoke(app, ["run", "--mode", "nope"]).exit_code != 0


def test_pause_resume_flatten_status(workdir: Path) -> None:
    assert runner.invoke(app, ["pause"]).exit_code == 0
    res = runner.invoke(app, ["status", "--json"])
    assert res.exit_code == 0, res.output
    info = json.loads(res.output)
    assert info["paused"] is True and info["breaker"]["state"] == "NORMAL" and info["open_positions"] == []
    assert runner.invoke(app, ["flatten"]).exit_code == 2  # needs --confirm
    assert runner.invoke(app, ["flatten", "--confirm"]).exit_code == 0
    assert runner.invoke(app, ["resume", "--confirm"]).exit_code == 0
    info = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert info["paused"] is False and info["flatten_requested"] is True
    cmds = [d["extra"]["command"] for d in _decisions(workdir)]
    assert cmds == ["pause", "flatten", "resume"]
    assert all(d["type"] == "OPERATOR_COMMAND" for d in _decisions(workdir))
    human = runner.invoke(app, ["status"])
    assert "breaker: NORMAL" in human.output and "last decisions:" in human.output


def test_every_command_has_help() -> None:
    for cmd in (
        "run",
        "status",
        "pause",
        "resume",
        "flatten",
        "report",
        "replay",
        "reflect",
        "export-submission",
        "llm-check",
    ):
        res = runner.invoke(app, [cmd, "--help"])
        assert res.exit_code == 0, cmd
