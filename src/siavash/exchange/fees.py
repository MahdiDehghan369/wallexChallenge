"""Account fees at startup: ``risk.fees`` is raised to the account's real rates, never lowered.

``GET /v1/account/fee`` gives per-market maker / taker rates. For the markets the bot can trade
(universe candidates + ``always_keep`` whose quote is in ``risk.allowed_quotes``) the highest rate
is compared with ``config/risk.yaml > fees``; the effective fee is the larger of the two, so the fee
filter and sizing never assume a cheaper fee than the exchange charges. If the call fails the config
values stay (and an error is logged).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from siavash.config import AppConfig
from siavash.logging import get_logger

log = get_logger(__name__)

FeeTable = dict[str, tuple[Decimal, Decimal]]


def traded_markets(cfg: AppConfig) -> list[str]:
    u = cfg.strategy.universe
    quotes = set(cfg.risk.allowed_quotes)
    return sorted({s for s in [*u.candidates, *u.always_keep] if s.split("/")[-1] in quotes})


def merge_fees(cfg: AppConfig, api: FeeTable) -> tuple[AppConfig, dict[str, Any]]:
    """``cfg`` with ``risk.fees`` = max(config, highest account rate over the traded markets)."""
    fees = cfg.risk.fees
    cfg_maker = fees.maker_pct if fees.maker_pct is not None else fees.taker_pct
    markets = [s for s in traded_markets(cfg) if s in api]
    info: dict[str, Any] = {
        "config": {"maker_pct": fees.maker_pct, "taker_pct": fees.taker_pct},
        "account": {s: {"maker_pct": float(api[s][0]), "taker_pct": float(api[s][1])} for s in markets},
    }
    if not markets:
        log.warning("account_fees_no_traded_markets", api_markets=len(api))
        return cfg, {**info, "effective": info["config"], "source": "config"}
    api_maker = max(float(api[s][0]) for s in markets)
    api_taker = max(float(api[s][1]) for s in markets)
    maker, taker = max(cfg_maker, api_maker), max(fees.taker_pct, api_taker)
    log.info("account_fees", markets=info["account"], api_maker_pct=api_maker, api_taker_pct=api_taker)
    if api_maker != cfg_maker or api_taker != fees.taker_pct:
        log.warning(
            "account_fees_differ",
            config_maker_pct=cfg_maker,
            config_taker_pct=fees.taker_pct,
            api_maker_pct=api_maker,
            api_taker_pct=api_taker,
            effective_maker_pct=maker,
            effective_taker_pct=taker,
        )
    new_fees = fees.model_copy(update={"maker_pct": maker, "taker_pct": taker})
    new_cfg = cfg.model_copy(update={"risk": cfg.risk.model_copy(update={"fees": new_fees})})
    source = "config" if (maker, taker) == (cfg_maker, fees.taker_pct) else "account"
    return new_cfg, {**info, "effective": {"maker_pct": maker, "taker_pct": taker}, "source": source}


async def apply_account_fees(
    cfg: AppConfig, fetch: Callable[[], Awaitable[FeeTable]]
) -> tuple[AppConfig, dict[str, Any]]:
    """Fetch the account fees and :func:`merge_fees`; on any error keep the config values."""
    try:
        api = await fetch()
    except Exception as e:
        log.error("account_fees_unavailable", error=f"{type(e).__name__}: {e}")
        fees = {"maker_pct": cfg.risk.fees.maker_pct, "taker_pct": cfg.risk.fees.taker_pct}
        return cfg, {"config": fees, "effective": fees, "source": "config", "error": type(e).__name__}
    return merge_fees(cfg, api)
