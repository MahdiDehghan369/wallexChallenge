"""Triple-barrier labelling of trade candidates (T-401).

Entry at the **open of the next candle** (+ slippage). Then candles are walked in order:
- a candle that touches both SL (``low <= SL``) and TP (``high >= TP``) counts as **SL** (pessimistic);
- a candle that opens below SL exits at its open (gap), otherwise at SL (− slippage);
- TP (``tp1_price`` when set, e.g. S1) → label 1, exit at the TP price (limit order, no slippage);
- SL → label 0;
- time-stop (``time_stop_minutes`` after the signal) → exit at the last close (− slippage),
  label 1 if the net return > 0 else 0.
Net return is after the round-trip fee (``fee_pct`` = entry + exit fee, in percent).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

import pandas as pd

from siavash.core.models import Candidate

ExitReason = Literal["TP", "SL", "TIME"]


def label_candidate(
    c: Candidate, future_bars: pd.DataFrame, fee_pct: float, slippage_pct: float
) -> tuple[int, float, ExitReason, int]:
    """``(label, realized_ret_pct, exit_reason, bars_held)``.

    ``future_bars``: candles that open at or after ``c.ts`` (columns ``ts_open, open, high, low, close``),
    i.e. data strictly after the signal candle closed — no lookahead into the signal itself.
    """
    bars = future_bars[future_bars["ts_open"] >= c.ts].sort_values("ts_open")
    if bars.empty:
        raise ValueError(f"no bars after {c.ts} for {c.symbol}")
    slip = slippage_pct / 100
    entry = float(bars["open"].iloc[0]) * (1 + slip)
    sl = c.stop_loss
    tp = c.tp1_price if c.tp1_price is not None else c.take_profit
    deadline = c.ts + timedelta(minutes=c.time_stop_minutes)

    def net(exit_price: float) -> float:
        return (exit_price / entry - 1) * 100 - fee_pct

    held = 0
    last_close = entry
    for row in bars.itertuples(index=False):
        if row.ts_open >= deadline:
            break
        held += 1
        o, h, lo = float(row.open), float(row.high), float(row.low)
        if lo <= sl:  # SL first: also when TP is touched in the same candle
            exit_price = min(o, sl) * (1 - slip)
            return 0, net(exit_price), "SL", held
        if h >= tp:
            return 1, net(tp), "TP", held
        last_close = float(row.close)
    r = net(last_close * (1 - slip))
    return (1 if r > 0 else 0), r, "TIME", held
