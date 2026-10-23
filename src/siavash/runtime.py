"""Runtime identity of the current process (T-003).

Every log line and decision record carries ``run_id``, ``git_sha``, ``version_tag``, ``mode`` and
``config_hash`` so a judge can tie any decision to the exact code and config that produced it.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID


@dataclass(frozen=True)
class RuntimeInfo:
    run_id: str
    git_sha: str
    version_tag: str | None
    mode: str
    config_hash: str

    def as_dict(self) -> dict[str, str | None]:
        """Fields for log/record enrichment."""
        return {
            "run_id": self.run_id,
            "git_sha": self.git_sha,
            "version_tag": self.version_tag,
            "mode": self.mode,
            "config_hash": self.config_hash,
        }


def detect_git_sha(cwd: str | os.PathLike[str] | None = None) -> str:
    """``GIT_SHA`` env (set at docker build) -> ``git rev-parse --short HEAD`` -> ``"unknown"``."""
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd or Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        sha = out.stdout.strip()
        return sha or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


_RUNTIME: RuntimeInfo | None = None


def init_runtime(mode: str = "paper", config_hash: str = "unknown", run_id: str | None = None) -> RuntimeInfo:
    """(Re)initialise the process-wide runtime info."""
    global _RUNTIME
    _RUNTIME = RuntimeInfo(
        run_id=run_id or str(ULID()),
        git_sha=detect_git_sha(),
        version_tag=(os.environ.get("VERSION_TAG") or "").strip() or None,
        mode=mode,
        config_hash=config_hash,
    )
    return _RUNTIME


def get_runtime() -> RuntimeInfo:
    """Process-wide singleton; lazily created with defaults if :func:`init_runtime` was not called."""
    if _RUNTIME is None:
        return init_runtime()
    return _RUNTIME


def update_runtime(**changes: str | None) -> RuntimeInfo:
    """Replace selected fields (e.g. ``mode`` or ``config_hash`` once config is loaded)."""
    global _RUNTIME
    _RUNTIME = replace(get_runtime(), **changes)  # type: ignore[arg-type]
    return _RUNTIME


def write_identity(path: str | os.PathLike[str], **extra: Any) -> Path:
    """Write the engine's identity (``state/engine.json``) so other processes (dashboard) can show it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **get_runtime().as_dict(),
        "started_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        **extra,
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return p
