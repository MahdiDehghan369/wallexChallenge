"""T-1107: preflight never crashes offline; risk caps and end-time checks work."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("preflight", ROOT / "scripts" / "preflight.py")
assert spec and spec.loader
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

from siavash.config import load_config  # noqa: E402


def test_offline_runs_without_crash(capsys) -> None:  # type: ignore[no-untyped-def]
    code = pf.main(["--offline", "--skip-tests"])
    out = capsys.readouterr().out
    assert code in (0, 1)
    assert "network: Wallex public API" in out and "SKIP" in out and "for live" in out
    assert "Traceback" not in out


def test_risk_caps() -> None:
    cfg = load_config("config")
    assert pf.check_risk_caps(cfg)[0] == "PASS"
    risky = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"max_position_pct": 25})})
    status, detail = pf.check_risk_caps(risky)
    assert status == "FAIL" and "max_position_pct" in detail


def test_competition_end() -> None:
    cfg = load_config("config")
    end = cfg.risk.endgame.model_copy(update={"competition_end_utc": datetime.now(UTC) + timedelta(days=3)})
    ok = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"endgame": end})})
    assert pf.check_competition_end(ok)[0] == "PASS"
    past = end.model_copy(update={"competition_end_utc": datetime.now(UTC) - timedelta(days=1)})
    old = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"endgame": past})})
    assert pf.check_competition_end(old)[0] == "FAIL"
