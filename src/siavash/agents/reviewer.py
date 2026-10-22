"""TradeReviewer agent (T-505, ``Reviewer`` protocol).

Sends a structured summary of one candidate to the LLM (appendix D): candidate, up to 30 key
features (``strategy.reviewer_key_features``), ML score with top SHAP features, regime, portfolio
and the last 10 trades of the same strategy. Output is validated, clamped (size ≤ 1) and logged.
Disabled → deterministic fallback with ``status=SKIPPED``; timeout / error / invalid output →
deterministic fallback with ``status=FALLBACK``.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import Awaitable, Callable
from typing import Any

from siavash.agents.fallback import fallback_review
from siavash.agents.prompts import load_prompt
from siavash.agents.schemas import LLMTradeReviewOut, complete_with_repair, to_trade_review
from siavash.core.interfaces import FeatureRow
from siavash.core.models import Candidate, MLScore, PortfolioSnapshot, RegimeAssessment, TradeReview
from siavash.core.types import ComponentStatus
from siavash.logging import get_logger

log = get_logger(__name__)
RecentFn = Callable[[str, int], Awaitable[list[dict[str, Any]]] | list[dict[str, Any]]]
MAX_FEATURES = 30


def _r(x: Any, nd: int = 4) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else round(x, nd)
    return x


class LLMReviewer:
    def __init__(
        self, cfg: Any, llm: Any = None, tools: Any = None, recent_fn: RecentFn | None = None
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.tools = tools
        self.recent_fn = recent_fn
        self.system, self.prompt_version = load_prompt("reviewer_v1")

    def build_input(
        self,
        c: Candidate,
        features: FeatureRow,
        ml: MLScore,
        regime: RegimeAssessment,
        portfolio: PortfolioSnapshot,
        recent: list[dict[str, Any]],
    ) -> dict[str, Any]:
        keys = list(self.cfg.strategy.reviewer_key_features)[:MAX_FEATURES]
        eq = float(portfolio.equity_tmn) or 1.0
        return {
            "candidate": {
                "symbol": c.symbol,
                "strategy": c.strategy.value,
                "entry": _r(c.entry_ref_price, 6),
                "stop_loss": _r(c.stop_loss, 6),
                "take_profit": _r(c.take_profit, 6),
                "tp1": _r(c.tp1_price, 6) if c.tp1_price else None,
                "expected_move_pct": _r(c.expected_move_pct),
                "sl_pct": _r((1 - c.stop_loss / c.entry_ref_price) * 100),
                "time_stop_minutes": c.time_stop_minutes,
                "conditions": {k: _r(v) for k, v in c.conditions.items()},
            },
            "features": {k: _r(float(features[k])) for k in keys if k in features},
            "regime": {
                "label": regime.regime.value,
                "risk_multiplier": regime.risk_multiplier,
                "source": regime.source,
                "reasons": regime.reasons[:5],
            },
            "ml": {
                "status": ml.status.value,
                "p_win": _r(ml.p_win),
                "threshold": ml.threshold,
                "top_features": [[n, _r(v)] for n, v in ml.top_features[:5]],
            },
            "portfolio": {
                "open_positions": [
                    {"symbol": p.symbol, "strategy": p.strategy.value} for p in portfolio.open_positions
                ],
                "exposure_pct": round(float(portfolio.exposure_tmn) / eq * 100, 2),
                "drawdown_pct": round(portfolio.drawdown_pct, 2),
                "daily_pnl_pct": round(portfolio.daily_pnl_pct, 2),
            },
            "recent_performance": recent[:10],
        }

    async def _recent(self, strategy: str) -> list[dict[str, Any]]:
        if self.recent_fn is None:
            return []
        try:
            res = self.recent_fn(strategy, 10)
            return list(await res if inspect.isawaitable(res) else res)
        except Exception as e:
            log.warning("recent_trades_failed", error=str(e))
            return []

    async def review(
        self,
        c: Candidate,
        features: FeatureRow,
        ml: MLScore,
        regime: RegimeAssessment,
        portfolio: PortfolioSnapshot,
    ) -> TradeReview:
        rcfg = self.cfg.llm.reviewer
        if not rcfg.enabled or self.llm is None or not getattr(self.llm, "enabled", True):
            return fallback_review(ml, self.cfg, ComponentStatus.SKIPPED, why="LLM reviewer disabled")
        payload = self.build_input(c, features, ml, regime, portfolio, await self._recent(c.strategy.value))
        kw: dict[str, Any] = {}
        if rcfg.allow_tools and self.tools is not None:
            kw = {
                "tools": self.tools.openai_tools(),
                "tool_executor": self.tools.execute,
                "max_tool_calls": self.cfg.llm.max_tool_calls,
            }
        try:
            out, meta = await asyncio.wait_for(
                complete_with_repair(
                    self.llm, self.system, payload, LLMTradeReviewOut, self.prompt_version, **kw
                ),
                timeout=rcfg.timeout_s,
            )
            meta.setdefault("prompt_version", self.prompt_version)
            return to_trade_review(out, meta, self.cfg)
        except Exception as e:
            why = "LLM timeout" if isinstance(e, TimeoutError) else f"LLM error: {type(e).__name__}"
            log.warning("reviewer_fallback", symbol=c.symbol, reason=why, error=str(e)[:200])
            fb = fallback_review(ml, self.cfg, ComponentStatus.FALLBACK, why=why)
            return fb.model_copy(update={"prompt_version": self.prompt_version})
