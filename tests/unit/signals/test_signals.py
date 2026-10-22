"""T-305: signal generators."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from siavash.config import AppConfig, StrategiesToggle, load_config
from siavash.core.models import RegimeAssessment
from siavash.core.types import Regime, StrategyId
from siavash.signals.implied_dev import ImpliedDeviation
from siavash.signals.meanrev import MeanReversion
from siavash.signals.registry import build_generators
from siavash.signals.trend import TrendBreakout

CFG_DIR = Path(__file__).resolve().parents[3] / "config"
TS = datetime(2026, 1, 10, 12, 15, tzinfo=UTC)
ALL = list(StrategyId)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(CFG_DIR, env_file=None)


def regime(enabled: list[StrategyId] = ALL, label: Regime = Regime.RANGE) -> RegimeAssessment:
    return RegimeAssessment(
        ts=TS,
        regime=label,
        risk_multiplier=0.7,
        enabled_strategies=enabled,
        reasons=[],
        source="rule",
        rule_regime=label,
    )


def s1_row(**kw: float) -> dict[str, float]:
    row = {
        "15m_close": 100.0,
        "15m_atr14": 1.0,
        "15m_donchian_high_dist": 0.4,
        "1h_ema20": 99.0,
        "1h_ema50": 97.0,
        "1h_adx14": 25.0,
        "15m_vol_z20": 1.8,
    }
    row.update(kw)
    return row


def s2_row(**kw: float) -> dict[str, float]:
    row = {
        "15m_close": 100.0,
        "15m_atr14": 1.0,
        "15m_bb_pos": -0.1,
        "15m_bb_mid": 103.0,
        "15m_rsi14": 24.0,
        "1h_adx14": 14.0,
        "4h_ema200_dist": 1.0,
    }
    row.update(kw)
    return row


def s3_row(**kw: float) -> dict[str, float]:
    row = {
        "15m_close": 100.0,
        "x_implied_z": -2.6,
        "x_implied_dev_pct": -2.0,
        "x_implied_dev_mean_pct": 0.0,
        "x_btc_ret_1h": -0.3,
        "ob_spread_pct": 0.1,
    }
    row.update(kw)
    return row


def _check_prices(c) -> None:  # type: ignore[no-untyped-def]
    assert c.stop_loss < c.entry_ref_price < c.take_profit


def test_s1_fires(cfg: AppConfig) -> None:
    (c,) = TrendBreakout(cfg).generate("BTC/TMN", TS, s1_row(), regime())
    _check_prices(c)
    assert c.strategy is StrategyId.S1_TREND_BREAKOUT
    assert c.stop_loss == pytest.approx(98.5) and c.tp1_price == pytest.approx(102.0)
    assert c.take_profit == pytest.approx(104.0) and c.time_stop_minutes == 720
    assert c.expected_move_pct == pytest.approx(2.0)
    assert c.conditions["adx_1h"] == 25.0 and c.ts == TS


@pytest.mark.parametrize(
    "bad",
    [{"15m_donchian_high_dist": -0.1}, {"1h_ema20": 96.0}, {"1h_adx14": 18.0}, {"15m_vol_z20": 0.5}],
)
def test_s1_no_signal(cfg: AppConfig, bad: dict[str, float]) -> None:
    assert TrendBreakout(cfg).generate("BTC/TMN", TS, s1_row(**bad), regime()) == []


def test_s2_fires(cfg: AppConfig) -> None:
    (c,) = MeanReversion(cfg).generate("ETH/TMN", TS, s2_row(), regime())
    _check_prices(c)
    assert c.take_profit == 103.0 and c.stop_loss == pytest.approx(98.8)
    assert c.expected_move_pct == pytest.approx(3.0) and c.time_stop_minutes == 360


@pytest.mark.parametrize(
    "bad",
    [
        {"15m_bb_pos": 0.1},
        {"15m_rsi14": 35.0},
        {"1h_adx14": 25.0},
        {"4h_ema200_dist": -3.0},
        {"15m_bb_mid": 99.0},
    ],
)
def test_s2_no_signal(cfg: AppConfig, bad: dict[str, float]) -> None:
    assert MeanReversion(cfg).generate("ETH/TMN", TS, s2_row(**bad), regime()) == []


def test_s3_fires_and_caps_tp(cfg: AppConfig) -> None:
    (c,) = ImpliedDeviation(cfg).generate("BTC/TMN", TS, s3_row(), regime())
    _check_prices(c)
    # z=0 price = 100/(0.98) ≈ 102.04 > cap 101.5 -> capped
    assert c.take_profit == pytest.approx(101.5)
    assert c.stop_loss == pytest.approx(98.8) and c.time_stop_minutes == 180
    assert c.conditions["tp_capped"] is True
    (c2,) = ImpliedDeviation(cfg).generate(
        "BTC/TMN", TS, s3_row(**{"x_implied_dev_pct": -1.5, "x_implied_dev_mean_pct": -0.5}), regime()
    )
    assert c2.take_profit == pytest.approx(100 * 0.995 / 0.985)


@pytest.mark.parametrize(
    "bad",
    [{"x_implied_z": -1.5}, {"ob_spread_pct": 0.5}, {"x_btc_ret_1h": -2.0}, {"x_implied_dev_mean_pct": -3.0}],
)
def test_s3_no_signal(cfg: AppConfig, bad: dict[str, float]) -> None:
    assert ImpliedDeviation(cfg).generate("BTC/TMN", TS, s3_row(**bad), regime()) == []


def test_s3_without_orderbook_feature(cfg: AppConfig) -> None:
    row = s3_row()
    del row["ob_spread_pct"]
    assert len(ImpliedDeviation(cfg).generate("BTC/TMN", TS, row, regime())) == 1
    assert ImpliedDeviation(cfg).generate("USDT/TMN", TS, row, regime()) == []


def test_regime_enabled_strategies(cfg: AppConfig) -> None:
    only_s3 = regime([StrategyId.S3_IMPLIED_DEV])
    assert TrendBreakout(cfg).generate("BTC/TMN", TS, s1_row(), only_s3) == []
    assert MeanReversion(cfg).generate("BTC/TMN", TS, s2_row(), only_s3) == []
    assert len(ImpliedDeviation(cfg).generate("BTC/TMN", TS, s3_row(), only_s3)) == 1
    assert ImpliedDeviation(cfg).generate("BTC/TMN", TS, s3_row(), regime([])) == []


def test_config_strategies_enabled(cfg: AppConfig) -> None:
    strat = cfg.strategy.model_copy(update={"strategies": StrategiesToggle(enabled=["S2_MEAN_REVERSION"])})
    c2 = cfg.model_copy(update={"strategy": strat})
    assert TrendBreakout(c2).generate("BTC/TMN", TS, s1_row(), regime()) == []
    assert len(MeanReversion(c2).generate("BTC/TMN", TS, s2_row(), regime())) == 1


def test_missing_features_no_signal(cfg: AppConfig) -> None:
    for gen in build_generators(cfg):
        assert gen.generate("BTC/TMN", TS, {"15m_close": float("nan")}, regime()) == []
