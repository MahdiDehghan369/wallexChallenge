"""``siavash`` command line (T-1104).

Engine: ``run --mode {backtest,paper,live,replay}`` (live requires env ``CONFIRM_LIVE=YES``).
Operator: ``status``, ``pause``, ``resume [--confirm]``, ``flatten --confirm`` — these only write
flags to the state store that the running engine reads (they are system commands, not manual trades)
and each one is recorded as an ``OPERATOR_COMMAND`` decision.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer

from siavash.config import AppConfig, config_hash, load_config
from siavash.core.types import BreakerState, DecisionType
from siavash.engine.decision_log import JsonlDecisionLogger, build_record
from siavash.engine.store import SqliteStore
from siavash.logging import register_secrets, setup_logging
from siavash.runtime import init_runtime

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Siavash — spot-only Wallex trading agent")
MODES = ("backtest", "paper", "live", "replay")


def _cfg(config_dir: str, mode: str | None = None) -> AppConfig:
    cfg = load_config(config_dir)
    if mode:
        cfg = cfg.model_copy(update={"base": cfg.base.model_copy(update={"mode": mode})})
    init_runtime(mode=cfg.mode, config_hash=config_hash(cfg))
    register_secrets(cfg.secrets.raw_values())
    return cfg


def _store(cfg: AppConfig) -> SqliteStore:
    return SqliteStore(Path(cfg.base.paths.state) / "state.sqlite")


def _operator_record(cfg: AppConfig, command: str, **extra: Any) -> None:
    JsonlDecisionLogger(Path(cfg.base.paths.logs) / "decisions.jsonl").write(
        build_record(
            DecisionType.OPERATOR_COMMAND,
            final_reason=f"operator: {command}",
            extra={"command": command, **extra},
        )
    )


async def _run_engine(cfg: AppConfig, mode: str) -> None:
    from siavash.engine.factory import build_app, make_pipeline
    from siavash.engine.orchestrator import Orchestrator, install_signal_handlers

    application = build_app(cfg, mode)
    orch = Orchestrator(application)
    orch.pipeline = make_pipeline(application, orch, cfg)
    install_signal_handlers(orch)
    await orch.run()


@app.command()
def run(
    mode: str = typer.Option("paper", "--mode", help="backtest | paper | live | replay"),
    date_from: str | None = typer.Option(None, "--from", help="backtest/replay start (YYYY-MM-DD)"),
    date_to: str | None = typer.Option(None, "--to", help="backtest/replay end (YYYY-MM-DD)"),
    config: str = typer.Option("config", "--config"),
) -> None:
    """Run the engine."""
    if mode not in MODES:
        raise typer.BadParameter(f"mode must be one of {MODES}")
    if mode == "live" and os.environ.get("CONFIRM_LIVE") != "YES":
        typer.echo(
            "refusing to start LIVE: set CONFIRM_LIVE=YES (human only, after the go-live checklist)", err=True
        )
        raise typer.Exit(2)
    cfg = _cfg(config, mode)
    setup_logging("INFO", cfg.base.paths.logs)
    if mode in ("backtest", "replay"):
        typer.echo(
            f"use `make backtest` / `siavash replay` for {mode} (see scripts/run_backtest.py)", err=True
        )
        raise typer.Exit(1)
    _operator_record(cfg, "run", mode=mode)
    asyncio.run(_run_engine(cfg, mode))


def _tail_decisions(cfg: AppConfig, n: int = 10) -> list[dict[str, Any]]:
    p = Path(cfg.base.paths.logs) / "decisions.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(x) for x in lines if x.strip()]


@app.command()
def status(
    config: str = typer.Option("config", "--config"), as_json: bool = typer.Option(False, "--json")
) -> None:
    """Breaker, equity, drawdown, open positions and the last 10 decisions."""
    cfg = _cfg(config)

    async def collect() -> dict[str, Any]:
        st = _store(cfg)
        try:
            br = await st.get_breaker()
            eq = await st.equity_series()
            positions = [
                p
                for p in await st.load_positions()
                if p.status.value in ("PENDING_ENTRY", "OPEN", "PENDING_EXIT")
            ]
            peak_raw = await st.kv_get("peak_equity")
            last_eq = eq[-1][1] if eq else None
            drawdown = None
            if peak_raw and last_eq is not None and Decimal(str(peak_raw)) > 0:
                peak = Decimal(str(peak_raw))
                drawdown = round(float((peak - last_eq) / peak * 100), 3)
            return {
                "breaker": {"state": br[0].value, "reason": br[1], "since": br[2].isoformat()}
                if br
                else {"state": BreakerState.NORMAL.value},
                "paused": bool(await st.kv_get("operator_pause", False)),
                "flatten_requested": bool(await st.kv_get("operator_flatten", False)),
                "equity_tmn": str(last_eq) if last_eq is not None else None,
                "peak_equity_tmn": peak_raw,
                "drawdown_pct": drawdown,
                "open_positions": [
                    {
                        "symbol": p.symbol,
                        "strategy": p.strategy.value,
                        "status": p.status.value,
                        "qty": str(p.qty),
                        "avg_entry": str(p.avg_entry),
                        "sl": str(p.stop_loss),
                        "tp": str(p.take_profit),
                    }
                    for p in positions
                ],
                "last_decisions": [
                    {
                        "ts": d["ts"],
                        "type": d["type"],
                        "symbol": d.get("symbol"),
                        "action": d["outcome_action"],
                        "reason": d["final_reason"][:140],
                    }
                    for d in _tail_decisions(cfg)
                ],
            }
        finally:
            await st.close()

    info = asyncio.run(collect())
    if as_json:
        typer.echo(json.dumps(info, ensure_ascii=False, indent=2))
        return
    typer.echo(f"breaker: {info['breaker']['state']} {info['breaker'].get('reason', '')}")
    typer.echo(f"paused: {info['paused']}  flatten requested: {info['flatten_requested']}")
    typer.echo(
        f"equity: {info['equity_tmn']} TMN  peak: {info['peak_equity_tmn']}  drawdown: {info['drawdown_pct']}%"
    )
    typer.echo(f"open positions ({len(info['open_positions'])}):")
    for p in info["open_positions"]:
        typer.echo(
            f"  {p['symbol']} {p['strategy']} {p['status']} qty={p['qty']} entry={p['avg_entry']} SL={p['sl']} TP={p['tp']}"
        )
    typer.echo("last decisions:")
    for d in info["last_decisions"]:
        typer.echo(f"  {d['ts']} {d['type']} {d['symbol'] or ''} {d['action']}: {d['reason']}")


@app.command()
def pause(config: str = typer.Option("config", "--config")) -> None:
    """Stop opening new positions (exits keep running)."""
    cfg = _cfg(config)

    async def go() -> None:
        st = _store(cfg)
        await st.kv_set("operator_pause", True)
        await st.close()

    asyncio.run(go())
    _operator_record(cfg, "pause")
    typer.echo("paused: no new entries")


@app.command()
def resume(
    confirm: bool = typer.Option(False, "--confirm", help="also clear a kill-switch halt"),
    config: str = typer.Option("config", "--config"),
) -> None:
    """Allow entries again; with ``--confirm`` also clear a FLATTEN_AND_HALT kill-switch."""
    cfg = _cfg(config)

    async def go() -> None:
        st = _store(cfg)
        await st.kv_set("operator_pause", False)
        if confirm:
            await st.kv_set("breaker_halted", False)
            br = await st.get_breaker()
            if br and br[0] is BreakerState.FLATTEN_AND_HALT:
                await st.set_breaker(BreakerState.NORMAL, "operator resume --confirm", datetime.now(UTC))
        await st.close()

    asyncio.run(go())
    _operator_record(cfg, "resume", confirm=confirm)
    typer.echo("resumed" + (" (kill-switch cleared)" if confirm else ""))


@app.command()
def flatten(
    confirm: bool = typer.Option(False, "--confirm"), config: str = typer.Option("config", "--config")
) -> None:
    """Ask the running engine to exit every position (reason OPERATOR)."""
    if not confirm:
        typer.echo("flatten requires --confirm", err=True)
        raise typer.Exit(2)
    cfg = _cfg(config)

    async def go() -> None:
        st = _store(cfg)
        await st.kv_set("operator_flatten", True)
        await st.close()

    asyncio.run(go())
    _operator_record(cfg, "flatten")
    typer.echo("flatten requested; the engine exits all positions on its next main-loop tick")


def _not_yet(task: str) -> None:
    typer.echo(f"not implemented: {task}", err=True)
    raise typer.Exit(1)


@app.command()
def report(
    mode: str = typer.Option("live", "--mode"),
    logs: str = typer.Option("logs", "--logs"),
    out: str = typer.Option("reports/live", "--out"),
    live_data: str = typer.Option("data/live", "--live-data", help="candles for the AI counterfactual"),
    history: str = typer.Option("data/processed", "--history"),
) -> None:
    """Judge-metrics report (REPORT.md, metrics.json, charts) from a logs directory."""
    from siavash.backtest.market_data import processed_frames
    from siavash.reporting.consistency import load_live_frames, merge_frames
    from siavash.reporting.judge_report import build_judge_report

    cfg = load_config("config")
    frames = merge_frames(
        processed_frames(history) if Path(history).exists() else {},
        load_live_frames(live_data) if Path(live_data).exists() else {},
    )
    payload = build_judge_report(logs, out, cfg.base.timezone, mode, frames or None, cfg)
    typer.echo(json.dumps(payload["judge"], indent=2, default=str))
    typer.echo(f"report: {Path(out) / 'REPORT.md'}")


@app.command()
def replay(
    date_from: str | None = typer.Option(None, "--from"),
    date_to: str | None = typer.Option(None, "--to"),
    logs: str = typer.Option("logs", "--logs"),
) -> None:
    """Replay recorded data + LLM cache and compare decisions."""
    _not_yet("T-1305")


@app.command()
def reflect(date: str = typer.Option(..., "--date")) -> None:
    """Daily reflection report (LLM)."""
    _not_yet("T-506")


@app.command("export-submission")
def export_submission() -> None:
    """Package logs and reports for the judges."""
    _not_yet("T-1303")


async def _llm_check(cfg: AppConfig) -> dict[str, Any]:
    from siavash.agents.budget import LLMBudget
    from siavash.agents.llm import LLMClient

    store = _store(cfg)
    try:
        client = LLMClient.from_config(cfg, budget=LLMBudget.from_config(cfg, store))
        try:
            return await client.probe()
        finally:
            await client.aclose()
    finally:
        await store.close()


@app.command("llm-check")
def llm_check(config: str = typer.Option("config", "--config")) -> None:
    """Probe the LLM gateway (tools + JSON mode, 2 tiny requests) and save state/llm_capabilities.json."""
    from siavash.agents.llm import LLMError, capabilities_path

    cfg = _cfg(config)
    setup_logging("WARNING", cfg.base.paths.logs)
    if not cfg.secrets.llm_api_key or not cfg.llm.model:
        typer.echo("LLM is not configured: set LLM_API_KEY (and optionally LLM_MODEL) in .env", err=True)
        raise typer.Exit(2)
    try:
        caps = asyncio.run(_llm_check(cfg))
    except LLMError as e:
        typer.echo(f"llm-check failed: {e}", err=True)
        raise typer.Exit(1) from e
    path = capabilities_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(caps, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(json.dumps(caps, indent=2, ensure_ascii=False))
    typer.echo(f"saved {path}")


if __name__ == "__main__":
    app()
