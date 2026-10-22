"""T-1003: consistency comparison on hand-made logs that partly agree."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from siavash.config import load_config
from siavash.core.types import Timeframe
from siavash.reporting.consistency import (
    assumed_slippage_pct,
    compare_candidates,
    consistency_markdown,
    entry_slippage_pct,
    explain,
    load_candidates,
    load_live_frames,
    merge_frames,
)

T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def rec(i: int, symbol: str, outcome: str, stage: str | None = None, pid: str | None = None) -> dict:
    ts = (T0 + timedelta(minutes=15 * i)).isoformat()
    return {
        "decision_id": f"D{i}{symbol}",
        "ts": ts,
        "type": "ENTRY_CANDIDATE",
        "symbol": symbol,
        "strategy": "S1_TREND_BREAKOUT",
        "signal": {"ts": ts, "symbol": symbol, "strategy": "S1_TREND_BREAKOUT", "entry_ref_price": 100.0},
        "outcome_action": outcome,
        "rejected_at": stage,
        "position_id": pid,
    }


def write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def logs(tmp_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    live = [
        rec(0, "BTC/TMN", "ORDER_PLACED", pid="P0"),
        rec(1, "BTC/TMN", "REJECTED", "LLM"),
        rec(2, "ETH/TMN", "REJECTED", "RISK"),
        rec(3, "ETH/TMN", "REJECTED", "FEE_FILTER"),
        {"type": "EXIT", "decision_id": "X"},  # ignored
    ]
    bt = [
        rec(0, "BTC/TMN", "ORDER_PLACED", pid="Q0"),
        rec(1, "BTC/TMN", "ORDER_PLACED", pid="Q1"),  # LLM approved in replay (cache miss -> fallback)
        rec(2, "ETH/TMN", "REJECTED", "RISK"),
        rec(5, "SOL/TMN", "REJECTED", "SPREAD"),  # only in the backtest
    ]
    return load_candidates(write(tmp_path / "live" / "decisions.jsonl", live)), load_candidates(
        write(tmp_path / "bt" / "decisions.jsonl", bt)
    )


def test_compare(logs: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    ref, bt = logs
    assert len(ref) == 4 and len(bt) == 4
    c = compare_candidates(ref, bt)
    assert c["n_common"] == 3 and c["pct_common_of_ref"] == 75.0 and c["pct_common_of_union"] == 60.0
    assert c["pct_same_decision"] == pytest.approx(66.67)
    assert c["decision_diffs"] == {"REJECTED@LLM → PLACED": 1}
    assert c["only_ref_by_symbol"] == {"ETH/TMN": 1} and c["only_bt_by_symbol"] == {"SOL/TMN": 1}


def test_slippage_and_explanations(logs: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    ref, bt = logs
    fills = pd.DataFrame(
        [
            {"position_id": "P0", "side": "buy", "price": 100.2, "qty": 1.0},
            {"position_id": "P0", "side": "buy", "price": 100.4, "qty": 1.0},
            {"position_id": "P0", "side": "sell", "price": 103.0, "qty": 2.0},
        ]
    )
    slip = entry_slippage_pct(ref, fills)
    assert slip == pytest.approx(0.3)
    assert entry_slippage_pct(ref, fills.iloc[0:0]) is None
    cfg = load_config("config")
    assumed = assumed_slippage_pct(cfg, ["BTC/TMN"])
    assert assumed == pytest.approx(cfg.base.bt.spread_for("BTC/TMN") / 2 + cfg.base.bt.extra_slippage_pct)
    cmp = compare_candidates(ref, bt)
    lines = explain(cmp, slip, assumed, 1.0, 0.4)
    text = " ".join(lines)
    assert "75.0 %" in text and "ETH/TMN (1)" in text  # < 80 % reproduced -> data explanation
    assert "LLM verdicts differ" in text
    assert "worse than" in text and "+0.600 pp" in text
    md = consistency_markdown(
        cmp, lines, slip, None, assumed, {"net_return_pct": 1.0}, {"net_return_pct": 0.4}, (T0, T0)
    )
    assert "identical candidates (% of live) | 75.0" in md and "## Main differences" in md


def test_live_frames_and_merge(tmp_path: Path) -> None:
    day = tmp_path / "2026-11-01"
    day.mkdir()
    candles = [
        {
            "symbol": "BTC/TMN",
            "timeframe": "15m",
            "ts_open": (T0 + timedelta(minutes=15 * i)).isoformat(),
            "open": 1,
            "high": 2,
            "low": 0.5,
            "close": 1.5 + i,
            "volume": 3,
            "is_filled": False,
        }
        for i in range(3)
    ]
    write(day / "candles_15m.jsonl", candles + candles[-1:])  # duplicate line
    live = load_live_frames(tmp_path)
    df = live["BTC/TMN"][Timeframe.M15]
    assert len(df) == 3 and df["ts_open"].is_monotonic_increasing
    hist = pd.DataFrame(
        {
            "ts_open": pd.date_range(T0 - timedelta(hours=1), periods=6, freq="15min", tz="UTC"),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 9.0,
            "volume": 1.0,
            "is_filled": False,
        }
    )
    merged = merge_frames({"BTC/TMN": {Timeframe.M15: hist}}, live)["BTC/TMN"][Timeframe.M15]
    assert len(merged) == 7  # 4 history-only + 3 live (live wins on the 2 overlapping candles)
    assert merged[merged["ts_open"] == pd.Timestamp(T0)]["close"].iloc[0] == 1.5
