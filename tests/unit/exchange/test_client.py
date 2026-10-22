"""T-102: HTTP client (respx)."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
import respx

from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.errors import (
    AuthError,
    BadRequest,
    InsufficientBalance,
    NetworkError,
    OrderNotFound,
    RateLimited,
    ServerError,
)
from siavash.exchange.rate_limit import ErrorCounter, TokenBucket
from siavash.logging import setup_logging

BASE = "https://wallex.test"
KEY = "TEST-WALLEX-KEY-1234567890"


class FakeTime:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


@pytest.fixture
def ft() -> FakeTime:
    return FakeTime()


@pytest.fixture
def client(ft: FakeTime) -> WallexHttp:
    return WallexHttp(
        BASE, KEY, public_rate=(1000, 1000), private_rate=(1000, 1000), sleep_fn=ft.sleep, time_fn=ft.time
    )


@respx.mock(base_url=BASE)
async def test_retry_503_then_success(respx_mock: respx.MockRouter, client: WallexHttp, ft: FakeTime) -> None:
    route = respx_mock.get(ep.Paths.MARKETS).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"result": {"ok": 1}}),
        ]
    )
    body = await client.get(ep.Paths.MARKETS)
    assert body == {"result": {"ok": 1}}
    assert route.call_count == 3
    assert len(ft.slept) == 2
    assert client.errors.count(1e9) == 2


@respx.mock(base_url=BASE)
async def test_no_retry_on_400(respx_mock: respx.MockRouter, client: WallexHttp) -> None:
    route = respx_mock.get(ep.Paths.DEPTH).mock(
        return_value=httpx.Response(400, json={"message": "bad symbol"})
    )
    with pytest.raises(BadRequest):
        await client.get(ep.Paths.DEPTH, params={"symbol": "X"})
    assert route.call_count == 1


@respx.mock(base_url=BASE)
async def test_post_order_never_retried(respx_mock: respx.MockRouter, client: WallexHttp) -> None:
    route = respx_mock.post(ep.Paths.ORDERS).mock(return_value=httpx.Response(503))
    with pytest.raises(ServerError):
        await client.post(ep.Paths.ORDERS, json={"symbol": "BTCTMN"})
    assert route.call_count == 1
    route.mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(NetworkError):
        await client.request("POST", ep.Paths.ORDERS, json={}, private=True, retry=True)
    assert route.call_count == 2  # one more call, no retries


@respx.mock(base_url=BASE)
async def test_gives_up_after_max_retries(respx_mock: respx.MockRouter, client: WallexHttp) -> None:
    route = respx_mock.get(ep.Paths.TRADES).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(NetworkError):
        await client.get(ep.Paths.TRADES)
    assert route.call_count == 4


@respx.mock(base_url=BASE)
async def test_retry_after_respected(respx_mock: respx.MockRouter, client: WallexHttp, ft: FakeTime) -> None:
    respx_mock.get(ep.Paths.MARKETS).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={})]
    )
    await client.get(ep.Paths.MARKETS)
    assert ft.slept == [3.0]


@respx.mock(base_url=BASE)
async def test_error_mapping(respx_mock: respx.MockRouter, client: WallexHttp) -> None:
    respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(401, json={"message": "invalid key"}))
    with pytest.raises(AuthError):
        await client.get(ep.Paths.BALANCES, private=True)
    respx_mock.post(ep.Paths.ORDERS).mock(
        return_value=httpx.Response(400, json={"message": "Insufficient balance"})
    )
    with pytest.raises(InsufficientBalance):
        await client.post(ep.Paths.ORDERS, json={})
    respx_mock.get("/v1/account/orders/1").mock(return_value=httpx.Response(404, json={}))
    with pytest.raises(OrderNotFound):
        await client.get("/v1/account/orders/1", private=True)
    respx_mock.get(ep.Paths.FEE).mock(
        return_value=httpx.Response(200, json={"success": False, "message": "Insufficient balance"})
    )
    with pytest.raises(InsufficientBalance):
        await client.get(ep.Paths.FEE, private=True)


@respx.mock(base_url=BASE)
async def test_auth_header_only_on_private(respx_mock: respx.MockRouter, client: WallexHttp) -> None:
    pub = respx_mock.get(ep.Paths.MARKETS).mock(return_value=httpx.Response(200, json={}))
    priv = respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(200, json={}))
    await client.get(ep.Paths.MARKETS)
    await client.get(ep.Paths.BALANCES, private=True)
    assert ep.AUTH_HEADER not in pub.calls.last.request.headers
    assert priv.calls.last.request.headers[ep.AUTH_HEADER] == KEY


async def test_private_without_key_fails() -> None:
    c = WallexHttp(BASE, None)
    with pytest.raises(AuthError):
        await c.get(ep.Paths.BALANCES, private=True)
    await c.aclose()


async def test_token_bucket_rate(ft: FakeTime) -> None:
    b = TokenBucket(2.0, 2, time_fn=ft.time, sleep_fn=ft.sleep)
    for _ in range(6):
        await b.acquire()
    # 2 burst tokens free, then 4 more at 2/s -> 2 s of waiting
    assert sum(ft.slept) == pytest.approx(2.0)


@respx.mock(base_url=BASE)
async def test_rate_limit_applied_to_requests(respx_mock: respx.MockRouter, ft: FakeTime) -> None:
    c = WallexHttp(BASE, KEY, public_rate=(5, 1), private_rate=(1, 1), sleep_fn=ft.sleep, time_fn=ft.time)
    respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(200, json={}))
    for _ in range(3):
        await c.get(ep.Paths.BALANCES, private=True)
    assert sum(ft.slept) == pytest.approx(2.0)


def test_error_counter() -> None:
    ec = ErrorCounter(time_fn=lambda: 1000.0)
    for t in (100, 800, 950, 990):
        ec.record(t)
    assert ec.count(300) == 3 and ec.count(60) == 2 and ec.last_error_ts() == 990


@respx.mock(base_url=BASE)
async def test_key_never_logged(
    respx_mock: respx.MockRouter, client: WallexHttp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WALLEX_API_KEY", KEY)
    setup_logging("DEBUG", tmp_path, console=False)
    respx_mock.get(ep.Paths.BALANCES).mock(return_value=httpx.Response(500, json={"message": f"echo {KEY}"}))
    with pytest.raises(ServerError):
        await client.get(ep.Paths.BALANCES, private=True)
    for h in logging.getLogger().handlers:
        h.flush()
    text = (tmp_path / "system.log").read_text(encoding="utf-8")
    assert "http_request" in text
    assert KEY not in text


def test_rate_limited_has_retry_after() -> None:
    from siavash.exchange.errors import error_from_response

    e = error_from_response(429, {}, retry_after=2.5)
    assert isinstance(e, RateLimited) and e.retry_after == 2.5
