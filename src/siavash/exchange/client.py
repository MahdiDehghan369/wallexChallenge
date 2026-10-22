"""Low-level async HTTP client for Wallex (T-102).

- auth header from ``endpoints.AUTH_HEADER`` (private calls only),
- separate token buckets for public and private calls,
- retries (tenacity) only on network errors, timeouts, 5xx and 429 (honours ``Retry-After``);
  **POST is never retried automatically** (order idempotency is handled in T-802),
- every request is logged as ``event="http_request"`` with path/status/ms (never headers),
  errors are recorded in an ``ErrorCounter`` for the breaker.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import wait_base

from siavash.exchange import endpoints as ep
from siavash.exchange.errors import (
    AuthError,
    NetworkError,
    RateLimited,
    ServerError,
    WallexError,
    error_from_response,
)
from siavash.exchange.rate_limit import ErrorCounter, TokenBucket
from siavash.logging import get_logger

log = get_logger(__name__)

RETRYABLE = (NetworkError, ServerError, RateLimited)


class _WaitRetryAfter(wait_base):
    """Use ``Retry-After`` from a ``RateLimited`` error when present, else exponential backoff with jitter."""

    def __init__(self, fallback: wait_base, max_wait: float) -> None:
        self.fallback = fallback
        self.max_wait = max_wait

    def __call__(self, retry_state: Any) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RateLimited) and exc.retry_after is not None:
            return float(min(max(exc.retry_after, 0.0), self.max_wait * 4))
        return float(self.fallback(retry_state))


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("Retry-After")
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


class WallexHttp:
    """Thin async HTTP layer; higher layers (market data, account) build on ``request``."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        public_rate: tuple[float, int] = (5.0, 10),
        private_rate: tuple[float, int] = (2.0, 4),
        timeout: float = 10.0,
        max_retries: int = 4,
        backoff_min_s: float = 0.5,
        backoff_max_s: float = 8.0,
        proxy: str | None = None,
        error_counter: ErrorCounter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        time_fn: Callable[[], float] = time.monotonic,
        on_latency: Callable[[str, str, int | None, float], None] | None = None,
    ) -> None:
        self.base_url = (base_url or ep.BASE_URL).rstrip("/")
        self._api_key = api_key
        self.max_retries = max_retries
        self._sleep = sleep_fn
        self._wait = _WaitRetryAfter(
            wait_exponential_jitter(multiplier=backoff_min_s, max=backoff_max_s), backoff_max_s
        )
        self.public_bucket = TokenBucket(*public_rate, time_fn=time_fn, sleep_fn=sleep_fn)
        self.private_bucket = TokenBucket(*private_rate, time_fn=time_fn, sleep_fn=sleep_fn)
        self.errors = error_counter or ErrorCounter()
        self.on_latency = on_latency
        kwargs: dict[str, Any] = {"base_url": self.base_url, "timeout": timeout}
        if transport is not None:
            kwargs["transport"] = transport
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.AsyncClient(**kwargs)

    @classmethod
    def from_config(cls, cfg: Any, error_counter: ErrorCounter | None = None, **kw: Any) -> WallexHttp:
        """Build from ``AppConfig`` (proxy only if ``exchange.use_proxy``)."""
        ex = cfg.base.exchange
        rl = ex.rate_limit
        key = cfg.secrets.wallex_api_key.get_secret_value() if cfg.secrets.wallex_api_key else None
        return cls(
            base_url=cfg.wallex_base_url,
            api_key=key,
            public_rate=(rl.public_per_s, rl.public_burst),
            private_rate=(rl.private_per_s, rl.private_burst),
            timeout=ex.timeout_s,
            max_retries=ex.max_retries,
            backoff_min_s=ex.backoff_min_s,
            backoff_max_s=ex.backoff_max_s,
            proxy=cfg.https_proxy if ex.use_proxy else None,
            error_counter=error_counter,
            **kw,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> WallexHttp:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _once(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        private: bool,
    ) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        if private:
            if not self._api_key:
                raise AuthError("WALLEX_API_KEY is not configured")
            headers[ep.AUTH_HEADER] = self._api_key
        await (self.private_bucket if private else self.public_bucket).acquire()
        t0 = time.perf_counter()
        status: int | None = None
        try:
            resp = await self._client.request(method, path, params=params, json=json, headers=headers)
            status = resp.status_code
        except (httpx.TimeoutException, httpx.TransportError) as e:
            self.errors.record()
            raise NetworkError(f"{type(e).__name__}: {e}") from e
        finally:
            ms = (time.perf_counter() - t0) * 1000.0
            log.info("http_request", method=method, path=path, status=status, ms=round(ms, 1))
            if self.on_latency:
                self.on_latency(method, path, status, ms)
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text
        if status >= 400:
            self.errors.record()
            raise error_from_response(status, body, retry_after=_retry_after(resp))
        if isinstance(body, dict) and body.get("success") is False:
            self.errors.record()
            raise error_from_response(400, body)
        return body

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        private: bool = False,
        retry: bool | None = None,
    ) -> Any:
        """Send a request and return the decoded JSON body; raises ``WallexError`` subclasses."""
        method = method.upper()
        do_retry = (method != "POST") if retry is None else (retry and method != "POST")
        if not do_retry:
            return await self._once(method, path, params, json, private)
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_retries),
            wait=self._wait,
            retry=retry_if_exception_type(RETRYABLE),
            sleep=self._sleep,
            reraise=True,
        ):
            with attempt:
                return await self._once(method, path, params, json, private)
        raise WallexError("unreachable")  # pragma: no cover

    async def get(self, path: str, params: dict[str, Any] | None = None, private: bool = False) -> Any:
        return await self.request("GET", path, params=params, private=private)

    async def post(self, path: str, json: dict[str, Any] | None = None, private: bool = True) -> Any:
        return await self.request("POST", path, json=json, private=private)

    async def delete(self, path: str, json: dict[str, Any] | None = None, private: bool = True) -> Any:
        return await self.request("DELETE", path, json=json, private=private)
