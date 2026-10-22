"""Edge diagnostics: simulator ≡ T-401 labeller, maker fills, trailing stop, profiles, hold-out split."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from siavash.config import load_config
from siavash.core.models import Candidate
from siavash.core.types import StrategyId
from siavash.ml.labeling import label_candidate
from siavash.research.diagnostics import nested_filter, walk_forward_select, zero_fee_report
from siavash.research.profiles import expand, load_profiles, load_settings, variant_config
from siavash.research.simulate import Bars, Setup, labels, net_return, non_overlapping, simulate

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_open": [pd.Timestamp(T0 + timedelta(minutes=15 * i)) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        }
    )


def _row(res: object, slip: float) -> pd.DataFrame:
    d = {k: [getattr(res, k)] for k in ("filled", "entry_raw", "exit_raw", "entry_slip", "entry_maker")}
    d |= {k: [getattr(res, k)] for k in ("exit_slip", "exit_maker", "reason")}
    return pd.DataFrame({**d, "slip_pct": [slip]})


def test_taker_matches_label_candidate() -> None:
    rng = np.random.default_rng(7)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, 400)))
    rows = [(p, p * (1 + abs(rng.normal(0, 0.003))), p * (1 - abs(rng.normal(0, 0.003))), p) for p in px]
    df = _bars(rows)
    bars = Bars.from_frame(df)
    for i in range(0, 300, 7):
        ts = T0 + timedelta(minutes=15 * i)
        e = float(px[i - 1]) if i else float(px[0])
        sl, tp = e * (1 - rng.uniform(0.003, 0.02)), e * (1 + rng.uniform(0.003, 0.02))
        c = Candidate(
            candidate_id="x",
            ts=ts,
            symbol="BTC/TMN",
            strategy=StrategyId.S1_TREND_BREAKOUT,
            entry_ref_price=e,
            stop_loss=sl,
            take_profit=tp,
            time_stop_minutes=int(rng.choice([180, 360, 720])),
            expected_move_pct=1.0,
            conditions={},
        )
        s = Setup(pd.Timestamp(ts), c.symbol, "S1", e, sl, tp, c.time_stop_minutes, 1.0)
        res = simulate(bars, s)
        assert res is not None
        for fee, slip in ((0.0, 0.0), (0.4, 0.1), (0.7, 0.125)):
            n = -(-c.time_stop_minutes // 15)
            lab, ret, reason, held = label_candidate(c, df.iloc[i : i + n], fee, slip)
            r = _row(res, slip)
            net = net_return(r, fee / 2, fee / 2)
            assert net.iloc[0] == pytest.approx(ret, abs=1e-9)
            assert labels(r, net).iloc[0] == lab and res.reason == reason and res.bars_held == held


def test_maker_fill_rules() -> None:
    # entry limit 100: bar 0 never trades below → no fill within ttl=1
    df = _bars([(101, 102, 100, 101), (101, 102, 99.5, 100), *[(100, 100.5, 99.8, 100)] * 20])
    bars = Bars.from_frame(df)
    s = Setup(pd.Timestamp(T0), "BTC/TMN", "S1", 100.0, 98.0, 101.0, 300, 1.0)
    assert simulate(bars, s, "maker", entry_ttl_bars=1).reason == "NOFILL"  # type: ignore[union-attr]
    # ttl=2: fills in bar 1 at the limit; the TP of 101 is touched on the fill bar (102) → not counted
    r = simulate(bars, s, "maker", entry_ttl_bars=2)
    assert r is not None and r.filled and r.entry_raw == 100.0 and r.entry_maker and not r.entry_slip
    assert r.reason == "TIME" and not r.exit_maker and r.exit_slip
    # TP needs a strictly higher trade on a later bar
    df2 = _bars([(100.5, 100.6, 99.9, 100.2), (100.2, 101.0, 100.1, 100.9), (100.9, 101.2, 100.8, 101.1)])
    r2 = simulate(
        Bars.from_frame(df2), Setup(pd.Timestamp(T0), "X/TMN", "S1", 100.0, 98.0, 101.0, 45, 1.0), "maker"
    )
    assert r2 is not None and r2.reason == "TP" and r2.exit_maker and r2.bars_held == 3
    net = net_return(_row(r2, 0.2), 0.1, 0.3)  # maker legs: no slippage, maker fee
    assert net.iloc[0] == pytest.approx(1.0 - 0.2)


def test_trailing_stop() -> None:
    df = _bars(
        [(100, 101, 99.5, 101), (101, 104, 100.5, 104), (104, 104.2, 101.5, 102), *[(102, 102, 102, 102)] * 5]
    )
    s = Setup(pd.Timestamp(T0), "X/TMN", "TF", 100.0, 97.0, 120.0, 120, 20.0, trail_dist=2.0)
    r = simulate(Bars.from_frame(df), s)
    # after bar 1 the stop is 104 − 2 = 102; bar 2 trades down to 101.5 → trailing exit at 102
    assert r is not None and r.reason == "TRAIL" and r.exit_raw == 102.0 and r.bars_held == 3


def test_non_overlapping() -> None:
    ts = [pd.Timestamp(T0 + timedelta(hours=h)) for h in (0, 1, 3, 3)]
    df = pd.DataFrame(
        {
            "variant": ["v", "v", "v", "w"],
            "symbol": ["A"] * 4,
            "ts": ts,
            "exit_ts": [ts[0] + timedelta(hours=2), ts[1], ts[2] + timedelta(hours=1), ts[3]],
            "filled": True,
        }
    )
    assert non_overlapping(df).tolist() == [True, False, True, True]


def test_repo_profiles_load_and_expand() -> None:
    cfg = load_config(ROOT / "config", env_file=None)
    settings = load_settings(ROOT / "config" / "profiles")
    profiles = load_profiles(ROOT / "config" / "profiles")
    assert {p.name for p in profiles} >= {
        "baseline",
        "a_h1_breakout",
        "b_maker",
        "c_s3_strict",
        "d_trend_top3",
    }
    assert settings.fee_rt_pct == [0.0, 0.2, 0.3, 0.4, 0.5, 0.7] and settings.holdout_days == 20
    vs = [v for p in profiles for v in expand(p)]
    assert len({v.key for v in vs}) == len(vs) == 14
    c = next(v for v in vs if v.key == "c_s3_strict[z_entry=3]")
    vcfg = variant_config(cfg, c)
    assert vcfg.strategy.s3.z_entry == 3.0 and vcfg.strategy.strategies.enabled == ["S3_IMPLIED_DEV"]
    assert (
        vcfg.strategy.s1 == cfg.strategy.s1 and len(cfg.strategy.strategies.enabled) == 3
    )  # cfg not mutated


def test_walk_forward_select_uses_only_past_windows() -> None:
    t = pd.DataFrame(
        {
            "variant": ["a", "b", "a", "b", "a", "b"],
            "window": [0, 0, 1, 1, 2, 2],
            "net": [1.0, -1.0, -5.0, 2.0, 0.5, 0.5],
            "ts": pd.date_range("2026-01-01", periods=6, freq="D", tz="UTC"),
        }
    )
    oos, picks = walk_forward_select(t, ["a", "b"], 3, min_trades=1)
    assert picks == [None, "a", "b"]  # window 1: a led window 0; window 2: b leads windows 0–1
    assert oos["net"].tolist() == [-5.0, 0.5]


def test_nested_filter_skips_first_fold() -> None:
    o = pd.DataFrame(
        {
            "fold": [0] * 4 + [1] * 4,
            "ts": pd.date_range("2026-01-01", periods=8, freq="D", tz="UTC"),
            "p_raw": [0.9, 0.8, 0.2, 0.1, 0.9, 0.8, 0.2, 0.1],
            "ret_pct": [1.0, 1.0, -1.0, -1.0, 2.0, -1.0, 3.0, 3.0],
        }
    )
    kept, base = nested_filter(o, [0.5, 1.0], min_tpd=0.0)
    assert len(base) == 4 and kept["ret_pct"].tolist() == [2.0, -1.0]


def test_zero_fee_report(tmp_path: Path) -> None:
    m = {
        "result": {
            "markets": [
                {"base_asset": "AAA", "quote_asset": "TMN", "is_spot": True, "is_zero_fee": True},
                {"base_asset": "BBB", "quote_asset": "USDT", "is_spot": True, "is_zero_fee": True},
                {"base_asset": "CCC", "quote_asset": "TMN", "is_spot": True, "is_zero_fee": True},
                {"base_asset": "DDD", "quote_asset": "TMN", "is_spot": True, "is_zero_fee": False},
            ]
        }
    }
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m))
    text = zero_fee_report(p, frozenset({"CCC/TMN"}), ["TMN"])
    assert "`is_zero_fee: true`: **3**" in text and "zero fee: 1** → AAA/TMN" in text
    assert "CCC/TMN: excluded (denylist)" in text
