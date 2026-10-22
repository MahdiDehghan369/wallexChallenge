"""T-107: read-only tool allow-list."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from siavash.agents.tools import ALLOWED_TOOLS, MAX_CHARS, ToolNotAllowed, ToolRegistry, select_backend
from siavash.config import load_config
from siavash.core.models import OrderBook, OrderBookLevel, PublicTrade, Ticker
from siavash.core.types import Side, Timeframe
from siavash.exchange.mcp_tools import mcp_server_config
from tests.fakes.clock import FakeClock
from tests.fakes.market_data import InMemoryMarketData
from tests.fakes.synthetic import SYN_START, make_synthetic_market, to_candles

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)


def _registry(tmp_path: Path) -> ToolRegistry:
    clock = FakeClock(SYN_START + timedelta(days=2))
    df = make_synthetic_market(seed=1, days=3)["BTC/TMN"]
    md = InMemoryMarketData(
        candles={("BTC/TMN", Timeframe.M15): to_candles(df, "BTC/TMN", "15m")}, clock=clock
    )
    md.set_ticker(
        Ticker(
            symbol="BTC/TMN",
            ts=clock.now(),
            last=Decimal("100"),
            bid=Decimal("99"),
            ask=Decimal("101"),
            volume_24h_quote=Decimal("1e9"),
        )
    )
    md.set_book(
        OrderBook(
            symbol="BTC/TMN",
            ts=clock.now(),
            bids=[OrderBookLevel(price=Decimal("99"), qty=Decimal("5"))],
            asks=[OrderBookLevel(price=Decimal("101"), qty=Decimal("5"))],
        )
    )
    md.add_trades(
        "BTC/TMN",
        [
            PublicTrade(
                symbol="BTC/TMN",
                ts=clock.now() - timedelta(seconds=i),
                price=Decimal("100"),
                qty=Decimal("1"),
                side=Side.BUY if i % 2 else Side.SELL,
            )
            for i in range(400)
        ],
    )

    def stats(strategy: str, hours: int) -> dict[str, object]:
        return {"strategy": strategy, "hours": hours, "trades": 3, "notes": "x" * 5000}

    return ToolRegistry(
        md,
        portfolio_fn=lambda: {"equity": 5_000_000},
        stats_fn=stats,
        log_path=tmp_path / "tool_calls.jsonl",
        now_fn=clock.now,
    )


async def test_allow_list_and_outputs(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    assert {t["function"]["name"] for t in reg.openai_tools()} == ALLOWED_TOOLS
    assert json.loads(await reg.execute("get_ticker", {"symbol": "BTC/TMN"}))["last"] == "100"
    ob = json.loads(await reg.execute("get_orderbook_summary", {"symbol": "BTC/TMN"}))
    assert ob["spread_pct"] == pytest.approx(2.0)
    c = json.loads(await reg.execute("get_candles_summary", {"symbol": "BTC/TMN", "tf": "15m", "n": 8}))
    assert c["candles"] == 8
    tr = json.loads(await reg.execute("get_recent_trades_summary", {"symbol": "BTC/TMN"}))
    assert tr["trades"] == 100
    assert json.loads(await reg.execute("get_portfolio", {}))["equity"] == 5_000_000
    long = await reg.execute("get_strategy_stats", {"strategy": "S1_TREND_BREAKOUT", "hours": 24})
    assert len(long) <= MAX_CHARS
    lines = (tmp_path / "tool_calls.jsonl").read_text().splitlines()
    assert len(lines) == 6 and all(json.loads(x)["status"] == "ok" for x in lines)


@pytest.mark.parametrize("name", ["place_order", "cancel_order", "create_order", "transfer_x", "unknown"])
async def test_forbidden_tools(tmp_path: Path, name: str) -> None:
    with pytest.raises(ToolNotAllowed):
        await _registry(tmp_path).execute(name, {})


async def test_tool_error_is_reported(tmp_path: Path) -> None:
    out = json.loads(await _registry(tmp_path).execute("get_ticker", {"symbol": "ETH/TMN"}))
    assert "error" in out


def test_backend_selection(tmp_path: Path) -> None:
    assert select_backend(CFG, tmp_path) == "native"  # auto + no server.json
    (tmp_path / "server.json").write_text(json.dumps({"command": "x", "tool_map": {"get_ticker": "ticker"}}))
    assert select_backend(CFG, tmp_path) == "mcp"
    (tmp_path / "server.json").write_text(json.dumps({"command": "x", "tool_map": {"place_order": "order"}}))
    with pytest.raises(ValueError):
        mcp_server_config(tmp_path)
