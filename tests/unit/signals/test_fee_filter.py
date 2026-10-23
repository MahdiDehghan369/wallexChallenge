"""T-306: fee filter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from siavash.config import AppConfig, FeesConfig, load_config
from siavash.core.models import Candidate
from siavash.core.types import StrategyId
from siavash.signals.fee_filter import (
    FEE_TOO_HIGH,
    min_expected_move_pct,
    passes_fee_filter,
    round_trip_fee_pct,
)

CFG_DIR = Path(__file__).resolve().parents[3] / "config"


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    # pinned pessimistic fees: the repo's risk.yaml fees are the operator's account rate and may change
    return _with_fees(load_config(CFG_DIR, env_file=None), 0.35, 0.35)


def _with_fees(cfg: AppConfig, maker: float | None, taker: float) -> AppConfig:
    risk = cfg.risk.model_copy(update={"fees": FeesConfig(maker_pct=maker, taker_pct=taker)})
    return cfg.model_copy(update={"risk": risk})


def _cand(move: float) -> Candidate:
    return Candidate(
        candidate_id="c",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        symbol="BTC/TMN",
        strategy=StrategyId.S3_IMPLIED_DEV,
        entry_ref_price=100,
        stop_loss=98,
        take_profit=100 + move,
        time_stop_minutes=60,
        expected_move_pct=move,
        conditions={},
    )


def test_round_trip(cfg: AppConfig) -> None:
    assert round_trip_fee_pct(cfg) == pytest.approx(0.70)
    assert round_trip_fee_pct(_with_fees(cfg, 0.1, 0.2)) == pytest.approx(0.30)
    assert round_trip_fee_pct(_with_fees(cfg, None, 0.25)) == pytest.approx(0.50)


def test_filter(cfg: AppConfig) -> None:
    c2 = _with_fees(cfg, 0.1, 0.2)  # rt 0.3 -> min 0.75
    assert min_expected_move_pct(c2) == pytest.approx(0.75)
    assert passes_fee_filter(_cand(0.8), c2) == (True, "")
    assert passes_fee_filter(_cand(0.7), c2) == (False, FEE_TOO_HIGH)
    # with the pessimistic default fees (0.35/0.35) a 1.5 % target fails (needs 1.75 %)
    assert passes_fee_filter(_cand(1.5), cfg) == (False, FEE_TOO_HIGH)
    assert passes_fee_filter(_cand(2.0), cfg) == (True, "")
