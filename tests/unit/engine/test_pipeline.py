"""T-602: DecisionPipeline — one rejection test per stage + a full accept; one record per candidate."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from siavash.config import load_config
from siavash.core.models import Candidate, DecisionRecord, MLScore, RegimeAssessment, TradeReview
from siavash.core.types import ComponentStatus, Regime, RejectStage, StrategyId, Timeframe
from siavash.engine.clock import SimClock
from siavash.engine.factory import assemble
from siavash.engine.orchestrator import Orchestrator
from siavash.engine.pipeline import ONE_ENTRY_PER_CANDLE, DecisionPipeline
from siavash.engine.store import InMemoryStore
from siavash.exchange import endpoints as ep
from siavash.exchange.guard import SymbolGuard
from siavash.exchange.markets import MarketRegistry
from tests.fakes.broker import FakeBroker
from tests.fakes.candle_book import CandleBookMarketData
from tests.fakes.synthetic import SYN_START, make_synthetic_market, to_candles
from tests.unit.exchange.conftest import DENYLIST, load

CFG = load_config(Path(__file__).resolve().parents[3] / "config", env_file=None)
D = Decimal
DF = make_synthetic_market(seed=3, days=4)["BTC/TMN"]
PX = float(DF["close"].iloc[3 * 96 - 1])  # last closed candle at SYN_START + 3 days


class ListGenerator:
    strategy_id = StrategyId.S3_IMPLIED_DEV

    def __init__(self, cands: list[Candidate]) -> None:
        self.cands = cands

    def generate(self, symbol: str, ts: datetime, features: Any, regime: RegimeAssessment) -> list[Candidate]:
        return [c for c in self.cands if c.symbol == symbol]


class StubScorer:
    def __init__(self, by_id: dict[str, MLScore]) -> None:
        self.by_id = by_id

    def score(self, c: Candidate, features: Any) -> MLScore:
        return self.by_id.get(c.candidate_id, MLScore(status=ComponentStatus.SKIPPED))


class StubReviewer:
    def __init__(self, reject: set[str]) -> None:
        self.reject = reject

    async def review(self, c: Candidate, f: Any, ml: MLScore, r: RegimeAssessment, p: Any) -> TradeReview:
        verdict = "REJECT" if c.candidate_id in self.reject else "APPROVE"
        return TradeReview(status=ComponentStatus.OK, verdict=verdict, size_multiplier=0.8, reasons=["stub"])


async def _setup(
    tmp_path: Path, cands: list[Candidate], ml: dict[str, MLScore], reject: set[str]
) -> tuple[DecisionPipeline, Orchestrator, Any]:
    start = SYN_START + timedelta(days=3, seconds=5)
    clock = SimClock(start)
    md = CandleBookMarketData(
        markets=ep.parse_markets(load("markets")),
        candles={("BTC/TMN", Timeframe.M15): to_candles(DF, "BTC/TMN", "15m")},
        clock=clock,
    )
    reg = MarketRegistry(md.list_markets, clock)
    await reg.load()
    guard = SymbolGuard(DENYLIST, reg)
    broker = FakeBroker({"TMN": D("5000000")}, clock=clock, guard=guard, market_data=md)
    app = assemble(
        CFG,
        "backtest",
        clock,
        md,
        broker,
        reg,
        guard,
        InMemoryStore(),
        tmp_path / "logs",
        tmp_path / "reports",
        universe=["BTC/TMN"],
    )
    app.regime = RegimeAssessment(
        ts=start,
        regime=Regime.RANGE,
        risk_multiplier=0.7,
        enabled_strategies=list(StrategyId),
        reasons=[],
        source="rule",
        rule_regime=Regime.RANGE,
    )
    orch = Orchestrator(app)
    pipe = DecisionPipeline(
        orch,
        [ListGenerator(cands)],
        StubScorer(ml),
        StubReviewer(reject),
        features_fn=lambda s, ts, b: {"x": 1.0},
        dynamic_universe=False,
    )
    orch.pipeline = pipe
    app.live_data.set_symbols(["BTC/TMN"])
    await app.live_data.bootstrap()
    return pipe, orch, app


def cand(cid: str, symbol: str = "BTC/TMN", move: float = 2.0, sl: float = 0.985) -> Candidate:
    px = PX
    return Candidate(
        candidate_id=cid,
        ts=SYN_START,
        symbol=symbol,
        strategy=StrategyId.S3_IMPLIED_DEV,
        entry_ref_price=px,
        stop_loss=px * sl,
        take_profit=px * (1 + move / 100),
        time_stop_minutes=180,
        expected_move_pct=move,
        conditions={"z_dev": -2.5},
    )


def _records(tmp_path: Path) -> list[DecisionRecord]:
    p = tmp_path / "logs" / "decisions.jsonl"
    return [DecisionRecord.model_validate_json(x) for x in p.read_text().splitlines()] if p.exists() else []


async def _cleanup(orch: Orchestrator) -> None:
    for t in list(orch._tasks):
        t.cancel()
    await asyncio.gather(*orch._tasks, return_exceptions=True)


@pytest.mark.parametrize(
    ("cid", "kw", "ml", "reject", "stage", "code"),
    [
        ("fee", {"move": 1.0}, None, False, RejectStage.FEE_FILTER, "FEE_TOO_HIGH"),
        (
            "ml",
            {},
            MLScore(status=ComponentStatus.OK, p_win=0.5, threshold=0.55, ev_pct=0.4),
            False,
            RejectStage.ML,
            "ML_BELOW_THRESHOLD",
        ),
        (
            "ev",
            {},
            MLScore(status=ComponentStatus.OK, p_win=0.6, threshold=0.55, ev_pct=-0.1),
            False,
            RejectStage.EV,
            "EV_NOT_POSITIVE",
        ),
        ("llm", {}, None, True, RejectStage.LLM, "LLM_REJECT"),
        ("risk", {"symbol": "TON/TMN"}, None, False, RejectStage.RISK, "RISK_SYMBOL_NOT_ALLOWED"),
    ],
)
async def test_reject_at_each_stage(
    tmp_path: Path,
    cid: str,
    kw: dict[str, Any],
    ml: MLScore | None,
    reject: bool,
    stage: RejectStage,
    code: str,
) -> None:
    c = cand(cid, **kw)
    pipe, orch, app = await _setup(tmp_path, [c], {cid: ml} if ml else {}, {cid} if reject else set())
    n = await pipe.process_symbol(c.symbol, SYN_START + timedelta(days=3))
    recs = _records(tmp_path)
    assert n == 1 and len(recs) == 1
    assert (
        recs[0].outcome_action == "REJECTED" and recs[0].rejected_at is stage and recs[0].reject_code == code
    )
    assert recs[0].signal == c and recs[0].final_reason.startswith("S3_IMPLIED_DEV deviation z=-2.50")
    assert app.positions.open_positions() == []
    await _cleanup(orch)


async def test_full_accept_and_one_entry_per_candle(tmp_path: Path) -> None:
    ok_ml = MLScore(status=ComponentStatus.OK, p_win=0.62, threshold=0.55, ev_pct=0.5)
    cands = [cand("a"), cand("b"), cand("c", move=0.5)]
    pipe, orch, app = await _setup(tmp_path, cands, {"a": ok_ml, "b": ok_ml}, set())
    n = await pipe.process_symbol("BTC/TMN", SYN_START + timedelta(days=3))
    recs = _records(tmp_path)
    assert n == 3 and len(recs) == 3  # exactly one record per candidate
    first = recs[0]
    assert (
        first.outcome_action == "ORDER_PLACED"
        and first.position_id is not None
        and first.risk is not None
        and first.risk.approved
    )
    assert (
        "ML p=0.62≥0.55" in first.final_reason
        and "LLM APPROVE x0.80" in first.final_reason
        and "size=" in first.final_reason
    )
    assert recs[1].reject_code == ONE_ENTRY_PER_CANDLE
    assert recs[2].rejected_at is RejectStage.FEE_FILTER
    (p,) = app.positions.open_positions()
    assert p.entry_decision_id == first.decision_id
    await SimClock.settle()
    assert app.broker.placed and app.broker.placed[0].decision_id == first.decision_id  # entry order sent
    await _cleanup(orch)


async def test_no_features_no_records(tmp_path: Path) -> None:
    pipe, orch, _ = await _setup(tmp_path, [cand("a")], {}, set())
    pipe.features_fn = lambda s, ts, b: {}
    assert await pipe.process_symbol("BTC/TMN", SYN_START) == 0 and _records(tmp_path) == []
    await _cleanup(orch)
