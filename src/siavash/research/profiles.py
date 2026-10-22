"""Diagnostic config profiles (``config/profiles/*.yaml``).

A profile describes one strategy variant for the edge diagnostics. It never touches the live
config files: ``strategy_overrides`` are deep-merged over ``config/strategy.yaml`` **in memory**.
``grid`` expands a profile into several variants (cartesian product); every expanded variant counts
towards the "variants tried" total. ``_settings.yaml`` holds the diagnostic run settings (fee grid,
hold-out length, walk-forward windows).
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from siavash.config import AppConfig, StrategyConfig

SignalKind = Literal["rules", "h1_breakout", "trend_follow"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecSettings(_M):
    """``taker``: enter at the next open (+ slippage). ``maker``: resting limit at the signal price."""

    mode: Literal["taker", "maker"] = "taker"
    entry_ttl_bars: int = Field(2, ge=1)  # maker: bars a resting entry order stays open
    entry_offset_pct: float = Field(0.0, ge=0)  # maker: limit = signal price × (1 − offset)


class SymbolSettings(_M):
    """``top_liquid``: keep only the N highest-turnover allowed TMN markets of the dev period."""

    top_liquid: int | None = Field(None, ge=1)


class Profile(_M):
    name: str
    description: str = ""
    kind: SignalKind = "rules"
    strategy_overrides: dict[str, Any] = Field(default_factory=dict)  # kind=rules
    params: dict[str, float] = Field(default_factory=dict)  # kind=h1_breakout / trend_follow
    grid: dict[str, list[float]] = Field(default_factory=dict)  # dotted strategy path or param name
    execution: ExecSettings = ExecSettings()
    symbols: SymbolSettings = SymbolSettings()
    regime_filter: bool = True  # rules: live regime → strategy map; custom: blocked in PANIC


class DiagnosticSettings(_M):
    fee_rt_pct: list[float]  # round-trip fees to test (maker = taker = rt / 2)
    holdout_days: float = Field(gt=0)
    window_days: float = Field(gt=0)  # walk-forward evaluation windows inside the dev period
    min_group_trades: int = Field(ge=1)  # edge breakdown: smaller groups are marked as noise
    selection_min_trades: int = Field(ge=0)  # walk-forward selection: min past trades to pick a variant
    nested_keep_fractions: list[float]  # ML nested selection: keep the top-q of p_raw


class Variant(_M):
    """One expanded profile: grid point applied."""

    key: str  # e.g. "a_h1_breakout[tp_pct=3.0,sl_pct=1.5]"
    family: str  # profile name
    profile: Profile
    point: dict[str, float]
    params: dict[str, float]


def load_settings(root: str | Path = "config/profiles") -> DiagnosticSettings:
    return DiagnosticSettings.model_validate(_read(Path(root) / "_settings.yaml"))


def load_profiles(root: str | Path = "config/profiles") -> list[Profile]:
    """Every ``*.yaml`` except files starting with ``_``, sorted by file name."""
    return [
        Profile.model_validate(_read(p))
        for p in sorted(Path(root).glob("*.yaml"))
        if not p.name.startswith("_")
    ]


def expand(profile: Profile) -> list[Variant]:
    """Cartesian product of ``grid`` (a profile without a grid is one variant)."""
    keys = list(profile.grid)
    points = [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*profile.grid.values())]
    out = []
    for point in points or [{}]:
        params = {**profile.params, **{k: v for k, v in point.items() if "." not in k}}
        label = ",".join(f"{k.rsplit('.', 1)[-1]}={v:g}" for k, v in point.items())
        key = f"{profile.name}[{label}]" if label else profile.name
        out.append(Variant(key=key, family=profile.name, profile=profile, point=point, params=params))
    return out


def variant_config(cfg: AppConfig, v: Variant) -> AppConfig:
    """``cfg`` with the profile overrides and dotted grid values merged into ``strategy`` (in memory)."""
    raw = cfg.strategy.model_dump(mode="json")
    _merge(raw, v.profile.strategy_overrides)
    for path, val in v.point.items():
        if "." in path:
            _set(raw, path, val)
    return cfg.model_copy(update={"strategy": StrategyConfig.model_validate(raw)})


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    return data


def _merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v


def _set(dst: dict[str, Any], path: str, val: Any) -> None:
    *head, last = path.split(".")
    node = dst
    for k in head:
        node = node[k]
    if last not in node:
        raise KeyError(f"unknown strategy parameter {path!r}")
    node[last] = val
