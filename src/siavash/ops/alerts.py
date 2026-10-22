"""Operator alerts (T-1103).

Backends: ``log_only`` (default) or ``webhook`` (POST JSON to ``ALERT_WEBHOOK_URL`` — Bale / Telegram
bot relay / Slack, whichever is reachable from the server). Sending is non-blocking: events go into a
bounded queue drained by a background worker at most one message per ``alerts.min_interval_s``. A
failing webhook is logged and never stops the engine. Events: startup, entry, exit (with PnL),
breaker, reconcile critical (``ORDER_NOT_FROM_BOT``), repeated errors, end-game, hourly equity.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import httpx

from siavash.logging import get_logger, mask_value

log = get_logger(__name__)


class Alerts:
    def __init__(
        self,
        backend: str = "log_only",
        webhook_url: str | None = None,
        min_interval_s: float = 3.0,
        max_queue: int = 500,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        if backend == "webhook" and not webhook_url:
            log.warning("alerts_webhook_missing_url_fallback_log_only")
            backend = "log_only"
        self.backend = backend
        self.url = webhook_url
        self.min_interval_s = min_interval_s
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._transport = transport
        self._timeout = timeout_s
        self._worker: asyncio.Task[None] | None = None
        self.sent = 0
        self.failed = 0
        self.dropped = 0

    @classmethod
    def from_config(cls, cfg: Any) -> Alerts:
        url = cfg.secrets.alert_webhook_url.get_secret_value() if cfg.secrets.alert_webhook_url else None
        return cls(cfg.base.alerts.backend, url, cfg.base.alerts.min_interval_s)

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        """Same signature as the ``alert`` hooks of breakers / reconcile; never blocks or raises."""
        self.send(kind, data)

    def send(self, kind: str, data: dict[str, Any] | None = None) -> None:
        msg = {"ts": datetime.now(UTC).isoformat(), "kind": kind, **mask_value(dict(data or {}))}
        log.info("alert", **{k: v for k, v in msg.items() if k != "ts"})
        if self.backend != "webhook":
            return
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            self.dropped += 1

    async def _post(self, client: httpx.AsyncClient, msg: dict[str, Any]) -> None:
        assert self.url is not None
        try:
            r = await client.post(self.url, json={"text": self.format(msg), "event": msg})
            r.raise_for_status()
            self.sent += 1
        except Exception as e:
            self.failed += 1
            log.warning("alert_send_failed", error=f"{type(e).__name__}: {e}"[:200])

    @staticmethod
    def format(msg: dict[str, Any]) -> str:
        fields = ", ".join(f"{k}={v}" for k, v in msg.items() if k not in ("ts", "kind"))
        return f"[siavash] {msg['kind']}: {fields}"

    async def run(self) -> None:
        """Worker loop (rate-limited)."""
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        async with httpx.AsyncClient(**kwargs) as client:
            while True:
                msg = await self.queue.get()
                await self._post(client, msg)
                self.queue.task_done()
                await asyncio.sleep(self.min_interval_s)

    def start(self) -> None:
        if self.backend == "webhook" and self._worker is None:
            self._worker = asyncio.ensure_future(self.run())

    async def stop(self, drain_timeout_s: float = 5.0) -> None:
        if self._worker is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.queue.join(), timeout=drain_timeout_s)
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None
