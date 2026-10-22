"""LLM response cache and replay (T-502).

Key: ``"sha256:" + sha256(model + "|" + prompt_version + "|" + canonical_json(input))`` (sorted keys,
floats rounded to 6 digits). Modes:
- ``live``: return a cached answer when present, otherwise call the model and store the answer;
- ``replay``: cache only — a miss raises ``CacheMiss`` (no network), so a judge can reproduce
  decisions exactly;
- ``record_only``: always call the model and (over)write the cache.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from siavash.agents.llm import LLMClient, LLMError, ToolExecutor, canonical_json

T = TypeVar("T", bound=BaseModel)
CacheMode = Literal["live", "replay", "record_only"]


class CacheMiss(LLMError):
    """Replay mode and no cached answer for this input."""


def cache_key(model: str, prompt_version: str, payload: Any) -> str:
    return (
        "sha256:" + hashlib.sha256(f"{model}|{prompt_version}|{canonical_json(payload)}".encode()).hexdigest()
    )


class LLMCache:
    def __init__(self, path: str | Path = "logs/llm/cache.sqlite", mode: CacheMode = "live") -> None:
        self.path = str(path)
        self.mode = mode
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, model TEXT, prompt_version TEXT, "
                "response TEXT NOT NULL, raw TEXT, created_at TEXT)"
            )
            self._conn.commit()

    def get(self, key: str) -> tuple[str, str] | None:
        """``(response_json, raw_text)`` or ``None``."""
        with self._lock:
            row = self._conn.execute("SELECT response, raw FROM cache WHERE key=?", (key,)).fetchone()
        return (row[0], row[1] or "") if row else None

    def put(self, key: str, model: str, prompt_version: str, response_json: str, raw: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?)",
                (key, model, prompt_version, response_json, raw, datetime.now(UTC).isoformat()),
            )
            self._conn.commit()

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class CachedLLMClient:
    """Same ``complete_json`` interface as ``LLMClient``, with caching / replay."""

    def __init__(self, client: LLMClient | None, cache: LLMCache, model_name: str | None = None) -> None:
        self.client = client
        self.cache = cache
        self.model_name = model_name or (client.model if client else None) or "unknown"

    @property
    def enabled(self) -> bool:
        return self.cache.mode == "replay" or bool(self.client and self.client.enabled)

    async def complete_json(
        self,
        system: str,
        user: str | dict[str, Any],
        schema: type[T],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_calls: int = 3,
        prompt_version: str | None = None,
    ) -> tuple[T, dict[str, Any]]:
        pv = prompt_version or "unversioned"
        payload = {
            "system": system,
            "user": user,
            "tools": sorted(t["function"]["name"] for t in tools or []),
        }
        key = cache_key(self.model_name, pv, payload)
        if self.cache.mode != "record_only":
            hit = self.cache.get(key)
            if hit is not None:
                return schema.model_validate_json(hit[0]), {
                    "cache_key": key,
                    "cache_hit": True,
                    "model": self.model_name,
                    "raw": hit[1],
                    "status": "ok",
                    "prompt_version": pv,
                }
            if self.cache.mode == "replay":
                raise CacheMiss(f"no cached LLM answer for {key}")
        if self.client is None:
            raise LLMError("no LLM client configured")
        parsed, meta = await self.client.complete_json(
            system, user, schema, tools, tool_executor, max_tool_calls, pv
        )
        self.cache.put(key, self.model_name, pv, parsed.model_dump_json(), str(meta.get("raw", "")))
        meta.update({"cache_key": key, "cache_hit": False})
        return parsed, meta
