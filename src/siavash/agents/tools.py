"""Read-only tools for the LLM agents (T-107).

Explicit allow-list of six read-only tools exposed with OpenAI function-calling schemas. Anything
else — including any order / cancel tool — raises ``ToolNotAllowed``. Each result is a compact JSON
string of at most ``MAX_CHARS`` characters, and every call is appended to ``logs/llm/tool_calls.jsonl``.
"""

from __future__ import annotations

import inspect
import json
import re
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from siavash.core.interfaces import MarketData
from siavash.core.types import Timeframe

MAX_CHARS = 1500


class ToolNotAllowed(Exception):
    pass


def _sym_param() -> dict[str, Any]:
    return {"type": "string", "description": "canonical market symbol, e.g. BTC/TMN"}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_ticker": {
        "description": "Last price, bid/ask and 24h quote volume of a market.",
        "parameters": {"type": "object", "properties": {"symbol": _sym_param()}, "required": ["symbol"]},
    },
    "get_orderbook_summary": {
        "description": "Best bid/ask, spread and depth within ±0.5% of a market.",
        "parameters": {"type": "object", "properties": {"symbol": _sym_param()}, "required": ["symbol"]},
    },
    "get_candles_summary": {
        "description": "OHLC summary and returns of the last n closed candles.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": _sym_param(),
                "tf": {"type": "string", "enum": ["15m", "1h", "4h"]},
                "n": {"type": "integer", "minimum": 2, "maximum": 96},
            },
            "required": ["symbol", "tf"],
        },
    },
    "get_recent_trades_summary": {
        "description": "Buy/sell split, VWAP and size of recent public trades.",
        "parameters": {"type": "object", "properties": {"symbol": _sym_param()}, "required": ["symbol"]},
    },
    "get_portfolio": {
        "description": "Bot equity, cash, exposure, drawdown and open positions.",
        "parameters": {"type": "object", "properties": {}},
    },
    "get_strategy_stats": {
        "description": "Recent performance of one strategy (trades, win rate, PnL).",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy": {"type": "string"},
                "hours": {"type": "integer", "minimum": 1, "maximum": 168},
            },
            "required": ["strategy"],
        },
    },
}
ALLOWED_TOOLS = frozenset(TOOL_SCHEMAS)
# order-action tool names (place / create / submit / cancel / modify an order) must never be exposed
ORDER_ACTION_RE = re.compile(
    r"(place|create|submit|cancel|modify|new|delete)_?orders?|^(buy|sell)\b", re.IGNORECASE
)
assert not any(ORDER_ACTION_RE.search(n) for n in ALLOWED_TOOLS), "order tools must never be exposed"

Provider = Callable[..., Awaitable[dict[str, Any]] | dict[str, Any]]


def _clip(obj: Any) -> str:
    s = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))
    return s if len(s) <= MAX_CHARS else s[: MAX_CHARS - 20] + '..."truncated"}'


