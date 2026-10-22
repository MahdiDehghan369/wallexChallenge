"""Identifier helpers (section 8.3 contract)."""

from __future__ import annotations

from ulid import ULID

CLIENT_ORDER_PREFIX = "SIAV-"
LEGS = frozenset({"E", "X", "T", "G", "O", "S"})
# Wallex client-id length limit is UNVERIFIED (T-101). Until verified we keep the full ULID;
# set this to 16 once docs confirm a limit (see docs/wallex_api_notes.md).
DECISION_ID_MAX_LEN: int | None = None


def new_ulid() -> str:
    """New ULID string (monotonic-ish, sortable)."""
    return str(ULID())


def client_order_id(decision_id: str, leg: str, attempt: int) -> str:
    """فرمت: SIAV-{decision_id}-{leg}{attempt}  ؛ leg ∈ {E,X,T,G,O,S}
    (Entry, eXit, Tp1, endGame, Operator, Smoke). اگر والکس محدودیت طول دارد
    (T-101)، decision_id را به ۱۶ کاراکتر آخر کوتاه کن و در یادداشت API ثبت کن."""
    if leg not in LEGS:
        raise ValueError(f"invalid leg {leg!r}; expected one of {sorted(LEGS)}")
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if not decision_id:
        raise ValueError("decision_id is required")
    did = decision_id[-DECISION_ID_MAX_LEN:] if DECISION_ID_MAX_LEN else decision_id
    return f"{CLIENT_ORDER_PREFIX}{did}-{leg}{attempt}"


def is_ours(client_order_id: str) -> bool:
    return client_order_id.startswith("SIAV-")
