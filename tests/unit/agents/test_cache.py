"""T-502: LLM cache / replay."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel

from siavash.agents.cache import CachedLLMClient, CacheMiss, LLMCache, cache_key
from siavash.agents.llm import LLMClient
from tests.unit.agents.test_llm import BASE, KEY, completion


class Out(BaseModel):
    verdict: str


def _client(tmp_path: Path) -> LLMClient:
    return LLMClient("m", KEY, BASE, calls_log=tmp_path / "calls.jsonl", max_retries=0)


@respx.mock
async def test_same_input_one_http_request(tmp_path: Path) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"verdict": "APPROVE"}'))
    )
    cached = CachedLLMClient(_client(tmp_path), LLMCache(tmp_path / "cache.sqlite", "live"))
    a, m1 = await cached.complete_json("sys", {"x": 1.0000001, "y": [1, 2]}, Out, prompt_version="v1")
    b, m2 = await cached.complete_json(
        "sys", {"y": [1, 2], "x": 1.0000004}, Out, prompt_version="v1"
    )  # same canonical input
    assert a == b and route.call_count == 1
    assert m1["cache_hit"] is False and m2["cache_hit"] is True and m1["cache_key"] == m2["cache_key"]
    await cached.complete_json("sys", {"x": 1.0}, Out, prompt_version="v2")  # new prompt version -> new call
    assert route.call_count == 2


async def test_replay_without_network(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    live = LLMCache(path, "live")
    key = cache_key("m", "v1", {"system": "sys", "user": "u", "tools": []})
    live.put(key, "m", "v1", '{"verdict": "REJECT"}', "raw")
    live.close()
    with respx.mock(assert_all_called=False) as router:
        route = router.post(f"{BASE}/chat/completions")
        replay = CachedLLMClient(None, LLMCache(path, "replay"), model_name="m")
        out, meta = await replay.complete_json("sys", "u", Out, prompt_version="v1")
        assert out.verdict == "REJECT" and meta["cache_hit"] and replay.enabled
        with pytest.raises(CacheMiss):
            await replay.complete_json("sys", "other", Out, prompt_version="v1")
        assert route.call_count == 0


@respx.mock
async def test_record_only_overwrites(tmp_path: Path) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=completion('{"verdict": "A"}')),
            httpx.Response(200, json=completion('{"verdict": "B"}')),
        ]
    )
    cache = LLMCache(tmp_path / "c.sqlite", "record_only")
    c = CachedLLMClient(_client(tmp_path), cache)
    await c.complete_json("s", "u", Out, prompt_version="v")
    out, _ = await c.complete_json("s", "u", Out, prompt_version="v")
    assert out.verdict == "B" and len(cache) == 1
