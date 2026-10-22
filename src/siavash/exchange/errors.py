"""Wallex error hierarchy (T-102). Mapping from HTTP responses lives in ``endpoints.parse_error``."""

from __future__ import annotations

from typing import Any

from siavash.exchange import endpoints as ep


class WallexError(Exception):
    """Base class for all exchange errors."""

    kind = "unknown"

    def __init__(self, message: str = "", status: int | None = None, body: Any = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message
        self.status = status
        self.body = body


class AuthError(WallexError):
    kind = ep.ERR_AUTH


class RateLimited(WallexError):
    kind = ep.ERR_RATE_LIMITED

    def __init__(
        self, message: str = "", status: int | None = 429, body: Any = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, status, body)
        self.retry_after = retry_after


class InsufficientBalance(WallexError):
    kind = ep.ERR_INSUFFICIENT_BALANCE


class InvalidPrecision(WallexError):
    kind = ep.ERR_INVALID_PRECISION


class BelowMinimum(WallexError):
    kind = ep.ERR_BELOW_MINIMUM


class MarketClosed(WallexError):
    kind = ep.ERR_MARKET_CLOSED


class OrderNotFound(WallexError):
    kind = ep.ERR_ORDER_NOT_FOUND


class ServerError(WallexError):
    kind = ep.ERR_SERVER


class NetworkError(WallexError):
    """Connection problems and timeouts (request outcome unknown)."""

    kind = "network"


class BadRequest(WallexError):
    kind = ep.ERR_BAD_REQUEST


class SymbolNotAllowed(WallexError):
    """Raised by ``SymbolGuard`` before any request is sent."""

    kind = "symbol_not_allowed"


_BY_KIND: dict[str, type[WallexError]] = {
    cls.kind: cls
    for cls in (
        AuthError,
        RateLimited,
        InsufficientBalance,
        InvalidPrecision,
        BelowMinimum,
        MarketClosed,
        OrderNotFound,
        ServerError,
        BadRequest,
    )
}


def error_from_response(status: int, body: Any, retry_after: float | None = None) -> WallexError:
    """Build the matching ``WallexError`` subclass for an HTTP error response."""
    kind, message = ep.parse_error(status, body)
    cls = _BY_KIND.get(kind, BadRequest)
    if cls is RateLimited:
        return RateLimited(message, status, body, retry_after=retry_after)
    return cls(message, status, body)
