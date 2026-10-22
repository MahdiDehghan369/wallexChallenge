"""Research v2: dense grid, signal exit, signals without lookahead, portfolio drawdown, variant count."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from siavash.config import load_config
from siavash.research import v2
from siavash.research.simulate import Bars, Setup, simulate

ROOT = Path(__file__).resolve().parents[2]
T0 = pd.Timestamp("2026-01-01", tz="UTC")


def _m15(closes: list[float], start: pd.Timestamp = T0, skip: tuple[int, ...] = ()) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        if i in skip:
            continue
        rows.append(
            {"ts_open": start + i * v2.BAR, "open": c, "high": c, "low": c, "close": c, "volume": 1.0}
        )
    return pd.DataFrame(rows)


def test_repo_config_has_23_variants() -> None:
    rc = v2.load_v2(ROOT / "config" / "research_v2.yaml")
    vs = v2.variants(rc)
    assert len(vs) == 23
    assert {h: sum(v.hypothesis == h for v in vs) for h in v2.HYPOTHESES} == {"H1": 9, "H2": 12, "H3": 2}
    assert len({v.key for v in vs}) == 23


def test_dense_grid_fills_gaps_without_inventing_moves() -> None:
    d = v2.dense_15m(_m15([10, 11, 12, 13], skip=(1, 2)), T0, T0 + 4 * v2.BAR)
    assert list(d["close"]) == [10, 10, 10, 13]
    assert list(d["real"]) == [True, False, False, True]
    assert list(d["age"]) == [0, 1, 2, 0]
    assert d.iloc[1][["open", "high", "low"]].tolist() == [10, 10, 10] and d.iloc[1]["volume"] == 0


def test_signal_exit_at_next_open() -> None:
    n = 10
    open_s = (T0 + pd.Series(range(n)) * v2.BAR).astype("int64").to_numpy() // 10**9
    px = np.array([100, 100, 101, 102, 103, 104, 105, 106, 107, 108], dtype=float)
    bars = Bars(open_s, px, px + 0.5, px - 0.5, px)
    flags = np.zeros(n, dtype=bool)
    flags[3] = True  # condition true at the close of bar 3 → exit at the open of bar 4
    s = Setup(T0 + v2.BAR, "X/TMN", "H1", 100, 90, 200, 90, 1.0)
    r = simulate(bars, s, exit_flags=flags)
    assert r is not None and r.reason == "SIGNAL" and r.exit_raw == px[4] and r.entry_raw == px[1]
    assert simulate(bars, s).reason == "TIME"  # type: ignore[union-attr]


def test_h1_trades_use_only_closed_bars() -> None:
    rc = v2.load_v2(ROOT / "config" / "research_v2.yaml")
    cfg = load_config(ROOT / "config")
    n = 4 * 24 * 14
    start, end = T0, T0 + n * v2.BAR
    tmn = [100.0] * n
    tmn[1000] = 97.0  # one-bar dislocation of −3 % vs the implied price
    tmn[1001:1010] = [97.5] * 9
    frames = {"AAA/TMN": {v2.Timeframe.M15: _m15(tmn)}}
    usdt = {
        "AAA/USDT": {v2.Timeframe.M15: _m15([1.0] * n)},
        "USDT/TMN": {v2.Timeframe.M15: _m15([100.0] * n)},
    }
    rc = {**rc, "warmup_days": 1}
    t, dense = v2.build_trades(frames, ["AAA/TMN"], usdt, rc, cfg, start, end)
    h1 = t[t["hypothesis"] == "H1"]
    assert len(h1) == 9  # every H1 variant triggers once (−3 % ≤ −X for all X)
    sig_close = T0 + 1001 * v2.BAR
    assert (h1["ts"] == sig_close).all()  # signal = close of the dislocated bar, never earlier
    assert (h1["entry_raw"] == 97.5).all()  # fill = next open
    t = v2.add_returns(t, dense, [0.5], start + pd.Timedelta(days=1))
    assert {"gross", "excess", "excess_idx", "net_0.5"} <= set(t.columns)


def test_portfolio_drawdown_caps_concurrency() -> None:
    ts = pd.to_datetime(["2026-01-01 00:00", "2026-01-01 00:00", "2026-01-01 01:00"], utc=True)
    t = pd.DataFrame({"ts": ts, "exit_ts": ts + pd.Timedelta(hours=2), "net": [-10.0, -10.0, 50.0]})
    # max 1 open: only the first trade (−10 % on 20 %) is taken → DD 2 %
    assert v2.portfolio_drawdown(t, "net", 0.2, 1) == (pytest.approx(2.0), 1)
    dd, taken = v2.portfolio_drawdown(t, "net", 0.2, 5)
    assert taken == 3 and dd == pytest.approx(4.0)  # two losers exit before the winner


def test_verdict_requires_holdout() -> None:
    rc = v2.load_v2(ROOT / "config" / "research_v2.yaml")
    good = {
        "n": 100,
        "mean_net": 0.5,
        "excess": 0.6,
        "windows_pos": 5,
        "windows_oos": 6,
        "trades_per_day": 2.0,
    }
    dev = {"hyp": {h: {"0.5": good} for h in v2.HYPOTHESES}}
    assert not any(x["pass"] for x in v2.verdict(dev, None, rc).values())
    ho = {h: {"0.5": {"n": 50, "mean_net": 0.1, "equity_dd": 3.0}} for h in v2.HYPOTHESES}
    assert all(x["pass"] for x in v2.verdict(dev, ho, rc).values())
    ho_bad = {h: {"0.5": {"n": 50, "mean_net": 0.1, "equity_dd": 6.0}} for h in v2.HYPOTHESES}
    assert not any(x["pass"] for x in v2.verdict(dev, ho_bad, rc).values())
