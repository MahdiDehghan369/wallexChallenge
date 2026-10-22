"""Signal generator registry (T-305)."""

from __future__ import annotations

from typing import Any

from siavash.core.interfaces import SignalGenerator
from siavash.signals.implied_dev import ImpliedDeviation
from siavash.signals.meanrev import MeanReversion
from siavash.signals.trend import TrendBreakout


def build_generators(cfg: Any) -> list[SignalGenerator]:
    """All generators; each one returns nothing when disabled in config or by regime."""
    return [TrendBreakout(cfg), MeanReversion(cfg), ImpliedDeviation(cfg)]
