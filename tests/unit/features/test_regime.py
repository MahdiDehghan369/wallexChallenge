"""T-304: market features + rule regime provider."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from siavash.config import AppConfig, load_config
from siavash.core.types import Regime, StrategyId, Timeframe
from siavash.features.builder import build_features
from siavash.features.regime import RuleRegimeProvider, market_features
from tests.fakes.synthetic import make_synthetic_market, resample_ohlcv

CFG_DIR = Path(__file__).resolve().parents[3] / "config"
TS = datetime(2026, 1, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def cfg() -> AppConfig:
    return load_config(CFG_DIR, env_file=None)


def _row(**kw: float) -> dict[str, float]:
    base = {
        "x_btc_ret_1h": 0.1,
        "x_btc_ret_4h": 0.2,
        "x_rv_percentile_7d": 50.0,
        "x_btc_adx_1h": 15.0,
        "x_btc_ema_trend_1h": -0.1,
        "x_breadth_ema50": 40.0,
    }
    base.update(kw)
    return base


async def test_panic(cfg: AppConfig) -> None:
    p = RuleRegimeProvider(cfg)
    a = await p.assess(_row(x_btc_ret_1h=-3.5, x_rv_percentile_7d=99), TS)
    assert a.regime is Regime.PANIC and a.risk_multiplier == 0.0 and a.enabled_strategies == []
    assert (await p.assess(_row(x_btc_ret_4h=-6), TS)).regime is Regime.PANIC
    assert a.source == "rule" and a.rule_regime is Regime.PANIC


async def test_high_vol(cfg: AppConfig) -> None:
    a = await RuleRegimeProvider(cfg).assess(_row(x_rv_percentile_7d=95, x_btc_adx_1h=30), TS)
    assert a.regime is Regime.HIGH_VOL and a.risk_multiplier == 0.5
    assert a.enabled_strategies == [StrategyId.S3_IMPLIED_DEV]


async def test_trend_up(cfg: AppConfig) -> None:
    a = await RuleRegimeProvider(cfg).assess(
        _row(x_btc_adx_1h=25, x_btc_ema_trend_1h=0.3, x_breadth_ema50=60), TS
    )
    assert a.regime is Regime.TREND_UP and a.risk_multiplier == 1.0
    assert a.enabled_strategies == [StrategyId.S1_TREND_BREAKOUT, StrategyId.S3_IMPLIED_DEV]


async def test_range(cfg: AppConfig) -> None:
    p = RuleRegimeProvider(cfg)
    a = await p.assess(_row(x_btc_adx_1h=25, x_btc_ema_trend_1h=0.3, x_breadth_ema50=50), TS)
    assert a.regime is Regime.RANGE and a.risk_multiplier == 0.7
    assert a.enabled_strategies == [StrategyId.S2_MEAN_REVERSION, StrategyId.S3_IMPLIED_DEV]
    nan = await p.assess(_row(x_btc_adx_1h=float("nan")), TS)
    assert nan.regime is Regime.RANGE and any("insufficient" in r for r in nan.reasons)


def test_market_features_on_synthetic() -> None:
    m = make_synthetic_market(seed=2, days=12)
    feats = {}
    for sym in ("BTC/TMN", "ETH/TMN"):
        df = m[sym]
        feats[sym] = build_features(
            {
                Timeframe.M15: df,
                Timeframe.H1: resample_ohlcv(df, "1h"),
                Timeframe.H4: resample_ohlcv(df, "4h"),
            }
        )
    mf = market_features(feats)
    for col in (
        "x_btc_ret_1h",
        "x_btc_ret_4h",
        "x_btc_adx_1h",
        "x_breadth_ema50",
        "x_avg_ret_4h",
        "x_rv_percentile_7d",
    ):
        assert col in mf.columns
    late = mf.iloc[-1]
    assert not np.isnan(late).any()
    assert 0 <= late["x_breadth_ema50"] <= 100 and 0 <= late["x_rv_percentile_7d"] <= 100
    assert late["x_btc_ret_1h"] == pytest.approx(feats["BTC/TMN"].iloc[-1]["15m_ret_4"])
