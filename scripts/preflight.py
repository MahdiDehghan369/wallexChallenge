#!/usr/bin/env python
"""Go-live preflight (T-1107): automatic PASS / FAIL / SKIP checks before ``make live``.

    python scripts/preflight.py              # on the bot server, before going live (human)
    python scripts/preflight.py --offline    # no network checks (CI / agent)
    python scripts/preflight.py --skip-tests # do not run the unit tests

Exit code 0 only if every executed check passed. A check never crashes the script: an exception is a FAIL.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Section 5 of WALLEX_AGENT_PLAN.md: risk.yaml may never exceed these values (rule 7).
SECTION5_CAPS: dict[str, float] = {
    "risk_per_trade_pct": 0.5,
    "max_position_pct": 20,
    "max_total_exposure_pct": 50,
    "max_concurrent_positions": 3,
    "max_positions_per_market": 1,
    "daily_loss_limit_pct": 2.0,
    "kill_switch_drawdown_pct": 5.0,
    "max_trades_per_hour": 4,
    "max_trades_per_day": 25,
}
SECTION5_MIN_COOLDOWNS = {"cooldown_after_exit_minutes": 30}
MIN_SHADOW_HOURS = 12
MIN_KEY_LEN = 16  # shorter values are placeholders (and would match ordinary text in the repo scan)

Result = tuple[str, str]  # (status, detail)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def check_tests(cfg: Any) -> Result:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/unit", "-q", "-x", "-p", "no:warnings"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    return ("PASS" if r.returncode == 0 else "FAIL"), last


def check_verified(cfg: Any) -> Result:
    from siavash.exchange import endpoints as ep

    return ("PASS", "endpoints.VERIFIED = True") if ep.VERIFIED else ("FAIL", "endpoints.VERIFIED is False")


def check_competition_end(cfg: Any) -> Result:
    end = cfg.risk.endgame.competition_end_utc
    if not end:
        return "FAIL", "risk.endgame.competition_end_utc is not set"
    ts = end if isinstance(end, datetime) else datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    if ts <= datetime.now(UTC):
        return "FAIL", f"competition_end_utc {ts.isoformat()} is in the past"
    return "PASS", f"ends {ts.isoformat()} ({(ts - datetime.now(UTC)).total_seconds() / 86400:.1f} days left)"


def check_denylist(cfg: Any) -> Result:
    from siavash.exchange.guard import SymbolGuard
    from siavash.exchange.markets import MarketRegistry

    path = ROOT / "config" / "denylist.txt"
    lines = [
        x.strip()
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip() and not x.startswith("#")
    ]
    guard = SymbolGuard.from_config(cfg, MarketRegistry(), ROOT / "config")
    n = len(guard.denylist)
    if n == 0:
        return "FAIL", "denylist is empty"
    if n != len(set(lines)):
        return "FAIL", f"loaded {n} symbols but the file has {len(set(lines))} entries"
    return "PASS", f"{n} denied symbols loaded"


def check_initial_scale(cfg: Any) -> Result:
    s = cfg.risk.initial_live_risk_scale
    return ("PASS" if s <= 0.5 else "FAIL"), f"initial_live_risk_scale = {s}"


def check_risk_caps(cfg: Any) -> Result:
    r = cfg.risk
    over = [f"{k}={getattr(r, k)} > {cap}" for k, cap in SECTION5_CAPS.items() if float(getattr(r, k)) > cap]
    under = [
        f"{k}={getattr(r, k)} < {v}" for k, v in SECTION5_MIN_COOLDOWNS.items() if float(getattr(r, k)) < v
    ]
    loss = r.cooldown_after_consecutive_losses
    if loss.losses > 2 or loss.minutes < 60:
        under.append(
            f"cooldown_after_consecutive_losses={loss.losses}/{loss.minutes}min (plan: 2 losses / 60 min)"
        )
    bad = over + under
    return ("FAIL", "; ".join(bad)) if bad else ("PASS", "all risk limits within section 5")


def check_shadow(cfg: Any) -> Result:
    import json

    report = ROOT / "reports" / "shadow" / "REPORT.md"
    if not report.exists():
        return "FAIL", "reports/shadow/REPORT.md missing (run the shadow, then make shadow-report)"
    days = None
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith("| days |"):
            days = float(line.split("|")[2])
    if days is None or days * 24 < MIN_SHADOW_HOURS:
        return "FAIL", f"shadow ran {0 if days is None else days * 24:.1f} h < {MIN_SHADOW_HOURS} h"
    crit = 0
    for f in (ROOT / "logs").glob("system.log*"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"critical"' in line.lower():
                try:
                    crit += json.loads(line).get("level") == "critical"
                except ValueError:
                    continue
    return ("PASS" if crit == 0 else "FAIL"), f"shadow {days * 24:.1f} h, critical log lines: {crit}"


def check_model(cfg: Any) -> Result:
    ml = cfg.strategy.ml
    if not ml.enabled:
        return "PASS", "ML disabled (no model needed)"
    from siavash.ml.registry import load_model

    m = load_model(ml.model_version, ROOT / cfg.base.paths.models)
    return "PASS", f"{m.version} loaded, feature set {m.metadata['feature_set_version']}"


def check_heartbeat(cfg: Any) -> Result:
    hb = ROOT / cfg.base.paths.state / "heartbeat"
    if not hb.exists():
        return (
            "FAIL",
            f"{hb.relative_to(ROOT)} missing (engine not running — expected before the first start)",
        )
    age = datetime.now(UTC).timestamp() - hb.stat().st_mtime
    limit = 2 * cfg.base.loop.heartbeat_s
    return ("PASS" if age <= limit else "FAIL"), f"heartbeat age {age:.0f} s (limit {limit:.0f} s)"


def check_git(cfg: Any) -> Result:
    dirty = _git("status", "--porcelain")
    tag = _git("describe", "--exact-match", "--tags", "HEAD")
    problems = []
    if dirty:
        problems.append(f"{len(dirty.splitlines())} uncommitted change(s)")
    if not tag.startswith("live-v"):
        problems.append("HEAD has no live-vX.Y tag (use scripts/deploy.sh)")
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", f"clean, tag {tag}")


def check_secrets(cfg: Any) -> Result:
    env = ROOT / ".env"
    if not env.exists():
        return "FAIL", ".env missing"
    problems = []
    if _git("ls-files", ".env"):
        problems.append(".env is tracked by git")
    mode = stat.S_IMODE(env.stat().st_mode)
    if os.name == "posix" and mode & 0o077:
        problems.append(f".env permissions {oct(mode)} (chmod 600; not possible on a Windows-mounted drive)")
    key = cfg.secrets.wallex_api_key.get_secret_value() if cfg.secrets.wallex_api_key else ""
    if not key:
        problems.append("WALLEX_API_KEY not set in .env")
    elif len(key) < MIN_KEY_LEN:
        problems.append(f"WALLEX_API_KEY looks like a placeholder ({len(key)} chars)")
    elif _git("grep", "-l", "-F", key, "--", "."):
        problems.append("the API key appears in a tracked file")
    return ("FAIL", "; ".join(problems)) if problems else ("PASS", "API key only in .env (600, untracked)")


def check_network_wallex(cfg: Any) -> Result:
    import asyncio

    from siavash.exchange import endpoints as ep
    from siavash.exchange.client import WallexHttp

    async def go() -> int:
        async with WallexHttp.from_config(cfg) as http:
            return len(ep.parse_markets(await http.get(ep.Paths.MARKETS)))

    return "PASS", f"public markets OK ({asyncio.run(go())} markets)"


def check_network_llm(cfg: Any) -> Result:
    import asyncio

    from pydantic import BaseModel

    from siavash.agents.llm import LLMClient

    class Pong(BaseModel):
        ok: bool

    async def go() -> str:
        client = LLMClient.from_config(cfg)
        try:
            _, meta = await client.complete_json(
                "Reply with JSON.", 'Return {"ok": true}.', Pong, prompt_version="preflight"
            )
            return str(meta.get("model"))
        finally:
            await client.aclose()

    if not cfg.secrets.llm_api_key:
        return "FAIL", "LLM_API_KEY not set"
    return "PASS", f"LLM answered ({asyncio.run(go())})"


CHECKS: list[tuple[str, Callable[[Any], Result], bool]] = [  # (name, fn, needs_network)
    ("unit tests", check_tests, False),
    ("endpoints verified", check_verified, False),
    ("competition end set", check_competition_end, False),
    ("denylist loaded", check_denylist, False),
    ("initial live risk ≤ 0.5", check_initial_scale, False),
    ("risk limits ≤ section 5", check_risk_caps, False),
    ("shadow ≥ 12 h, no critical", check_shadow, False),
    ("ML model consistent", check_model, False),
    ("heartbeat fresh", check_heartbeat, False),
    ("git clean + live tag", check_git, False),
    ("API key only in .env", check_secrets, False),
    ("network: Wallex public API", check_network_wallex, True),
    ("network: LLM", check_network_llm, True),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip checks that need the network")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config"))
    args = ap.parse_args(argv)

    from siavash.config import load_config

    cfg = load_config(args.config, env_file=ROOT / ".env")
    rows: list[tuple[str, str, str]] = []
    for name, fn, network in CHECKS:
        if (network and args.offline) or (fn is check_tests and args.skip_tests):
            rows.append((name, "SKIP", "--offline" if network else "--skip-tests"))
            continue
        try:
            status, detail = fn(cfg)
        except Exception as e:  # a check must never crash the preflight
            status, detail = "FAIL", f"{type(e).__name__}: {e}"[:200]
        rows.append((name, status, detail))
    width = max(len(r[0]) for r in rows)
    for name, status, detail in rows:
        print(f"{name:<{width}}  {status:<4}  {detail}")
    failed = [r for r in rows if r[1] == "FAIL"]
    print(
        f"\n{len(rows) - len(failed)} passed/skipped, {len(failed)} failed -> {'READY' if not failed else 'NOT READY'} for live"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
