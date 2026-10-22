"""LLM abstraction (T-501).

OpenAI-compatible chat client (OpenRouter / any gateway via ``LLM_BASE_URL``), ``temperature=0``,
``llm.timeout_s``, ``llm.max_retries`` retries on network / 5xx / 429 errors, then one attempt on
``LLM_FALLBACK_MODEL``. ``complete_json`` runs a function-calling loop (read-only tools, at most
``max_tool_calls``), extracts the JSON object from the answer and validates it with pydantic.
Every call is appended to ``logs/llm/calls.jsonl`` (model, prompt_version, input_hash, tokens,
latency, status) — never the API key.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
import openai
from pydantic import BaseModel, ValidationError

from siavash.logging import get_logger, mask_value

log = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str] | str]


class LLMError(Exception):
    """LLM call failed (network, server, configuration)."""


class LLMTimeout(LLMError):
    pass


class LLMInvalidOutput(LLMError):
    """The model answered, but not with valid JSON for the schema."""

    def __init__(self, message: str, raw: str = "") -> None:
        super().__init__(message)
        self.raw = raw


RETRYABLE = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    openai.RateLimitError,
)


def canonical_json(obj: Any) -> str:
    """Sorted keys, floats rounded to 6 digits — stable hashing for caching / logging."""

    def norm(x: Any) -> Any:
        if isinstance(x, float):
            return round(x, 6)
        if isinstance(x, dict):
            return {str(k): norm(v) for k, v in x.items()}
        if isinstance(x, list | tuple):
            return [norm(v) for v in x]
        if isinstance(x, BaseModel):
            return norm(x.model_dump(mode="json"))
        return x

    return json.dumps(norm(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def input_hash(*parts: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(list(parts)).encode()).hexdigest()


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Parse the first JSON object in ``text`` (tolerates code fences / prose around it)."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE)
    try:
        v = json.loads(t)
    except json.JSONDecodeError:
        m = _JSON_RE.search(t)
        if not m:
            raise LLMInvalidOutput("no JSON object in model output", text) from None
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMInvalidOutput(f"invalid JSON: {e}", text) from e
    if not isinstance(v, dict):
        raise LLMInvalidOutput("JSON output is not an object", text)
    return v


class CallLog:
    """Append-only ``logs/llm/calls.jsonl``."""

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: dict[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps(mask_value(rec), ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class LLMClient:
    def __init__(
        self,
        model: str | None,
        api_key: str | None,
        base_url: str | None = None,
        fallback_model: str | None = None,
        timeout_s: float = 20.0,
        max_retries: int = 2,
        proxy: str | None = None,
        calls_log: str | Path | None = "logs/llm/calls.jsonl",
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.model = model
        self.fallback_model = fallback_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._sleep = sleep
        self.log = CallLog(calls_log)
        self.enabled = bool(model and api_key)
        http_kwargs: dict[str, Any] = {"timeout": timeout_s}
        if proxy:
            http_kwargs["proxy"] = proxy
        if transport is not None:
            http_kwargs["transport"] = transport
        self._client = openai.AsyncOpenAI(
            api_key=api_key or "missing",
            base_url=base_url,
            timeout=timeout_s,
            max_retries=0,
            http_client=httpx.AsyncClient(**http_kwargs),
        )

    @classmethod
    def from_config(cls, cfg: Any, **kw: Any) -> LLMClient:
        llm = cfg.llm
        key = cfg.secrets.llm_api_key.get_secret_value() if cfg.secrets.llm_api_key else None
        return cls(
            model=llm.model,
            api_key=key,
            base_url=llm.base_url,
            fallback_model=llm.fallback_model,
            timeout_s=llm.timeout_s,
            max_retries=llm.max_retries,
            proxy=cfg.https_proxy if llm.use_proxy else None,
            calls_log=Path(cfg.base.paths.logs) / "llm" / "calls.jsonl",
            **kw,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def _create(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> Any:
        kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
        if tools:
            kwargs["tools"] = tools
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await asyncio.wait_for(
                    self._client.chat.completions.create(**kwargs), timeout=self.timeout_s
                )
            except TimeoutError as e:
                last = LLMTimeout(f"timeout after {self.timeout_s}s")
                last.__cause__ = e
            except RETRYABLE as e:
                last = e
            except openai.APIError as e:  # 4xx etc: not retried
                raise LLMError(f"{type(e).__name__}: {e}") from e
            if attempt < self.max_retries:
                await self._sleep(min(2.0**attempt, 4.0))
        if isinstance(last, LLMError):
            raise last
        raise LLMError(f"{type(last).__name__}: {last}") from last

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
        """Return ``(parsed, meta)``; raises ``LLMError`` / ``LLMTimeout`` / ``LLMInvalidOutput``."""
        if not self.enabled:
            raise LLMError("LLM is not configured (LLM_MODEL / LLM_API_KEY)")
        user_text = user if isinstance(user, str) else canonical_json(user)
        ih = input_hash(system, user_text)
        models = [m for m in (self.model, self.fallback_model) if m]
        t0 = time.perf_counter()
        meta: dict[str, Any] = {"prompt_version": prompt_version, "input_hash": ih, "tool_calls": 0}
        status = "error"
        last_err: Exception | None = None
        try:
            for model in dict.fromkeys(models):
                meta["model"] = model
                try:
                    parsed, raw, usage, ncalls = await self._run(
                        model, system, user_text, schema, tools, tool_executor, max_tool_calls
                    )
                except (LLMTimeout, LLMError) as e:
                    if isinstance(e, LLMInvalidOutput):
                        raise
                    last_err = e
                    log.warning("llm_model_failed", model=model, error=str(e))
                    continue
                meta.update({"raw": raw, "usage": usage, "tool_calls": ncalls})
                status = "ok"
                return parsed, meta
            assert last_err is not None
            raise last_err
        except LLMInvalidOutput:
            status = "invalid_output"
            raise
        except LLMTimeout:
            status = "timeout"
            raise
        finally:
            meta["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            meta["status"] = status
            self.log.write(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "model": meta.get("model"),
                    "prompt_version": prompt_version,
                    "input_hash": ih,
                    "tokens": meta.get("usage"),
                    "latency_ms": meta["latency_ms"],
                    "status": status,
                    "tool_calls": meta.get("tool_calls", 0),
                }
            )

    async def _run(
        self,
        model: str,
        system: str,
        user_text: str,
        schema: type[T],
        tools: list[dict[str, Any]] | None,
        tool_executor: ToolExecutor | None,
        max_tool_calls: int,
    ) -> tuple[T, str, dict[str, int] | None, int]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
        ncalls = 0
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        while True:
            allow_tools = tools if (tools and tool_executor and ncalls < max_tool_calls) else None
            resp = await self._create(model, messages, allow_tools)
            if getattr(resp, "usage", None) is not None:
                usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                usage["completion_tokens"] += resp.usage.completion_tokens or 0
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            if calls and allow_tools and tool_executor is not None:
                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [c.model_dump() for c in calls],
                    }
                )
                for c in calls:
                    ncalls += 1
                    try:
                        args = json.loads(c.function.arguments or "{}")
                        res = tool_executor(c.function.name, args)
                        out = await res if inspect.isawaitable(res) else res
                    except Exception as e:  # tool errors are reported back to the model
                        out = json.dumps({"error": f"{type(e).__name__}: {e}"})
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": str(out)})
                continue
            raw = msg.content or ""
            data = extract_json(raw)
            try:
                return schema.model_validate(data), raw, usage, ncalls
            except ValidationError as e:
                raise LLMInvalidOutput(f"schema validation failed: {e}", raw) from e
