"""Structured logging (T-003).

JSON lines to ``<log_dir>/system.log`` (rotating) and a human console renderer. Every record is
enriched with runtime fields and passed through a secret mask.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from collections.abc import Iterable, MutableMapping
from pathlib import Path
from typing import Any

import structlog

from siavash.runtime import get_runtime

MASK = "***"
SENSITIVE_KEY_RE = re.compile(r"key|token|secret|password|authorization", re.IGNORECASE)
SECRET_ENV_VARS = ("WALLEX_API_KEY", "LLM_API_KEY")

_extra_secrets: set[str] = set()


def register_secrets(values: Iterable[str]) -> None:
    """Add literal secret values that must never appear in logs."""
    for v in values:
        if v and len(v) >= 4:
            _extra_secrets.add(v)


def _secret_values() -> list[str]:
    vals = set(_extra_secrets)
    for name in SECRET_ENV_VARS:
        v = os.environ.get(name, "")
        if v and len(v) >= 4:
            vals.add(v)
    return sorted(vals, key=len, reverse=True)


def mask_value(value: Any, secrets: list[str] | None = None) -> Any:
    """Recursively mask sensitive keys and literal secret values inside ``value``."""
    secrets = _secret_values() if secrets is None else secrets
    if isinstance(value, str):
        for s in secrets:
            if s in value:
                value = value.replace(s, MASK)
        return value
    if isinstance(value, MutableMapping):
        return {
            k: (MASK if isinstance(k, str) and SENSITIVE_KEY_RE.search(k) else mask_value(v, secrets))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(mask_value(v, secrets) for v in value)
    return value


def mask_secrets(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: mask sensitive keys and secret values."""
    secrets = _secret_values()
    for k in list(event_dict.keys()):
        if k != "event" and SENSITIVE_KEY_RE.search(k):
            event_dict[k] = MASK
        else:
            event_dict[k] = mask_value(event_dict[k], secrets)
    return event_dict


def add_runtime(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor: add run_id / git_sha / version_tag / mode / config_hash."""
    for k, v in get_runtime().as_dict().items():
        event_dict.setdefault(k, v)
    return event_dict


def _shared_processors() -> list[Any]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_runtime,
        mask_secrets,
    ]


def setup_logging(
    level: str = "INFO",
    log_dir: str | os.PathLike[str] = "logs",
    console: bool = True,
    max_bytes: int = 20 * 1024 * 1024,
    backup_count: int = 10,
) -> None:
    """Configure structlog + stdlib logging. Safe to call more than once (handlers are replaced)."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    shared = _shared_processors()

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    file_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "system.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False, default=str),
            ],
            foreign_pre_chain=shared,
        )
    )

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.addHandler(file_handler)
    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
                ],
                foreign_pre_chain=shared,
            )
        )
        root.addHandler(console_handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> Any:
    """Return a structlog bound logger."""
    return structlog.get_logger(name)
