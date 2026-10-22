"""T-1103: alerts (respx)."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import respx

from siavash.ops.alerts import Alerts

URL = "https://hooks.test/abc"


@respx.mock
async def test_webhook_rate_limited_and_masked() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    a = Alerts("webhook", URL, min_interval_s=0.05)
    a.start()
    t0 = time.perf_counter()
    for i in range(3):
        a.send("entry", {"symbol": "BTC/TMN", "n": i, "api_key": "secret"})
    await a.stop()
    assert route.call_count == 3 and a.sent == 3
    assert time.perf_counter() - t0 >= 0.1  # at most one message per interval
    body = json.loads(route.calls[0].request.content)
    assert body["event"]["kind"] == "entry" and body["event"]["api_key"] == "***"
    assert body["text"].startswith("[siavash] entry:")


@respx.mock
async def test_failures_never_raise() -> None:
    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    a = Alerts("webhook", URL, min_interval_s=0)
    a.start()
    a("breaker", {"new": "FLATTEN_AND_HALT"})  # callable hook form
    await a.stop()
    assert a.failed == 1 and a.sent == 0


async def test_log_only_and_queue_full() -> None:
    a = Alerts("log_only")
    a.send("startup", {})
    assert a.queue.qsize() == 0
    b = Alerts("webhook", URL, max_queue=1)
    b.send("x")
    b.send("y")  # not started -> second is dropped
    assert b.dropped == 1
    c = Alerts("webhook", None)
    assert c.backend == "log_only"
    await asyncio.sleep(0)
