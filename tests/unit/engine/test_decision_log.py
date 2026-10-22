"""T-605: decision / orders / fills loggers."""

from __future__ import annotations

import asyncio
import csv
import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from siavash.core.models import DecisionRecord, Fill, OrderRequest
from siavash.core.types import DecisionType, OrderType, RejectStage, Side, StrategyId
from siavash.engine.decision_log import (
    FILLS_COLUMNS,
    FillsCsvLogger,
    JsonlDecisionLogger,
    OrdersLogger,
    build_record,
)
from siavash.runtime import init_runtime
from tests.fakes.clock import FakeClock


def _rec(i: int, clock: FakeClock | None = None) -> DecisionRecord:
    return build_record(
        DecisionType.ENTRY_CANDIDATE,
        clock=clock,
        final_reason=f"candidate {i} rejected by ML" + " x" * 200,
        outcome_action="REJECTED",
        symbol="BTC/TMN",
        strategy=StrategyId.S1_TREND_BREAKOUT,
        rejected_at=RejectStage.ML,
        extra={"i": i},
    )


def test_build_record_fills_runtime() -> None:
    init_runtime(mode="paper", config_hash="sha256:abc")
    clock = FakeClock(datetime(2026, 11, 5, 9, 15, tzinfo=UTC))
    r = _rec(1, clock)
    assert r.ts == clock.now()
    assert r.mode.value == "paper" and r.config_hash == "sha256:abc" and r.git_sha
    assert len(r.decision_id) == 26


def test_lines_parse_back(tmp_path: Path) -> None:
    init_runtime(mode="paper", config_hash="sha256:abc")
    log = JsonlDecisionLogger(tmp_path / "logs" / "decisions.jsonl")
    for i in range(5):
        log.write(_rec(i))
    lines = (tmp_path / "logs" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        DecisionRecord.model_validate_json(line)
    assert [r.extra["i"] for r in log.read_all()] == list(range(5))


async def test_concurrent_writes_no_corruption(tmp_path: Path) -> None:
    init_runtime(mode="backtest", config_hash="sha256:abc")
    path = tmp_path / "decisions.jsonl"
    log = JsonlDecisionLogger(path)

    async def task(start: int) -> None:
        for i in range(start, start + 10):
            log.write(_rec(i))
            await asyncio.sleep(0)

    def thread_job(start: int) -> None:
        for i in range(start, start + 10):
            log.write(_rec(i))

    threads = [threading.Thread(target=thread_job, args=(1000 + k * 10,)) for k in range(5)]
    for t in threads:
        t.start()
    await asyncio.gather(*(task(k * 10) for k in range(5)))
    for t in threads:
        t.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    ids = {DecisionRecord.model_validate_json(line).decision_id for line in lines}
    assert len(ids) == 100


def test_orders_logger_masks_and_drops_headers(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("WALLEX_API_KEY", "REAL-KEY-ABCDEFG-123")
    log = OrdersLogger(tmp_path / "orders.jsonl")
    req = OrderRequest(
        symbol="BTC/TMN",
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=Decimal("0.001"),
        price=Decimal("100"),
        client_order_id="SIAV-D-E1",
        decision_id="D",
        purpose="entry",
    )
    log.log("request", req, headers={"X-API-Key": "REAL-KEY-ABCDEFG-123"}, note="key REAL-KEY-ABCDEFG-123")
    raw = (tmp_path / "orders.jsonl").read_text(encoding="utf-8")
    assert "REAL-KEY-ABCDEFG-123" not in raw
    rec = json.loads(raw)
    assert "headers" not in rec
    assert rec["payload"]["client_order_id"] == "SIAV-D-E1"
    assert rec["payload"]["qty"] == "0.001"


def test_fills_csv(tmp_path: Path) -> None:
    log = FillsCsvLogger(tmp_path / "fills.csv")
    f = Fill(
        fill_id="1",
        order_id="9",
        client_order_id="SIAV-D-E1",
        symbol="BTC/TMN",
        side=Side.BUY,
        price=Decimal("100"),
        qty=Decimal("2"),
        fee=Decimal("0.7"),
        fee_asset="TMN",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        is_maker=True,
    )
    log.write(f, Decimal("200"), decision_id="D", position_id="P", strategy="S1_TREND_BREAKOUT")
    log.write(f, Decimal("200"))
    with (tmp_path / "fills.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == FILLS_COLUMNS
    assert len(rows) == 2 and rows[0]["notional_tmn"] == "200" and rows[0]["is_maker"] == "true"
    assert rows[0]["ts"] == "2026-01-01T00:00:00Z"
