#!/usr/bin/env python
"""Smoke test against the REAL Wallex account (T-108). HUMAN ONLY — the agent never runs this.

Default (read-only): balances, account fees, markets, ticker, order book, candles and public trades for
BTC/TMN, with latency per call. ``--save-samples DIR`` stores the raw JSON responses (to confirm the
UNKNOWN items in docs/wallex_api_notes.md: status strings, list keys, ...).

``--place-test-order``: one LIMIT BUY of the minimum size at 20 % below the best bid, read back, and
cancelled immediately — through ``WallexAccount`` (SymbolGuard, rounding, minimums) with
``purpose="smoke_test"`` and an ``OPERATOR_COMMAND`` decision record. Asks for confirmation unless ``--yes``.

    python scripts/smoke_test.py
    python scripts/smoke_test.py --base-url https://api.wallex.com     # compare hosts (T-108)
    python scripts/smoke_test.py --save-samples docs/vendor/wallex/samples
    python scripts/smoke_test.py --place-test-order                    # only if the challenge rules allow it
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import Any

from siavash.config import load_config
from siavash.core.ids import client_order_id, new_ulid
from siavash.core.models import OrderRequest
from siavash.core.types import DecisionType, OrderType, Side, Timeframe
from siavash.engine.decision_log import JsonlDecisionLogger, build_record
from siavash.exchange import endpoints as ep
from siavash.exchange.account import OrderIdMap, WallexAccount
from siavash.exchange.client import WallexHttp
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.market_data import WallexMarketData
from siavash.exchange.markets import MarketRegistry
from siavash.logging import mask_value, register_secrets, setup_logging

SYMBOL = "BTC/TMN"


class Smoke:
    def __init__(self, samples: Path | None) -> None:
        self.samples = samples
        self.rows: list[tuple[str, str, float, str]] = []

    async def step(self, name: str, coro: Any) -> Any:
        t0 = time.perf_counter()
        try:
            out = await coro
            ok, detail = "OK", ""
        except Exception as e:  # report and continue with the next check
            out, ok, detail = None, "FAIL", f"{type(e).__name__}: {e}"[:160]
        self.rows.append((name, ok, (time.perf_counter() - t0) * 1000, detail))
        return out

    def save(self, name: str, raw: Any) -> None:
        if self.samples is None or raw is None:
            return
        self.samples.mkdir(parents=True, exist_ok=True)
        (self.samples / f"{name}.json").write_text(
            json.dumps(mask_value(raw), indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )

    def print(self) -> None:
        print(f"\n{'check':34} {'result':6} {'ms':>8}  detail")
        for name, ok, ms, detail in self.rows:
            print(f"{name:34} {ok:6} {ms:8.1f}  {detail}")


async def run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    register_secrets(cfg.secrets.raw_values())
    setup_logging("INFO", cfg.base.paths.logs)
    if args.base_url:
        cfg = cfg.model_copy(update={"wallex_base_url": args.base_url})
    http = WallexHttp.from_config(cfg)
    md = WallexMarketData(http, ticker_cache_s=0, min_notional=cfg.base.exchange.min_notional)
    registry = MarketRegistry(md.list_markets)
    s = Smoke(Path(args.save_samples) if args.save_samples else None)
    print(f"base url: {http.base_url}  key configured: {bool(cfg.secrets.wallex_api_key)}")
    try:
        s.save("markets", await s.step("GET markets (raw)", http.get(ep.Paths.MARKETS)))
        await s.step("MarketRegistry.load", registry.load())
        s.save(
            "depth",
            await s.step("GET depth BTCTMN (raw)", http.get(ep.Paths.DEPTH, params=ep.symbol_params(SYMBOL))),
        )
        ticker = await s.step("get_ticker BTC/TMN", md.get_ticker(SYMBOL))
        now = datetime.now(UTC)
        await s.step(
            "get_candles 15m (6h)", md.get_candles(SYMBOL, Timeframe.M15, now - timedelta(hours=6), now)
        )
        await s.step("get_recent_trades", md.get_recent_trades(SYMBOL))
        if not cfg.secrets.wallex_api_key:
            print("WALLEX_API_KEY not set: private checks skipped")
        else:
            s.save("balances", await s.step("GET balances (raw)", http.get(ep.Paths.BALANCES, private=True)))
            fees = await s.step("GET account fee (raw)", http.get(ep.Paths.FEE, private=True))
            s.save("fee", fees)
            if fees is not None:
                pct = ep.parse_fees(fees).get(SYMBOL)
                print(
                    f"account fee {SYMBOL}: maker/taker % = {pct}  -> copy into config/risk.yaml > fees (T-108)"
                )
            s.save(
                "open_orders",
                await s.step("GET openOrders (raw)", http.get(ep.Paths.OPEN_ORDERS, private=True)),
            )
            since = (now - timedelta(days=3)).date().isoformat()
            s.save(
                "orders_history",
                await s.step(
                    "GET orders history (raw)",
                    http.get(
                        ep.Paths.ORDERS, params={"from": since, "per_page": 10, "page": 1}, private=True
                    ),
                ),
            )
            if args.place_test_order:
                await test_order(cfg, http, registry, ticker, s, args.yes)
    finally:
        await http.aclose()
    s.print()
    return 0 if all(ok == "OK" for _, ok, _, _ in s.rows) else 1


async def test_order(
    cfg: Any, http: WallexHttp, registry: MarketRegistry, ticker: Any, s: Smoke, yes: bool
) -> None:
    if ticker is None or ticker.bid is None:
        print("no best bid: test order skipped")
        return
    m = registry.get(SYMBOL)
    price = registry.round_price(SYMBOL, ticker.bid * Decimal("0.8"), Side.BUY)
    qty = ((m.min_notional * Decimal("1.1")) / price / m.qty_step).to_integral_value(ROUND_UP) * m.qty_step
    qty = max(qty, m.min_qty)
    print(
        f"\nTEST ORDER: LIMIT BUY {qty} {SYMBOL} @ {price} (best bid {ticker.bid}, notional {qty * price:.0f} TMN)"
    )
    if not yes and input("type YES to place and immediately cancel it: ").strip() != "YES":
        print("test order skipped")
        return
    decision_id = new_ulid()
    cid = client_order_id(decision_id, "S", 1)
    JsonlDecisionLogger(Path(cfg.base.paths.logs) / "decisions.jsonl").write(
        build_record(
            DecisionType.OPERATOR_COMMAND,
            final_reason="operator: smoke test order",
            decision_id=decision_id,
            extra={"command": "smoke_test", "client_order_id": cid},
        )
    )
    guard = SymbolGuard.from_config(cfg, registry, "config")
    account = WallexAccount(http, registry, OrderIdMap(Path(cfg.base.paths.state) / "id_map.sqlite"), guard)
    req = OrderRequest(
        symbol=SYMBOL,
        side=Side.BUY,
        type=OrderType.LIMIT,
        qty=qty,
        price=price,
        client_order_id=cid,
        decision_id=decision_id,
        purpose="smoke_test",
    )
    placed = await s.step("place_order (test)", account.place_order(req))
    try:
        if placed is not None:
            s.save("order_placed", placed.model_dump(mode="json"))
            s.save(
                "order_status",
                await s.step("GET order by client_id (raw)", http.get(ep.order_path(cid), private=True)),
            )
    finally:
        cancelled = await s.step("cancel_order (test)", account.cancel_order(SYMBOL, client_order_id=cid))
        if cancelled is not None:
            s.save("order_cancelled", cancelled.model_dump(mode="json"))
            print(f"test order final status: {cancelled.status.value}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config")
    ap.add_argument(
        "--base-url", default=None, help="override WALLEX_BASE_URL (compare api.wallex.ir / api.wallex.com)"
    )
    ap.add_argument("--save-samples", default=None, metavar="DIR", help="save raw responses (secrets masked)")
    ap.add_argument(
        "--place-test-order", action="store_true", help="place + cancel one tiny far-from-market order"
    )
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
