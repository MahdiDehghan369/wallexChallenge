"""T-501: LLM budget guard — caps → fallback, persistence across restarts, cache hits not counted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from siavash.agents.budget import BudgetExceeded, LLMBudget, estimate_tokens
from siavash.agents.cache import CachedLLMClient, LLMCache
from siavash.agents.llm import LLMClient
from siavash.config import LLMBudgetConfig
from siavash.engine.store import SqliteStore

BASE = "https://llm.test/v1"


class Out(BaseModel):
    verdict: str


def completion(content: str, prompt_tokens: int = 100, completion_tokens: int = 20) -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


def cfg(**kw: int) -> LLMBudgetConfig:
    base = {
        "max_calls_per_hour": 3,
        "max_calls_per_day": 5,
        "max_tokens_per_day": 10_000,
        "max_input_tokens_per_call": 100,
    }
    base.update(kw)
    return LLMBudgetConfig(**base)


T0 = datetime(2026, 11, 5, 8, 0, tzinfo=UTC)  # 11:30 Tehran


async def test_hourly_and_daily_call_caps() -> None:
    clock = Clock(T0)
    b = LLMBudget(cfg(), now_fn=clock.now)
    for _ in range(3):
        await b.check("hi")
        await b.record(10)
    with pytest.raises(BudgetExceeded, match="max_calls_per_hour"):
        await b.check("hi")
    clock.t = T0 + timedelta(hours=1)  # new Tehran hour
    for _ in range(2):
        await b.check("hi")
        await b.record(10)
    with pytest.raises(BudgetExceeded, match="max_calls_per_day"):
        await b.check("hi")
    clock.t = T0 + timedelta(hours=13)  # 00:30 Tehran next day -> daily counters reset
    await b.check("hi")


async def test_token_cap_and_input_size() -> None:
    alerts: list[str] = []
    b = LLMBudget(cfg(max_tokens_per_day=150), now_fn=Clock(T0).now, on_exceeded=alerts.append)
    await b.record(200)
    with pytest.raises(BudgetExceeded, match="max_tokens_per_day"):
        await b.check()
    with pytest.raises(BudgetExceeded, match="input"):
        await b.check("x" * 301)  # ~101 tokens > 100
    assert estimate_tokens("x" * 300) == 100
    assert len(alerts) == 2


async def test_counters_survive_restart(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "state.sqlite")
    clock = Clock(T0)
    b = LLMBudget(cfg(), store, now_fn=clock.now)
    for _ in range(3):
        await b.record(10)
    await store.close()

    store2 = SqliteStore(tmp_path / "state.sqlite")
    b2 = LLMBudget(cfg(), store2, now_fn=clock.now)
    with pytest.raises(BudgetExceeded, match="max_calls_per_hour"):
        await b2.check("hi")
    assert b2.snapshot()["day_tokens"] == 30
    await store2.close()


@respx.mock
async def test_client_falls_back_and_cache_hits_are_free(tmp_path: Path) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"verdict": "APPROVE"}'))
    )
    budget = LLMBudget(cfg(max_calls_per_hour=1), now_fn=Clock(T0).now)
    client = LLMClient("m", "k", BASE, calls_log=tmp_path / "calls.jsonl", budget=budget, fallback_model="fb")
    cached = CachedLLMClient(client, LLMCache(":memory:", "live"))

    await cached.complete_json("sys", {"a": 1}, Out, prompt_version="v1")
    assert route.call_count == 1 and budget.snapshot()["day_tokens"] == 120
    # identical input -> cache hit: no request, no budget use
    _, meta = await cached.complete_json("sys", {"a": 1}, Out, prompt_version="v1")
    assert meta["cache_hit"] and route.call_count == 1 and budget.snapshot()["hour_calls"] == 1
    # new input while the hourly cap is reached -> BudgetExceeded, fallback model NOT tried
    with pytest.raises(BudgetExceeded):
        await cached.complete_json("sys", {"a": 2}, Out, prompt_version="v1")
    assert route.call_count == 1
    last = (tmp_path / "calls.jsonl").read_text().splitlines()[-1]
    assert '"status": "budget_exceeded"' in last