async def _maybe(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


class ToolRegistry:
    """Native backend over ``MarketData`` plus portfolio / strategy-stats providers."""

    backend = "native"

    def __init__(
        self,
        market_data: MarketData,
        portfolio_fn: Provider | None = None,
        stats_fn: Provider | None = None,
        log_path: str | Path | None = "logs/llm/tool_calls.jsonl",
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.md = market_data
        self.portfolio_fn = portfolio_fn
        self.stats_fn = stats_fn
        self.log_path = Path(log_path) if log_path else None
        self.now = now_fn or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def openai_tools(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """Function-calling schemas for the allowed tools."""
        return [
            {"type": "function", "function": {"name": n, **TOOL_SCHEMAS[n]}}
            for n in (names or sorted(ALLOWED_TOOLS))
        ]

    def _log(self, name: str, args: dict[str, Any], status: str, size: int) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": self.now().isoformat(),
            "tool": name,
            "args": args,
            "status": status,
            "chars": size,
            "backend": self.backend,
        }
        with self._lock, self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        """Run an allowed tool; raises ``ToolNotAllowed`` for anything else."""
        if name not in ALLOWED_TOOLS or ORDER_ACTION_RE.search(name):
            self._log(name, args, "rejected", 0)
            raise ToolNotAllowed(f"tool {name!r} is not allowed (read-only allow-list)")
        try:
            result = await getattr(self, f"_t_{name}")(**args)
            out = _clip(result)
            self._log(name, args, "ok", len(out))
            return out
        except ToolNotAllowed:
            raise
        except Exception as e:
            self._log(name, args, f"error: {type(e).__name__}", 0)
            return _clip({"error": f"{type(e).__name__}: {e}"})

    # ------------------------------------------------------------------ tools
    async def _t_get_ticker(self, symbol: str) -> dict[str, Any]:
        t = await self.md.get_ticker(symbol)
        return {
            "symbol": symbol,
            "last": str(t.last),
            "bid": str(t.bid),
            "ask": str(t.ask),
            "vol24h_quote": str(t.volume_24h_quote),
        }

    async def _t_get_orderbook_summary(self, symbol: str) -> dict[str, Any]:
        from siavash.data.universe import depth_tmn_within

        b = await self.md.get_orderbook(symbol)
        return {
            "symbol": symbol,
            "best_bid": str(b.best_bid),
            "best_ask": str(b.best_ask),
            "spread_pct": round(b.spread_pct, 4) if b.spread_pct is not None else None,
            "depth_quote_0.5pct": str(depth_tmn_within(b, 0.5).quantize(1)) if b.mid else "0",
            "levels": [len(b.bids), len(b.asks)],
        }

    async def _t_get_candles_summary(self, symbol: str, tf: str = "15m", n: int = 24) -> dict[str, Any]:
        timeframe = Timeframe(tf)
        n = max(2, min(int(n), 96))
        end = self.now()
        candles = (
            await self.md.get_candles(
                symbol, timeframe, end - timedelta(seconds=timeframe.seconds * (n + 2)), end
            )
        )[-n:]
        if not candles:
            return {"symbol": symbol, "tf": tf, "candles": 0}
        closes = [c.close for c in candles]
        return {
            "symbol": symbol,
            "tf": tf,
            "candles": len(candles),
            "open": candles[0].open,
            "high": max(c.high for c in candles),
            "low": min(c.low for c in candles),
            "close": closes[-1],
            "ret_pct": round((closes[-1] / candles[0].open - 1) * 100, 3),
            "last5_close": [round(x, 6) for x in closes[-5:]],
            "volume": round(sum(c.volume for c in candles), 6),
        }

    async def _t_get_recent_trades_summary(self, symbol: str) -> dict[str, Any]:
        trades = await self.md.get_recent_trades(symbol, 100)
        if not trades:
            return {"symbol": symbol, "trades": 0}
        buy = sum(float(t.qty) for t in trades if t.side and t.side.value == "buy")
        sell = sum(float(t.qty) for t in trades if t.side and t.side.value == "sell")
        qty = sum(float(t.qty) for t in trades)
        vwap = sum(float(t.price) * float(t.qty) for t in trades) / qty if qty else 0.0
        return {
            "symbol": symbol,
            "trades": len(trades),
            "buy_qty": round(buy, 8),
            "sell_qty": round(sell, 8),
            "vwap": round(vwap, 6),
            "first": trades[0].ts.isoformat(),
            "last": trades[-1].ts.isoformat(),
        }

    async def _t_get_portfolio(self) -> dict[str, Any]:
        return await _maybe(self.portfolio_fn()) if self.portfolio_fn else {"error": "portfolio unavailable"}

    async def _t_get_strategy_stats(self, strategy: str, hours: int = 24) -> dict[str, Any]:
        return (
            await _maybe(self.stats_fn(strategy, int(hours)))
            if self.stats_fn
            else {"error": "stats unavailable"}
        )


def select_backend(cfg: Any, vendor_dir: str | Path = "docs/vendor/wallex_mcp") -> str:
    """``llm.tools_backend``: ``native`` | ``mcp`` | ``auto`` (mcp only if a server definition exists)."""
    from siavash.exchange.mcp_tools import mcp_server_config

    want = cfg.llm.tools_backend
    if want == "native":
        return "native"
    has = mcp_server_config(vendor_dir) is not None
    if want == "mcp" and not has:
        raise RuntimeError("llm.tools_backend=mcp but no MCP server definition in docs/vendor/wallex_mcp/")
    return "mcp" if has else "native"
