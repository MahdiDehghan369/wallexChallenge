"""Speed metrics (T-805) → ``logs/latency.jsonl``.

Kinds: ``candle_close_to_decision``, ``decision_to_order_sent``, ``order_sent_to_ack``,
``order_sent_to_first_fill``, ``exit_trigger_to_exit_fill`` (milliseconds).
``summarize`` gives n / p50 / p95 / max per kind for the reports.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

KINDS = (
    "candle_close_to_decision",
    "decision_to_order_sent",
    "order_sent_to_ack",
    "order_sent_to_first_fill",
    "exit_trigger_to_exit_fill",
)


class LatencyRecorder:
    def __init__(self, path: str | Path | None = "logs/latency.jsonl") -> None:
        self.path = Path(path) if path else None
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, ms: float, ts: datetime | None = None, **meta: Any) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown latency kind {kind}")
        rec = {
            "ts": (ts or datetime.now(UTC)).isoformat(),
            "kind": kind,
            "ms": round(max(ms, 0.0), 3),
            **meta,
        }
        with self._lock:
            self.records.append(rec)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")


def load(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """``{kind: {"n", "p50", "p95", "max"}}`` in milliseconds."""
    by: dict[str, list[float]] = {}
    for r in records:
        by.setdefault(r["kind"], []).append(float(r["ms"]))
    out = {}
    for kind, vals in by.items():
        a = np.asarray(vals)
        out[kind] = {
            "n": len(vals),
            "p50": float(np.percentile(a, 50)),
            "p95": float(np.percentile(a, 95)),
            "max": float(a.max()),
        }
    return out
