"""T-301: indicators vs independent loop reference + anti-lookahead."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from siavash.features import indicators as ind

REF = Path(__file__).resolve().parents[2] / "fixtures" / "indicators_reference.csv"


@pytest.fixture(scope="module")
def ref() -> pd.DataFrame:
    return pd.read_csv(REF)


def _compute(df: pd.DataFrame) -> dict[str, pd.Series]:
    mid, upper, lower, width = ind.bollinger(df["close"], 20, 2)
    m, sig, hist = ind.macd(df["close"])
    dh, dl = ind.donchian(df, 20)
    k, d = ind.stochastic(df, 14, 3)
    return {
        "ema20": ind.ema(df["close"], 20),
        "sma20": ind.sma(df["close"], 20),
        "rsi14": ind.rsi(df["close"], 14),
        "atr14": ind.atr(df, 14),
        "bb_mid": mid,
        "bb_upper": upper,
        "bb_lower": lower,
        "bb_width_pct": width,
        "adx14": ind.adx(df, 14),
        "macd": m,
        "macd_signal": sig,
        "macd_hist": hist,
        "donchian_high": dh,
        "donchian_low": dl,
        "stoch_k": k,
        "stoch_d": d,
        "obv": ind.obv(df),
        "zscore20": ind.zscore(df["close"], 20),
        "rv20": ind.realized_vol(df["close"], 20),
    }


def test_matches_reference(ref: pd.DataFrame) -> None:
    got = _compute(ref[["open", "high", "low", "close", "volume"]])
    for name, series in got.items():
        expected = ref[name].to_numpy(dtype=float)
        actual = series.to_numpy(dtype=float)
        assert np.array_equal(np.isnan(expected), np.isnan(actual)), f"NaN mask differs for {name}"
        mask = ~np.isnan(expected)
        assert mask.sum() > 200, name
        np.testing.assert_allclose(actual[mask], expected[mask], rtol=1e-9, atol=1e-6, err_msg=name)


def test_value_ranges(ref: pd.DataFrame) -> None:
    got = _compute(ref)
    for name in ("rsi14", "stoch_k", "adx14"):
        s = got[name].dropna()
        assert ((s >= 0) & (s <= 100)).all(), name


def test_no_lookahead(ref: pd.DataFrame) -> None:
    df = ref[["open", "high", "low", "close", "volume"]].copy()
    base = _compute(df)
    rng = np.random.default_rng(0)
    for t in (60, 150, 250):
        tampered = df.copy()
        tampered.iloc[t + 1 :] = tampered.iloc[t + 1 :] * rng.uniform(0.5, 1.5, size=(len(df) - t - 1, 1))
        changed = _compute(tampered)
        for name in base:
            a, b = base[name].iloc[: t + 1], changed[name].iloc[: t + 1]
            pd.testing.assert_series_equal(a, b, check_names=False, obj=f"{name}@{t}")


def test_donchian_excludes_current_bar() -> None:
    df = pd.DataFrame({"high": [1.0, 2, 3, 10], "low": [0.5, 1, 2, 9]})
    hi, lo = ind.donchian(df, 3)
    assert hi.iloc[3] == 3.0 and lo.iloc[3] == 0.5
