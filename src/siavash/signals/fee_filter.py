"""Fee filter (T-306): a candidate is valid only if ``expected_move ≥ fee_multiple × round_trip_fee``."""

from __future__ import annotations

from typing import Any

from siavash.core.models import Candidate

FEE_TOO_HIGH = "FEE_TOO_HIGH"


def round_trip_fee_pct(cfg: Any) -> float:
    """Entry (maker if known, else taker) + exit (taker), in percent; pessimistic ``2 × taker`` if maker unknown."""
    fees = cfg.risk.fees
    entry = fees.maker_pct if fees.maker_pct is not None else fees.taker_pct
    return float(entry) + float(fees.taker_pct)


def min_expected_move_pct(cfg: Any) -> float:
    """Minimum expected move (percent) a candidate needs to pass."""
    return float(cfg.strategy.fee_multiple) * round_trip_fee_pct(cfg)


def passes_fee_filter(c: Candidate, cfg: Any) -> tuple[bool, str]:
    """``(True, "")`` or ``(False, "FEE_TOO_HIGH")``."""
    if c.expected_move_pct < min_expected_move_pct(cfg):
        return False, FEE_TOO_HIGH
    return True, ""
