"""T-401: triple-barrier labels on synthetic price paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from siavash.core.models import Candidate
from siavash.core.types import StrategyId
from siavash.ml.labeling import label_candidate

T0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
FEE = 0.4  # round trip, percent


def cand(tp: float = 103.0, sl: float = 98.0, tp1: float | None = None, minutes: int = 60) -> Candidate:
    return Candidate(
        candidate_id="C1",
        ts=T0,
        symbol="BTC/TMN",
        strategy=StrategyId.S1_TREND_BREAKOUT,
        entry_ref_price=100.0,
        stop_loss=sl,
        take_profit=tp,
        tp1_price=tp1,
        time_stop_minutes=minutes,
        expected_move_pct=3.0,
        conditions={},
    )


def bars(*ohlc: tuple[float, float, float, float], start: datetime = T0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ts_open": start + timedelta(minutes=15 * i), "open": o, "high": h, "low": lo, "close": c}
            for i, (o, h, lo, c) in enumerate(ohlc)
        ]
    )


def test_take_profit() -> None:
    label, ret, reason, held = label_candidate(
        cand(), bars((100, 101, 99.5, 100.5), (100.5, 103.5, 100, 103)), FEE, 0.0
    )
    assert (label, reason, held) == (1, "TP", 2)
    assert ret == pytest.approx(3.0 - FEE)


def test_stop_loss_with_slippage() -> None:
    label, ret, reason, held = label_candidate(cand(), bars((100, 100.5, 97.5, 98)), FEE, 0.1)
    entry, exit_ = 100 * 1.001, 98 * 0.999
    assert (label, reason, held) == (0, "SL", 1)
    assert ret == pytest.approx((exit_ / entry - 1) * 100 - FEE)


def test_both_in_one_candle_is_sl() -> None:
    label, _, reason, _ = label_candidate(cand(), bars((100, 104, 97, 103)), FEE, 0.0)
    assert (label, reason) == (0, "SL")


def test_gap_below_stop_exits_at_open() -> None:
    _, ret, reason, held = label_candidate(
        cand(), bars((100, 100.2, 99.5, 99.8), (95, 96, 94, 95.5)), 0.0, 0.0
    )
    assert reason == "SL" and held == 2 and ret == pytest.approx(-5.0)


def test_tp1_used_when_set() -> None:
    label, ret, reason, _ = label_candidate(cand(tp=110, tp1=102), bars((100, 102.5, 99.5, 102)), FEE, 0.0)
    assert (label, reason) == (1, "TP") and ret == pytest.approx(2.0 - FEE)


def test_time_stop_positive_and_negative() -> None:
    path = [(100, 101, 99.5, 100.5)] * 3 + [(100.5, 101.5, 100, 101.5)] + [(101.5, 110, 101, 109)]
    label, ret, reason, held = label_candidate(cand(minutes=60), bars(*path), FEE, 0.0)
    assert (label, reason, held) == (1, "TIME", 4)  # the 5th candle (TP) is after the time stop
    assert ret == pytest.approx(1.5 - FEE)
    flat = [(100, 100.5, 99.5, 100.1)] * 4
    label2, ret2, reason2, _ = label_candidate(cand(minutes=60), bars(*flat), FEE, 0.0)
    assert (label2, reason2) == (0, "TIME") and ret2 < 0


def test_ignores_bars_before_signal_and_empty() -> None:
    before = bars((50, 200, 1, 100), start=T0 - timedelta(minutes=15))  # would hit TP and SL if used
    after = bars((100, 103.5, 99, 103))
    label, _, reason, _ = label_candidate(cand(), pd.concat([before, after]), FEE, 0.0)
    assert (label, reason) == (1, "TP")
    with pytest.raises(ValueError):
        label_candidate(cand(), before, FEE, 0.0)
