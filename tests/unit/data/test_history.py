"""T-201: historical downloader (mock market data)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import respx

from siavash.core.types import Timeframe
from siavash.data.history import (
    SOURCE_BINANCE,
    SOURCE_WALLEX,
    default_symbols,
    download,
    load_binance_aux,
    parquet_source,
    raw_path,
    read_parquet,
)
from siavash.exchange import endpoints as ep
from siavash.exchange.client import WallexHttp
from siavash.exchange.market_data import WallexMarketData
from tests.fakes.clock import FakeClock
from tests.fakes.market_data import InMemoryMarketData
from tests.fakes.synthetic import SYN_START, make_synthetic_market, to_candles


class RecordingMD(InMemoryMarketData):
    def __init__(self, *a, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*a, **kw)
        self.ranges: list[tuple[str, datetime, datetime]] = []

    async def get_candles(self, symbol, tf, start, end):  # type: ignore[no-untyped-def,override]
        self.ranges.append((symbol, start, end))
        return await super().get_candles(symbol, tf, start, end)


def _md(days: int = 6) -> RecordingMD:
    m = make_synthetic_market(seed=4, days=days)
    candles = {(s, Timeframe.M15): to_candles(df, s, "15m") for s, df in m.items()}
    return RecordingMD(candles=candles)


async def test_download_writes_and_resumes(tmp_path: Path) -> None:
    md = _md()
    clock = FakeClock(SYN_START + timedelta(days=3, minutes=7))
    out = tmp_path / "raw"
    rows = await download(
        md,
        ["BTC/TMN", "USDT/TMN"],
        [Timeframe.M15],
        days=2,
        out=out,
        clock=clock,
        report_path=tmp_path / "cov.md",
    )
    p = raw_path(out, "BTC/TMN", "15m")
    assert p.name == "BTC_TMN_15m.parquet" and parquet_source(p) == SOURCE_WALLEX
    df = read_parquet(p)
    assert list(df.columns) == ["ts_open", "open", "high", "low", "close", "volume"]
    assert len(df) == 2 * 96  # last closed candle opens at 23:45 of day 3 boundary
    assert df["ts_open"].max() == SYN_START + timedelta(days=3) - timedelta(minutes=15)
    assert rows[0].gap_pct == 0.0
    assert "BTC/TMN" in (tmp_path / "cov.md").read_text(encoding="utf-8")

    # resume one day later: only the missing day is requested
    md.ranges.clear()
    clock.advance(86400)
    await download(md, ["BTC/TMN"], [Timeframe.M15], days=2, out=out, clock=clock, report_path=None)
    (_, start, _) = md.ranges[0]
    assert start == SYN_START + timedelta(days=3)
    df2 = read_parquet(p)
    assert len(df2) == 3 * 96 and df2["ts_open"].is_unique and df2["ts_open"].is_monotonic_increasing


@respx.mock(base_url="https://wallex.test")
async def test_download_through_paginating_client(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        ts = list(range(int(p["from"]), int(p["to"]), 900))
        n = len(ts)
        return httpx.Response(
            200,
            json={
                "s": "ok",
                "t": ts,
                "o": [1] * n,
                "h": [2] * n,
                "l": [0.5] * n,
                "c": [1.5] * n,
                "v": [1] * n,
            },
        )

    route = respx_mock.get(ep.Paths.CANDLES).mock(side_effect=handler)

    async def nosleep(_: float) -> None:
        return None

    http = WallexHttp("https://wallex.test", None, public_rate=(1000, 1000), sleep_fn=nosleep)
    md = WallexMarketData(http, max_bars=600)  # 600 one-minute source bars per page
    clock = FakeClock(SYN_START + timedelta(days=1))
    await download(md, ["BTC/TMN"], [Timeframe.M15], days=1, out=tmp_path, clock=clock, report_path=None)
    assert route.call_count == 3  # 1440 source minutes / 600 per page
    assert len(read_parquet(raw_path(tmp_path, "BTC/TMN", "15m"))) == 96


async def test_failures_are_skipped(tmp_path: Path) -> None:
    md = _md()
    clock = FakeClock(SYN_START + timedelta(days=2))
    rows = await download(
        md, ["NOPE/TMN"], [Timeframe.M15], days=1, out=tmp_path, clock=clock, report_path=None
    )
    assert rows[0].candles == 0


def test_binance_aux(tmp_path: Path) -> None:
    aux = tmp_path / "aux"
    aux.mkdir()
    t0 = int(SYN_START.timestamp() * 1000)
    lines = [
        f"{t0 + i * 900_000},100,101,99,100.5,12,{t0 + i * 900_000 + 899_999},0,0,0,0,0" for i in range(8)
    ]
    (aux / "BTCUSDT-15m-2026-01.csv").write_text("\n".join(lines), encoding="utf-8")
    (aux / "README.txt").write_text("ignored", encoding="utf-8")
    rows = load_binance_aux(aux, tmp_path / "raw" / "aux")
    p = raw_path(tmp_path / "raw" / "aux", "BTC/USDT", "15m")
    assert parquet_source(p) == SOURCE_BINANCE and rows[0].candles == 8
    assert read_parquet(p)["ts_open"].iloc[0] == SYN_START


def test_default_symbols() -> None:
    from siavash.config import load_config

    cfg = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
    syms = default_symbols(cfg)
    assert "USDT/TMN" in syms and "BTC/USDT" in syms and len(syms) == len(set(syms))
