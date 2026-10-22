"""Fee-free trade simulation on 15m bars, with costs applied afterwards.

``simulate`` returns raw entry / exit prices and which legs were maker or paid slippage; ``net_return``
then applies any fee scenario. In taker mode the outcome equals ``ml.labeling.label_candidate``
for the same fee and slippage (unit-tested), so re-pricing a fee scenario is exactly the same as
re-running the labelling with that fee.

Taker mode (same rules as T-401): entry at the next open + slippage; a bar touching both SL and TP
counts as SL; SL exits at ``min(open, SL)`` − slippage; TP is a limit exit (no slippage); time stop
exits at the last close − slippage. An optional trailing stop (``trail_dist``, price units) rises
with each bar's high **after** that bar was checked against the previous stop. An optional signal
exit (``exit_flags[k]`` = the exit condition holds at the close of bar ``k``) exits at the next
bar's open − slippage, if that bar is still inside the time-stop window.

Maker mode: a resting buy limit at ``limit``; it fills only if a bar trades **strictly below** it
within ``entry_ttl_bars`` (queue position unknown), otherwise the setup is ``NOFILL``. TP is a
resting sell limit that fills only if a bar trades strictly above it, never on the fill bar
(the high may have come before the fill). SL and time-stop exits stay taker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

ExitKind = Literal["TP", "SL", "TRAIL", "TIME", "NOFILL", "SIGNAL"]
BAR_S = 900


@dataclass(frozen=True)
class Setup:
    """A long setup produced at ``ts`` (close time of the signal candle)."""

    ts: pd.Timestamp
    symbol: str
    strategy: str
    entry_ref: float
    stop_loss: float
    take_profit: float
    time_stop_minutes: int
    expected_move_pct: float
    trail_dist: float | None = None


@dataclass(frozen=True)
class SimResult:
    filled: bool
    entry_raw: float
    exit_raw: float
    entry_slip: bool
    entry_maker: bool
    exit_slip: bool
    exit_maker: bool
    reason: ExitKind
    bars_held: int
    exit_ts: pd.Timestamp  # close time of the exit bar (setup ts for NOFILL)


@dataclass(frozen=True)
class Bars:
    """15m bars of one symbol as arrays (``open_s`` = open time in epoch seconds)."""

    open_s: np.ndarray
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray

    @classmethod
    def from_frame(cls, m15: pd.DataFrame) -> Bars:
        d = m15.dropna(subset=["close"]).sort_values("ts_open")
        return cls(
            (d["ts_open"].astype("int64") // 10**9).to_numpy(),
            d["open"].to_numpy(dtype=float),
            d["high"].to_numpy(dtype=float),
            d["low"].to_numpy(dtype=float),
            d["close"].to_numpy(dtype=float),
        )


def window(bars: Bars, s: Setup) -> tuple[int, int] | None:
    """``(i0, n)``: first bar opening at/after the signal and the bars of the time-stop window;
    ``None`` if the window runs past the data (the outcome would be biased)."""
    t0 = int(pd.Timestamp(s.ts).timestamp())
    i0 = int(np.searchsorted(bars.open_s, t0, side="left"))
    n = math.ceil(s.time_stop_minutes / 15)
    if i0 + n > len(bars.open_s):
        return None
    return i0, n


def simulate(
    bars: Bars,
    s: Setup,
    mode: Literal["taker", "maker"] = "taker",
    entry_ttl_bars: int = 2,
    limit: float | None = None,
    exit_flags: np.ndarray | None = None,
) -> SimResult | None:
    w = window(bars, s)
    if w is None:
        return None
    i0, _ = w
    t0 = int(pd.Timestamp(s.ts).timestamp())
    deadline = t0 + s.time_stop_minutes * 60
    end = int(np.searchsorted(bars.open_s, deadline, side="left"))  # bars with ts_open < deadline
    if end <= i0:
        raise ValueError(f"no bars after {s.ts} for {s.symbol}")
    if mode == "taker":
        return _run(
            bars,
            s,
            i0,
            end,
            entry=float(bars.o[i0]),
            maker=False,
            first_bar_is_fill=False,
            exit_flags=exit_flags,
        )
    px = s.entry_ref if limit is None else limit
    for j in range(i0, min(i0 + entry_ttl_bars, end)):
        if bars.lo[j] < px:
            return _run(bars, s, j, end, entry=px, maker=True, first_bar_is_fill=True)
    ts = pd.Timestamp(s.ts)
    return SimResult(False, px, px, False, True, False, True, "NOFILL", 0, ts)


def _run(
    bars: Bars,
    s: Setup,
    i: int,
    end: int,
    entry: float,
    maker: bool,
    first_bar_is_fill: bool,
    exit_flags: np.ndarray | None = None,
) -> SimResult:
    stop, tp = s.stop_loss, s.take_profit
    last_close = entry
    held = 0
    for k in range(i, end):
        held += 1
        o, h, lo = float(bars.o[k]), float(bars.h[k]), float(bars.lo[k])
        fill_bar = first_bar_is_fill and k == i
        if lo <= stop:
            px = stop if fill_bar else min(o, stop)
            reason: ExitKind = "TRAIL" if stop > s.stop_loss else "SL"
            return _res(bars, k, entry, px, maker, False, reason, held)
        if maker and not fill_bar and h > tp:
            return _res(bars, k, entry, tp, maker, True, "TP", held)
        if not maker and h >= tp:
            return _res(bars, k, entry, tp, maker, False, "TP", held, exit_slip=False)
        if s.trail_dist is not None:
            stop = max(stop, h - s.trail_dist)
        if exit_flags is not None and exit_flags[k] and k + 1 < end:
            return _res(bars, k + 1, entry, float(bars.o[k + 1]), maker, False, "SIGNAL", held + 1)
        last_close = float(bars.c[k])
    return _res(bars, end - 1, entry, last_close, maker, False, "TIME", held)


def _res(
    bars: Bars,
    k: int,
    entry: float,
    exit_px: float,
    entry_maker: bool,
    exit_maker: bool,
    reason: ExitKind,
    held: int,
    exit_slip: bool | None = None,
) -> SimResult:
    slip_out = (not exit_maker) if exit_slip is None else exit_slip
    ts = pd.Timestamp(int(bars.open_s[k]) + BAR_S, unit="s", tz="UTC")
    return SimResult(
        True, entry, exit_px, not entry_maker, entry_maker, slip_out, exit_maker, reason, held, ts
    )


def gross_return(df: pd.DataFrame) -> pd.Series:
    """Return before fees and slippage, percent."""
    return (df["exit_raw"] / df["entry_raw"] - 1.0) * 100.0


def net_return(df: pd.DataFrame, maker_pct: float, taker_pct: float) -> pd.Series:
    """Net return, percent, after per-leg fees and slippage on taker legs (``slip_pct`` one-way)."""
    s = df["slip_pct"] / 100.0
    entry = df["entry_raw"] * (1.0 + s * df["entry_slip"].astype(float))
    exit_ = df["exit_raw"] * (1.0 - s * df["exit_slip"].astype(float))
    fee_in = np.where(df["entry_maker"], maker_pct, taker_pct)
    fee_out = np.where(df["exit_maker"], maker_pct, taker_pct)
    return (exit_ / entry - 1.0) * 100.0 - fee_in - fee_out


def labels(df: pd.DataFrame, net: pd.Series) -> pd.Series:
    """T-401 label: TP → 1, stop → 0, trailing / time exit → ``net > 0``."""
    out = (net > 0).astype(int)
    out[df["reason"] == "TP"] = 1
    out[df["reason"] == "SL"] = 0
    return out


def non_overlapping(df: pd.DataFrame, by: tuple[str, ...] = ("variant", "symbol")) -> pd.Series:
    """Mask of trades taken with one position per ``by`` group: a setup is skipped while the
    previous taken trade of its group is still open (``ts < exit_ts``). Unfilled setups are skipped."""
    keep = pd.Series(False, index=df.index)
    for _, g in df[df["filled"]].sort_values("ts").groupby(list(by), sort=False):
        busy_until = None
        for idx, ts, exit_ts in zip(g.index, g["ts"], g["exit_ts"], strict=True):
            if busy_until is None or ts >= busy_until:
                keep[idx] = True
                busy_until = exit_ts
    return keep
