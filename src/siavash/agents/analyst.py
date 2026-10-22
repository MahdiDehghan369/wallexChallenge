"""MarketAnalyst agent (T-504, ``RegimeProvider`` protocol).

1. The deterministic ``RuleRegimeProvider`` runs first.
2. The LLM gets the market features, the rule regime and its reasons, 24h strategy performance and
   up to ``llm.max_tool_calls`` read-only tool calls.
3. Conservative merge: the final regime is the one with the *lower* risk multiplier (ties → rule);
   enabled strategies = intersection; ``source="llm"`` only if the LLM's choice won; ``rule_regime``
   always set. Errors / timeouts → rule regime with ``status=FALLBACK``.
Every assessment goes to ``logs/regime.jsonl`` and a ``REGIME_UPDATE`` decision record.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from siavash.agents.prompts import load_prompt
from siavash.agents.schemas import LLMRegimeOut, complete_with_repair, to_regime
from siavash.core.interfaces import DecisionLogger, FeatureRow
from siavash.core.models import RegimeAssessment
from siavash.core.types import ComponentStatus, DecisionType
from siavash.features.regime import RuleRegimeProvider
from siavash.logging import get_logger

log = get_logger(__name__)
PerfFn = Callable[[int], Awaitable[dict[str, Any]] | dict[str, Any]]


def merge_conservative(rule: RegimeAssessment, llm: RegimeAssessment) -> RegimeAssessment:
    """Lower risk wins; strategies = intersection."""
    winner = llm if llm.risk_multiplier < rule.risk_multiplier else rule
    enabled = [
        s
        for s in winner.enabled_strategies
        if s in set(rule.enabled_strategies) & set(llm.enabled_strategies)
    ]
    reasons = [f"rule: {r}" for r in rule.reasons[:3]] + [f"llm: {r}" for r in llm.reasons[:3]]
    return winner.model_copy(
        update={
            "enabled_strategies": enabled,
            "reasons": reasons,
            "source": "llm" if winner is llm else "rule",
            "rule_regime": rule.rule_regime,
            "status": ComponentStatus.OK,
        }
    )


def needs_update(
    now: datetime,
    last: datetime | None,
    interval_min: float,
    btc_ret_15m: float | None,
    btc_atr_pct: float | None,
) -> bool:
    """Hourly (``analyst_interval_min``) or on a shock: ``|BTC 15m return| ≥ 2 × ATR%``."""
    if last is None or now - last >= timedelta(minutes=interval_min):
        return True
    if btc_ret_15m is None or btc_atr_pct is None or math.isnan(btc_ret_15m) or math.isnan(btc_atr_pct):
        return False
    return abs(btc_ret_15m) >= 2 * btc_atr_pct


class LLMRegimeProvider:
    def __init__(
        self,
        cfg: Any,
        llm: Any = None,
        tools: Any = None,
        perf_fn: PerfFn | None = None,
        decision_logger: DecisionLogger | None = None,
        regime_log: str | Path | None = "logs/regime.jsonl",
        clock: Any = None,
    ) -> None:
        self.cfg = cfg
        self.llm = llm
        self.tools = tools
        self.perf_fn = perf_fn
        self.rule = RuleRegimeProvider(cfg)
        self.decision_logger = decision_logger
        self.regime_log = Path(regime_log) if regime_log else None
        self.clock = clock
        self.system, self.prompt_version = load_prompt("analyst_v1")
        self.last: RegimeAssessment | None = None

    async def _perf(self) -> dict[str, Any]:
        if self.perf_fn is None:
            return {}
        try:
            res = self.perf_fn(24)
            return dict(await res if inspect.isawaitable(res) else res)
        except Exception as e:
            log.warning("strategy_perf_failed", error=str(e))
            return {}

    async def _llm(self, features: FeatureRow, rule: RegimeAssessment, ts: datetime) -> RegimeAssessment:
        payload = {
            "market_features": {
                k: (None if (isinstance(v, float) and math.isnan(v)) else round(float(v), 4))
                for k, v in features.items()
                if k.startswith("x_")
            },
            "rule_regime": {
                "regime": rule.regime.value,
                "risk_multiplier": rule.risk_multiplier,
                "reasons": rule.reasons,
            },
            "strategy_performance_24h": await self._perf(),
        }
        kw: dict[str, Any] = {}
        if self.tools is not None:
            kw = {
                "tools": self.tools.openai_tools(),
                "tool_executor": self.tools.execute,
                "max_tool_calls": self.cfg.llm.max_tool_calls,
            }
        out, _meta = await asyncio.wait_for(
            complete_with_repair(self.llm, self.system, payload, LLMRegimeOut, self.prompt_version, **kw),
            timeout=self.cfg.llm.timeout_s,
        )
        return to_regime(out, rule, self.cfg, ts)

    async def assess(self, market_features: FeatureRow, ts: datetime) -> RegimeAssessment:
        rule = await self.rule.assess(market_features, ts)
        if not self.cfg.llm.analyst.enabled or self.llm is None or not getattr(self.llm, "enabled", True):
            final = rule.model_copy(update={"status": ComponentStatus.SKIPPED})
        else:
            try:
                final = merge_conservative(rule, await self._llm(market_features, rule, ts))
            except Exception as e:
                log.warning("analyst_fallback", error=f"{type(e).__name__}: {str(e)[:200]}")
                final = rule.model_copy(update={"status": ComponentStatus.FALLBACK, "source": "fallback"})
        self._record(final)
        self.last = final
        return final

    def _record(self, r: RegimeAssessment) -> None:
        if self.regime_log is not None:
            self.regime_log.parent.mkdir(parents=True, exist_ok=True)
            with self.regime_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")
        if self.decision_logger is not None:
            from siavash.engine.decision_log import build_record

            self.decision_logger.write(
                build_record(
                    DecisionType.REGIME_UPDATE,
                    clock=self.clock,
                    regime=r,
                    outcome_action="INFO",
                    final_reason=f"regime={r.regime.value} (rule={r.rule_regime.value}, source={r.source}, x{r.risk_multiplier})",
                )
            )
