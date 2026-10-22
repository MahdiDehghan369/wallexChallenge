"""T-701: position sizing (+ hypothesis properties)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from siavash.config import AppConfig, load_config
from siavash.exchange import endpoints as ep
from siavash.exchange.markets import MarketRegistry
from siavash.risk.sizing import position_size_tmn, size_to_qty
from tests.unit.exchange.conftest import load

D = Decimal
CFG: AppConfig = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
REG = MarketRegistry.from_markets(ep.parse_markets(load("markets")))
EQ = D("5000000")


def size(**kw: object) -> Decimal:
    args: dict[str, object] = dict(
        equity=EQ,
        entry=D("100"),
        stop=D("98"),
        free_exposure_tmn=EQ,
        cash_tmn=EQ,
        regime_mult=1.0,
        llm_mult=1.0,
        cfg=CFG,
    )
    args.update(kw)
    return position_size_tmn(**args)  # type: ignore[arg-type]


def test_formula() -> None:
    # risk: 0.5 % × 5M / 2 % = 1.25M -> capped at 20 % = 1M
    assert size() == D("1000000")
    # wide stop: 0.5 % × 5M / 5 % = 500k
    assert size(stop=D("95")) == D("500000")
    assert size(regime_mult=0.7, llm_mult=0.5) == D("1000000") * D("0.7") * D("0.5")
    assert size(free_exposure_tmn=D("300000")) == D("300000")
    assert size(cash_tmn=D("100000")) == D("98000")
    assert size(mode="live") == D("500000")  # initial_live_risk_scale 0.5


def test_multipliers_only_reduce() -> None:
    assert size(llm_mult=3.0) == size(llm_mult=1.0)
    assert size(llm_mult=0.0) == D("1000000") * D("0.25")  # clamped to range low end
    assert size(regime_mult=2.0) == size()
    assert size(regime_mult=0.0) == 0  # PANIC


def test_degenerate_inputs() -> None:
    assert size(stop=D("100")) == 0 and size(stop=D("101")) == 0
    assert size(equity=D("0")) == 0 and size(free_exposure_tmn=D("-5")) == 0 and size(cash_tmn=D("-1")) == 0


def test_size_to_qty() -> None:
    q = size_to_qty(D("1000000"), D("7350000000"), "BTC/TMN", REG)
    assert q == D("0.000136")
    assert size_to_qty(D("50000"), D("7350000000"), "BTC/TMN", REG) == 0  # below min notional 100k


@settings(max_examples=400, deadline=None)
@given(
    equity=st.decimals(min_value=D("1000"), max_value=D("1e9"), places=0),
    entry=st.decimals(min_value=D("0.01"), max_value=D("1e10"), places=2),
    sl_frac=st.floats(min_value=0.0005, max_value=0.5),
    free=st.decimals(min_value=D("-1e6"), max_value=D("1e9"), places=0),
    cash=st.decimals(min_value=D("-1e6"), max_value=D("1e9"), places=0),
    rm=st.floats(min_value=-1, max_value=3),
    lm=st.floats(min_value=-1, max_value=3),
    mode=st.sampled_from(["paper", "live", "backtest"]),
)
def test_properties(
    equity: Decimal,
    entry: Decimal,
    sl_frac: float,
    free: Decimal,
    cash: Decimal,
    rm: float,
    lm: float,
    mode: str,
) -> None:
    stop = entry * (1 - D(str(sl_frac)))
    s = position_size_tmn(equity, entry, stop, free, cash, rm, lm, CFG, mode)
    assert s >= 0
    assert s <= equity * D("0.20") + D("1e-9")
    assert s <= max(cash, D(0)) + D("1e-9")
    assert s <= max(free, D(0)) + D("1e-9")


@pytest.mark.parametrize("price", [D("7350000000"), D("102500"), D("260100000")])
def test_qty_never_exceeds_size(price: Decimal) -> None:
    for sym in ("BTC/TMN", "USDT/TMN", "ETH/TMN"):
        q = size_to_qty(D("1000000"), price, sym, REG)
        assert q * price <= D("1000000")
