"""Decision, order and fill loggers (T-605).

- ``logs/decisions.jsonl``: one ``DecisionRecord`` per line (appendix C), append-only, fsync'd.
- ``logs/orders.jsonl``: every order request/response (no headers, secrets masked).
- ``logs/fills.csv``: executed fills (source for the judge report).
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from siavash.core.ids import new_ulid
from siavash.core.interfaces import Clock
from siavash.core.models import DecisionRecord, Fill
from siavash.core.types import DecisionType, Mode
from siavash.logging import mask_value
from siavash.runtime import get_runtime

FILLS_COLUMNS = [
    "ts",
    "symbol",
    "side",
    "price",
    "qty",
    "notional_tmn",
    "fee",
    "fee_asset",
    "is_maker",
    "client_order_id",
    "decision_id",
    "position_id",
    "strategy",
]
_HEADER_KEYS = {"headers", "request_headers", "response_headers"}


def _append_line(path: Path, line: str, lock: threading.Lock) -> None:
    with lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class JsonlDecisionLogger:
    """Append-only JSONL writer implementing the ``DecisionLogger`` protocol (thread/task safe)."""

    def __init__(self, path: str | os.PathLike[str] = "logs/decisions.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.count = 0

    def write(self, record: DecisionRecord) -> None:
        """Serialise ``record`` as one JSON line, flush and fsync."""
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        _append_line(self.path, line, self._lock)
        self.count += 1

    def read_all(self) -> list[DecisionRecord]:
        """Parse every record in the file (for reports/tests)."""
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as fh:
            return [DecisionRecord.model_validate_json(line) for line in fh if line.strip()]


class MemoryDecisionLogger:
    """In-memory ``DecisionLogger`` (tests, dry runs)."""

    def __init__(self) -> None:
        self.records: list[DecisionRecord] = []

    def write(self, record: DecisionRecord) -> None:
        self.records.append(record)


def build_record(
    type: DecisionType,
    *,
    final_reason: str,
    clock: Clock | None = None,
    outcome_action: str = "INFO",
    decision_id: str | None = None,
    **fields: Any,
) -> DecisionRecord:
    """Build a ``DecisionRecord`` filling ``decision_id``, ``ts`` and runtime fields."""
    rt = get_runtime()
    return DecisionRecord(
        decision_id=decision_id or new_ulid(),
        ts=clock.now() if clock else datetime.now(UTC),
        mode=Mode(rt.mode),
        git_sha=rt.git_sha,
        version_tag=rt.version_tag,
        config_hash=rt.config_hash,
        type=type,
        outcome_action=outcome_action,  # type: ignore[arg-type]
        final_reason=final_reason,
        **fields,
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items() if str(k).lower() not in _HEADER_KEYS}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "value") and not isinstance(value, int | float | str | bool):
        return value.value
    return value


class OrdersLogger:
    """``logs/orders.jsonl``: one line per order request/response/error."""

    def __init__(
        self, path: str | os.PathLike[str] = "logs/orders.jsonl", clock: Clock | None = None
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.Lock()

    def log(self, kind: str, payload: Any, **meta: Any) -> None:
        """``kind`` in {request, response, error, cancel, ...}; headers are dropped, secrets masked."""
        rec = {
            "ts": (self.clock.now() if self.clock else datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
            "kind": kind,
            "run_id": get_runtime().run_id,
            **_jsonable(meta),
            "payload": _jsonable(payload),
        }
        line = json.dumps(mask_value(rec), ensure_ascii=False, separators=(",", ":"), default=str)
        _append_line(self.path, line, self._lock)


class FillsCsvLogger:
    """``logs/fills.csv`` with the columns in :data:`FILLS_COLUMNS`."""

    def __init__(self, path: str | os.PathLike[str] = "logs/fills.csv") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(
        self,
        fill: Fill,
        notional_tmn: Decimal,
        decision_id: str | None = None,
        position_id: str | None = None,
        strategy: str | None = None,
    ) -> None:
        """Append one fill row (writes the header for a new file)."""
        row = {
            "ts": fill.ts.isoformat().replace("+00:00", "Z"),
            "symbol": fill.symbol,
            "side": fill.side.value,
            "price": str(fill.price),
            "qty": str(fill.qty),
            "notional_tmn": str(notional_tmn),
            "fee": str(fill.fee),
            "fee_asset": fill.fee_asset,
            "is_maker": "" if fill.is_maker is None else str(fill.is_maker).lower(),
            "client_order_id": fill.client_order_id,
            "decision_id": decision_id or "",
            "position_id": position_id or "",
            "strategy": strategy or "",
        }
        with self._lock:
            new = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FILLS_COLUMNS)
                if new:
                    w.writeheader()
                w.writerow(row)
                fh.flush()
                os.fsync(fh.fileno())
