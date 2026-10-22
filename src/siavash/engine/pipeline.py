"""Decision pipeline (T-602, section 4.4).

For every closed 15m candle of a universe market: features (multi-TF + implied deviation + market
+ order book) → every SignalGenerator → for each candidate: fee filter → ML (skipped if disabled)
→ ``p_win < θ`` → EV ≤ 0 → LLM reviewer → RiskManager → position + non-blocking entry.
**Exactly one DecisionRecord per candidate** (accepted or rejected), at most one entry per market
per candle. Exits: one EXIT record + ``Orchestrator.execute_exit``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pandas as pd

from siavash.agents.reviewer import LLMReviewer
from siavash.core.interfaces import FeatureRow, RegimeProvider, Reviewer, Scorer, SignalGenerator
from siavash.core.models import Candidate, MLScore, Position, RegimeAssessment, RiskDecision, TradeReview
from siavash.core.types import ComponentStatus, DecisionType, RejectStage, Side, Timeframe
from siavash.data.implied import implied_features
from siavash.engine.decision_log import build_record
from siavash.engine.exits import ExitDecision
from siavash.features.builder import FeatureParams, build_features
from siavash.features.orderbook import orderbook_features
from siavash.features.regime import RuleRegimeProvider, market_features
from siavash.logging import get_logger
from siavash.risk.endgame import endgame_phase
from siavash.risk.manager import DefaultRiskManager
from siavash.signals.fee_filter import FEE_TOO_HIGH, passes_fee_filter, round_trip_fee_pct
from siavash.signals.registry import build_generators

log = get_logger(__name__)
ONE_ENTRY_PER_CANDLE = "RISK_ONE_ENTRY_PER_CANDLE"
OPERATOR_PAUSE = "RISK_OPERATOR_PAUSE"
TFS = (Timeframe.M15, Timeframe.H1, Timeframe.H4)


class SkippedScorer:
    """ML disabled / no model: every candidate gets ``status=SKIPPED``."""

    def score(self, c: Candidate, features: FeatureRow) -> MLScore:
        return MLScore(status=ComponentStatus.SKIPPED)


class FeatureService:
    """Feature rows for the live buffers, cached per (symbol, last closed 15m candle)."""

    def __init__(
        self, live_data: Any, params: FeatureParams, s3_window: int, btc_symbol: str = "BTC/TMN"
    ) -> None:
        self.live = live_data
        self.params = params
        self.s3_window = s3_window
        self.btc = btc_symbol
        self._frames: dict[str, tuple[Any, pd.DataFrame]] = {}
        self._market: tuple[Any, pd.DataFrame] | None = None

    def _key(self, symbol: str) -> Any:
        buf = self.live.candles(symbol, Timeframe.M15)
        return buf[-1].ts_open if buf else None

    def frame(self, symbol: str) -> pd.DataFrame:
        key = self._key(symbol)
        cached = self._frames.get(symbol)
        if cached is not None and cached[0] == key:
            return cached[1]
        frames: dict[Timeframe | str, pd.DataFrame] = {tf: self.live.candles_df(symbol, tf) for tf in TFS}
        df = build_features(frames, self.params) if not frames[Timeframe.M15].empty else pd.DataFrame()
        self._frames[symbol] = (key, df)
        return df

    def market_row(self, universe: list[str]) -> dict[str, float]:
        syms = list(dict.fromkeys([self.btc, *universe]))
        key = tuple(self._key(s) for s in syms)
        if self._market is None or self._market[0] != key:
            frames = {s: self.frame(s) for s in syms}
            frames = {s: f for s, f in frames.items() if not f.empty}
            mdf = market_features(frames, self.btc) if self.btc in frames else pd.DataFrame()
            self._market = (key, mdf)
        mdf = self._market[1]
        return {k: float(v) for k, v in mdf.iloc[-1].items()} if not mdf.empty else {}

    def implied_row(self, symbol: str, ts: datetime) -> dict[str, float]:
        base, _, quote = symbol.partition("/")
        if quote != "TMN" or base == "USDT":
            return {}
        frames = [self.live.candles_df(s, Timeframe.M15) for s in (symbol, f"{base}/USDT", "USDT/TMN")]
        if any(f.empty for f in frames):
            return {}
        dev = implied_features(frames[0], frames[1], frames[2], window_bars=self.s3_window)
        if dev.empty or dev.index[-1] != pd.Timestamp(ts):
            return {}
        return {k: float(v) for k, v in dev.iloc[-1].items()}

    def row(self, symbol: str, ts: datetime, universe: list[str], book: Any) -> dict[str, float]:
        df = self.frame(symbol)
        if df.empty or df.index[-1] != pd.Timestamp(ts):
            return {}
        out = {k: float(v) for k, v in df.iloc[-1].items()}
        out.update(self.market_row(universe))
        out.update(self.implied_row(symbol, ts))
        out.update(orderbook_features(book))
        return out


def _fmt(x: Any, nd: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


class DecisionPipeline:
    def __init__(
        self,
        orch: Any,
        generators: list[SignalGenerator] | None = None,
        scorer: Scorer | None = None,
        reviewer: Reviewer | None = None,
        regime_provider: RegimeProvider | None = None,
        risk_manager: Any = None,
        features_fn: Callable[[str, datetime, Any], dict[str, float]] | None = None,
        dynamic_universe: bool = True,
    ) -> None:
        a = orch.app
        self.orch = orch
        self.app = a
        self.cfg = a.cfg
        self.generators = generators if generators is not None else build_generators(self.cfg)
        self.scorer = scorer or SkippedScorer()
        self.reviewer = reviewer or LLMReviewer(self.cfg, None)
        self.regime_provider = regime_provider or RuleRegimeProvider(self.cfg)
        self.features = FeatureService(
            a.live_data, FeatureParams.from_config(self.cfg), self.cfg.strategy.s3.z_window_bars
        )
        self.features_fn = features_fn
        self.dynamic_universe = dynamic_universe
        self.risk = risk_manager or DefaultRiskManager(
            self.cfg,
            a.guard,
            a.clock,
            a.cooldowns,
            endgame_phase=lambda now: endgame_phase(now, self.cfg),
            is_stale=lambda s: a.live_data.is_stale(s),
            open_order_sides=self._open_sides,
        )
        self.fee_rt = round_trip_fee_pct(self.cfg)

    def _open_sides(self, symbol: str) -> list[Side]:
        return [
            o.side
            for o in self.app.tracker.tracked.values()
            if o.symbol == symbol and not o.status.is_terminal
        ]

    async def current_regime(self, ts: datetime, row: FeatureRow) -> RegimeAssessment:
        if self.app.regime is not None:
            return self.app.regime
        reg = await RuleRegimeProvider(self.cfg).assess(row, ts)
        self.app.regime = reg
        return reg

    # ------------------------------------------------------------------ records
    def _record(
        self,
        c: Candidate,
        regime: RegimeAssessment,
        ml: MLScore | None,
        review: TradeReview | None,
        risk: RiskDecision | None,
        action: str,
        stage: RejectStage | None,
        code: str | None,
        reason: str,
        position_id: str | None = None,
        decision_id: str | None = None,
    ) -> str:
        rec = build_record(
            DecisionType.ENTRY_CANDIDATE,
            clock=self.app.clock,
            decision_id=decision_id,
            final_reason=reason,
            outcome_action=action,
            symbol=c.symbol,
            strategy=c.strategy,
            regime=regime,
            signal=c,
            ml=ml,
            llm_review=review,
            risk=risk,
            rejected_at=stage,
            reject_code=code,
            position_id=position_id,
            extra={"round_trip_fee_pct": self.fee_rt},
        )
        self.app.decision_logger.write(rec)
        return rec.decision_id

    def _head(self, c: Candidate) -> str:
        cond = c.conditions
        main = {
            "S1_TREND_BREAKOUT": f"breakout {_fmt(cond.get('donchian_high_dist_pct'))}% ADX={_fmt(cond.get('adx_1h'), 1)}",
            "S2_MEAN_REVERSION": f"BB pos={_fmt(cond.get('bb_pos_15m'))} RSI={_fmt(cond.get('rsi_15m'), 1)}",
            "S3_IMPLIED_DEV": f"deviation z={_fmt(cond.get('z_dev'))}",
        }.get(c.strategy.value, "")
        return f"{c.strategy.value} {main}".strip()

    # ------------------------------------------------------------------ main entry point
    async def process_symbol(self, symbol: str, ts: datetime) -> int:
        """Evaluate all candidates for ``symbol`` at candle close ``ts``; returns the number of records."""
        a = self.app
        book = await self.orch.book(symbol)
        row = (
            self.features_fn(symbol, ts, book)
            if self.features_fn
            else self.features.row(symbol, ts, a.universe, book)
        )
        if not row:
            return 0
        regime = await self.current_regime(ts, row)
        candidates: list[Candidate] = []
        for g in self.generators:
            try:
                candidates += g.generate(symbol, ts, row, regime)
            except Exception as e:
                log.exception("signal_failed", strategy=getattr(g, "strategy_id", "?"), error=str(e))
        entered = False
        for c in candidates:
            entered |= await self._evaluate(c, row, regime, book, entered)
        return len(candidates)

    async def _evaluate(
        self, c: Candidate, row: FeatureRow, regime: RegimeAssessment, book: Any, entered: bool
    ) -> bool:
        a = self.app
        head = self._head(c)
        reg_txt = f"regime={regime.regime.value}"
        ok, code = passes_fee_filter(c, self.cfg)
        if not ok:
            self._record(
                c,
                regime,
                None,
                None,
                None,
                "REJECTED",
                RejectStage.FEE_FILTER,
                code or FEE_TOO_HIGH,
                f"{head}; {reg_txt}; rejected: expected move {c.expected_move_pct:.2f}% < {self.cfg.strategy.fee_multiple}×fees {self.fee_rt:.2f}%",
            )
            return False
        try:
            ml = self.scorer.score(c, row)
        except Exception as e:
            log.warning("scorer_failed", error=str(e))
            ml = MLScore(status=ComponentStatus.ERROR)
        ml_txt = (
            "ML skipped"
            if ml.status is not ComponentStatus.OK
            else f"ML p={_fmt(ml.p_win)}≥{_fmt(ml.threshold)}"
        )
        if (
            ml.status is ComponentStatus.OK
            and ml.p_win is not None
            and ml.threshold is not None
            and ml.p_win < ml.threshold
        ):
            self._record(
                c,
                regime,
                ml,
                None,
                None,
                "REJECTED",
                RejectStage.ML,
                "ML_BELOW_THRESHOLD",
                f"{head}; {reg_txt}; rejected by ML: p={ml.p_win:.2f}<{ml.threshold:.2f}",
            )
            return False
        if ml.status is ComponentStatus.OK and ml.ev_pct is not None and ml.ev_pct <= 0:
            self._record(
                c,
                regime,
                ml,
                None,
                None,
                "REJECTED",
                RejectStage.EV,
                "EV_NOT_POSITIVE",
                f"{head}; {reg_txt}; {ml_txt}; rejected: EV {ml.ev_pct:.2f}% ≤ 0",
            )
            return False
        if entered or not self.orch.entries_allowed():
            if self.orch._paused:
                code2, why = OPERATOR_PAUSE, "operator pause"
            elif entered:
                code2, why = ONE_ENTRY_PER_CANDLE, "one entry per market per candle"
            else:
                code2, why = None, "entries blocked (breaker / end-game)"
            if code2 is not None:
                self._record(
                    c,
                    regime,
                    ml,
                    None,
                    None,
                    "REJECTED",
                    RejectStage.RISK,
                    code2,
                    f"{head}; {reg_txt}; {ml_txt}; rejected: {why}",
                )
                return False
        snap = await self.orch.portfolio_snapshot()
        review = await self.reviewer.review(c, row, ml, regime, snap)
        llm_txt = f"LLM {review.verdict} x{review.size_multiplier:.2f}" + (
            "" if review.status is ComponentStatus.OK else f" ({review.status.value})"
        )
        if review.verdict == "REJECT":
            self._record(
                c,
                regime,
                ml,
                review,
                None,
                "REJECTED",
                RejectStage.LLM,
                "LLM_REJECT",
                f"{head}; {reg_txt}; {ml_txt}; {llm_txt}: {'; '.join(review.reasons[:2])}",
            )
            return False
        market = a.registry.get(c.symbol)
        risk = self.risk.evaluate(c, ml, review, regime, snap, market, book)
        if not risk.approved:
            first = risk.checks_failed[0] if risk.checks_failed else "RISK"
            self._record(
                c,
                regime,
                ml,
                review,
                risk,
                "REJECTED",
                RejectStage.RISK,
                first,
                f"{head}; {reg_txt}; {ml_txt}; {llm_txt}; rejected by risk: {', '.join(risk.checks_failed)}",
            )
            return False
        from siavash.core.ids import new_ulid

        did = new_ulid()
        position = await a.positions.create(c, risk, did)
        self._record(
            c,
            regime,
            ml,
            review,
            risk,
            "ORDER_PLACED",
            None,
            None,
            f"{head}; {reg_txt}; {ml_txt}; {llm_txt}; size={risk.size_tmn:,.0f} TMN",
            position_id=position.position_id,
            decision_id=did,
        )

        async def still_valid() -> bool:
            if not self.orch.entries_allowed():
                return False
            b = await self.orch.book(c.symbol)
            return b.best_bid is not None and float(b.best_bid) > c.stop_loss

        self.orch.execute_entry(position, risk.qty, book, still_valid)
        return True

    async def process_exit(self, position: Position, decision: ExitDecision) -> None:
        rec = build_record(
            DecisionType.EXIT,
            clock=self.app.clock,
            final_reason=f"exit {position.symbol} {decision.reason.value}: {decision.detail}",
            outcome_action="EXITED",
            symbol=position.symbol,
            strategy=position.strategy,
            position_id=position.position_id,
            exit_reason=decision.reason,
            regime=self.app.regime,
            extra={"qty_fraction": str(decision.qty_fraction), "ref_price": str(decision.ref_price)},
        )
        self.app.decision_logger.write(rec)
        self.orch.execute_exit(position, decision, rec.decision_id)

    # ------------------------------------------------------------------ scheduled updates (called by the orchestrator)
    async def update_regime(self, ts: datetime) -> RegimeAssessment | None:
        row = self.features.market_row(self.app.universe)
        if not row:
            return None
        return await self.regime_provider.assess(row, ts)

    async def update_universe(self, ts: datetime) -> list[str] | None:
        if not self.dynamic_universe:
            return None
        from siavash.data.universe import select_universe, universe_record

        a = self.app
        await a.registry.refresh_if_stale()
        markets = a.registry.all()
        tickers = {}
        if hasattr(a.market_data, "get_tickers"):
            tickers = await a.market_data.get_tickers()
        else:
            for m in markets:
                try:
                    tickers[m.symbol] = await a.market_data.get_ticker(m.symbol)
                except Exception:
                    continue
        ranked = sorted(
            (s for s in tickers if a.guard.is_tradable(s, Side.BUY)[0]),
            key=lambda s: tickers[s].volume_24h_quote,
            reverse=True,
        )
        books = {}
        for s in ranked[: self.cfg.strategy.universe.top_volume_n]:
            try:
                books[s] = await a.market_data.get_orderbook(s)
            except Exception:
                continue
        open_syms = [p.symbol for p in a.positions.open_positions()]
        snap_eq = a.equity.last.equity_tmn if a.equity.last else None
        selected, report = select_universe(markets, tickers, books, a.guard, open_syms, self.cfg, snap_eq)
        a.decision_logger.write(universe_record(selected, report, a.clock))
        self.orch.set_universe(selected)
        return selected
