"""SymbolGuard (T-106): the last line of defence before any order leaves the bot.

A symbol is tradable only if it is not in ``config/denylist.txt`` (compared on canonical
``BASE/QUOTE``), is present in the market registry, is active and spot, and its quote is in
``allowed_quotes``. ``USDT/TMN`` is a data market: it is only allowed for SELL orders when
``allow_usdt_liquidation`` is set (end-game conversion of USDT to TMN).
"""

# Spot only (rule R1): this module — like the rest of the bot — has no code path for
# futures, margin, leverage, withdraw, transfer or deposit operations.

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from siavash.core.symbols import to_canonical
from siavash.core.types import Side
from siavash.exchange.errors import SymbolNotAllowed
from siavash.exchange.markets import MarketRegistry
from siavash.logging import get_logger

log = get_logger(__name__)

USDT_TMN = "USDT/TMN"


def load_denylist(path: str | Path) -> frozenset[str]:
    """Read the denylist (one symbol per line, ``#`` comments) as canonical symbols."""
    out = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.add(to_canonical(line))
    return frozenset(out)


class SymbolGuard:
    def __init__(
        self,
        denylist_path: str | Path,
        registry: MarketRegistry,
        allowed_quotes: Iterable[str] = ("TMN",),
        allow_usdt_liquidation: bool = False,
    ) -> None:
        self.denylist = load_denylist(denylist_path)
        self.registry = registry
        self.allowed_quotes = frozenset(q.upper() for q in allowed_quotes)
        self.allow_usdt_liquidation = allow_usdt_liquidation

    @classmethod
    def from_config(
        cls, cfg: object, registry: MarketRegistry, config_dir: str | Path = "config"
    ) -> SymbolGuard:
        """Build from ``AppConfig.risk`` (``allowed_quotes``, ``allow_usdt_liquidation``)."""
        risk = cfg.risk  # type: ignore[attr-defined]
        return cls(
            Path(config_dir) / "denylist.txt", registry, risk.allowed_quotes, risk.allow_usdt_liquidation
        )

    def is_denied(self, symbol: str) -> bool:
        try:
            return to_canonical(symbol) in self.denylist
        except ValueError:
            return True

    def is_tradable(self, symbol: str, side: Side | None = None) -> tuple[bool, str]:
        """``(ok, reason)``; ``reason`` is empty when tradable."""
        try:
            sym = to_canonical(symbol)
        except ValueError:
            return False, f"invalid symbol {symbol!r}"
        if sym in self.denylist:
            return False, f"{sym} is on the challenge denylist"
        if sym not in self.registry:
            return False, f"{sym} is not a known market"
        m = self.registry.get(sym)
        if not m.is_active:
            return False, f"{sym} is not active"
        if not m.is_spot:
            return False, f"{sym} is not a spot market"
        if sym == USDT_TMN:
            if self.allow_usdt_liquidation and side is Side.SELL:
                return True, ""
            return False, f"{sym} is data-only (SELL allowed only for end-game liquidation)"
        if m.quote not in self.allowed_quotes:
            return False, f"{sym}: quote {m.quote} not in allowed quotes {sorted(self.allowed_quotes)}"
        return True, ""

    def assert_tradable(self, symbol: str, side: Side | None = None) -> None:
        """Raise ``SymbolNotAllowed`` (and log a warning) if the symbol may not be traded."""
        ok, reason = self.is_tradable(symbol, side)
        if not ok:
            log.warning(
                "symbol_guard_reject", symbol=symbol, side=side.value if side else None, reason=reason
            )
            raise SymbolNotAllowed(reason)
