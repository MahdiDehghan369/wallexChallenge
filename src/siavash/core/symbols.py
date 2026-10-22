"""Canonical symbol helpers (section 8.3 contract).

Canonical form is ``BASE/QUOTE`` upper-case (``BTC/TMN``). The exchange form (``BTCTMN``) is
only used inside ``siavash.exchange``.
"""

from __future__ import annotations


def to_canonical(exchange_symbol: str, known_quotes: tuple[str, ...] = ("TMN", "USDT")) -> str:
    """``"BTCTMN" -> "BTC/TMN"``; already-canonical input is normalised and returned."""
    s = exchange_symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in s:
        base, quote = s.split("/", 1)
        if not base or not quote:
            raise ValueError(f"invalid symbol: {exchange_symbol!r}")
        return f"{base}/{quote}"
    # longest quote first so e.g. "USDT" wins over a hypothetical "T"
    for quote in sorted(known_quotes, key=len, reverse=True):
        q = quote.upper()
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}/{q}"
    raise ValueError(f"cannot split symbol {exchange_symbol!r} with quotes {known_quotes}")


def to_exchange(symbol: str) -> str:
    """``"BTC/TMN" -> "BTCTMN"``."""
    base, quote = split(symbol)
    return f"{base}{quote}"


def split(symbol: str) -> tuple[str, str]:
    """``"BTC/TMN" -> ("BTC", "TMN")`` (accepts exchange form too)."""
    canonical = to_canonical(symbol)
    base, quote = canonical.split("/", 1)
    return base, quote
