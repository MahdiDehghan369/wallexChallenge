"""T-501: LLMClient with a mocked OpenAI-compatible HTTP API (respx)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel

from siavash.agents.llm import LLMClient, LLMError, LLMInvalidOutput, LLMTimeout, extract_json, input_hash

BASE = "https://llm.test/v1"
KEY = "sk-test-llm-key-123456"


class Out(BaseModel):
    verdict: str
    size_multiplier: float


def completion(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


async def _nosleep(_: float) -> None:
    return None


def client(tmp_path: Path, **kw: Any) -> LLMClient:
    return LLMClient("m", KEY, BASE, calls_log=tmp_path / "calls.jsonl", sleep=_nosleep, **kw)


@respx.mock
async def test_valid_response_and_log(tmp_path: Path) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200, json=completion('```json\n{"verdict": "APPROVE", "size_multiplier": 0.75}\n```')
        )
    )
    c = client(tmp_path)
    out, meta = await c.complete_json("sys", {"x": 1.123456789}, Out, prompt_version="reviewer_v1")
    assert out.verdict == "APPROVE" and out.size_multiplier == 0.75
    body = json.loads(route.calls.last.request.content)
    assert body["temperature"] == 0 and body["model"] == "m"
    assert meta["status"] == "ok" and meta["usage"]["prompt_tokens"] == 10
    rec = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert (
        rec["prompt_version"] == "reviewer_v1"
        and rec["status"] == "ok"
        and rec["input_hash"].startswith("sha256:")
    )
    assert KEY not in (tmp_path / "calls.jsonl").read_text()


@respx.mock
async def test_broken_json(tmp_path: Path) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion("I think APPROVE"))
    )
    with pytest.raises(LLMInvalidOutput):
        await client(tmp_path).complete_json("s", "u", Out)
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"verdict": "X"}'))
    )
    with pytest.raises(LLMInvalidOutput, match="schema"):
        await client(tmp_path).complete_json("s", "u", Out)
    assert json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])["status"] == "invalid_output"


@respx.mock
async def test_timeout_and_fallback_model(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "m":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=completion('{"verdict": "REJECT", "size_multiplier": 0.25}'))

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    out, meta = await client(tmp_path, fallback_model="fb").complete_json("s", "u", Out)
    assert out.verdict == "REJECT" and meta["model"] == "fb"
    assert calls == ["m", "m", "m", "fb"]  # 1 try + 2 retries, then fallback


@respx.mock
async def test_all_fail(tmp_path: Path) -> None:
    respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LLMError):
        await client(tmp_path).complete_json("s", "u", Out)
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": {"message": "bad"}})
    )
    with pytest.raises(LLMError):
        await client(tmp_path).complete_json("s", "u", Out)


async def test_hard_timeout(tmp_path: Path) -> None:
    import asyncio

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return httpx.Response(200, json=completion("{}"))

    with respx.mock:
        respx.post(f"{BASE}/chat/completions").mock(side_effect=slow)
        c = LLMClient("m", KEY, BASE, timeout_s=0.05, max_retries=0, calls_log=None)
        with pytest.raises(LLMTimeout):
            await c.complete_json("s", "u", Out)


@respx.mock
async def test_one_tool_round(tmp_path: Path) -> None:
    tool_call = {
        "id": "t1",
        "type": "function",
        "function": {"name": "get_ticker", "arguments": '{"symbol": "BTC/TMN"}'},
    }
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=completion(None, [tool_call])),
            httpx.Response(200, json=completion('{"verdict": "APPROVE", "size_multiplier": 1.0}')),
        ]
    )
    seen: list[tuple[str, dict[str, Any]]] = []

    async def executor(name: str, args: dict[str, Any]) -> str:
        seen.append((name, args))
        return '{"last": 1}'

    tools = [
        {
            "type": "function",
            "function": {"name": "get_ticker", "parameters": {"type": "object", "properties": {}}},
        }
    ]
    out, meta = await client(tmp_path).complete_json("s", "u", Out, tools=tools, tool_executor=executor)
    assert (
        out.verdict == "APPROVE"
        and seen == [("get_ticker", {"symbol": "BTC/TMN"})]
        and meta["tool_calls"] == 1
    )
    second = json.loads(route.calls[1].request.content)
    assert second["messages"][-1]["role"] == "tool" and second["messages"][-1]["content"] == '{"last": 1}'
    assert meta["usage"]["prompt_tokens"] == 20


def test_helpers() -> None:
    assert extract_json('text {"a": 1} more') == {"a": 1}
    assert input_hash({"x": 0.1234567891}) == input_hash({"x": 0.1234568})
    with pytest.raises(LLMInvalidOutput):
        extract_json("[1, 2]")


async def test_not_configured() -> None:
    with pytest.raises(LLMError, match="not configured"):
        await LLMClient(None, None, calls_log=None).complete_json("s", "u", Out)


@respx.mock
async def test_without_tools_capability(tmp_path: Path) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"verdict": "APPROVE", "size_multiplier": 1.0}'))
    )
    tools = [{"type": "function", "function": {"name": "get_ticker", "parameters": {"type": "object"}}}]

    async def executor(name: str, args: dict[str, Any]) -> str:
        raise AssertionError("tools must not run")

    c = client(tmp_path, capabilities={"tools": False, "json_mode": True})
    out, meta = await c.complete_json("s", "u", Out, tools=tools, tool_executor=executor)
    body = json.loads(route.calls.last.request.content)
    assert "tools" not in body and body["response_format"] == {"type": "json_object"}
    assert out.verdict == "APPROVE" and meta["tool_calls"] == 0


@respx.mock
async def test_without_json_mode_extracts_json_from_text(tmp_path: Path) -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json=completion('Sure! Here it is: {"verdict": "REJECT", "size_multiplier": 0.25} hope it helps'),
        )
    )
    out, _ = await client(tmp_path, capabilities={"tools": True, "json_mode": False}).complete_json(
        "sys", "u", Out
    )
    body = json.loads(route.calls.last.request.content)
    assert "response_format" not in body
    assert (
        body["messages"][0]["content"].startswith("sys")
        and "only one valid JSON" in body["messages"][0]["content"]
    )
    assert out.verdict == "REJECT"


@respx.mock
async def test_probe_capabilities(tmp_path: Path) -> None:
    tool_call = {"id": "t1", "type": "function", "function": {"name": "ping", "arguments": '{"x": 1}'}}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" in body:
            return httpx.Response(200, json=completion(None, [tool_call]))
        return httpx.Response(400, json={"error": {"message": "response_format not supported"}})

    respx.post(f"{BASE}/chat/completions").mock(side_effect=handler)
    caps = await client(tmp_path).probe()
    assert caps["tools"] is True and caps["json_mode"] is False and caps["model"] == "m"
    assert "json_mode_error" in caps


def test_capabilities_file(tmp_path: Path) -> None:
    from siavash.agents.llm import load_capabilities

    p = tmp_path / "caps.json"
    assert load_capabilities(p) is None
    p.write_text('{"tools": false, "json_mode": true}')
    c = client(tmp_path, capabilities=load_capabilities(p))
    assert c.tools_supported is False and c.json_mode is True
    assert client(tmp_path).tools_supported is True and client(tmp_path).json_mode is False
