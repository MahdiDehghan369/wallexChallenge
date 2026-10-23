"""Read-only data access for the dashboard.

Sources (nothing is ever written): ``logs/decisions.jsonl``, ``logs/regime.jsonl``,
``logs/llm/calls.jsonl``, ``logs/latency.jsonl``, ``logs/system.log*``, ``logs/fills.csv``,
``logs/equity.csv``, ``state/state.sqlite`` (opened with ``mode=ro``), ``state/heartbeat``,
``state/engine.json`` (the engine's mode / git_sha / version_tag / fees / endgame config),
``data/live/*/books.jsonl`` (last best bid for unrealized PnL) and markdown files under ``reports/``.

Robustness:
- Missing files give empty sections, never errors.
- Numbers from logs / the store go through :func:`to_int` / :func:`to_float` here, at load time:
  strings are parsed, null / missing take the default, malformed values take the default and log
  one ``dashboard_bad_number`` warning per field. Views never cast.
- Every page is built from sections; :meth:`DashboardData.section` turns a failing loader into an
  ``{"unavailable": True, ...}`` marker that the template renders as an inline box.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from siavash.config import AppConfig, EndgameConfig
from siavash.logging import get_logger
from siavash.reporting import latency
from siavash.reporting.equity import trading_day_start
from siavash.reporting.judge_report import OFFICIAL_CAPITAL_TMN, api_stats
from siavash.reporting.metrics import build_trades, load_equity, load_fills
from siavash.risk.endgame import phase_at, phase_starts
from siavash.runtime import get_runtime

log = get_logger(__name__)

OPEN_STATUSES = ("PENDING_ENTRY", "OPEN", "PENDING_EXIT")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
REPORT_GROUPS = ("live", "shadow", "backtest", "diagnostics", "research_v2")
BUDGET_COUNTERS = ("hour_calls", "day_calls", "day_tokens")
CF_INT_FIELDS = ("rejected", "correct_rejections", "missed_opportunities")
CF_FLOAT_FIELDS = ("avoided_loss_pct", "missed_profit_pct", "net_value_pct")
DECISION_COLUMNS = ("decision_id", "type", "position_id", "regime", "exit_reason")

_warned_fields: set[str] = set()


# ---------------------------------------------------------------------- coercion
def _bad_number(field: str, value: Any) -> None:
    if field not in _warned_fields:  # once per field per process: pages refresh every 30 s
        _warned_fields.add(field)
        log.warning("dashboard_bad_number", field=field, value=repr(value)[:80])


def to_float(value: Any, field: str, default: float | None = None) -> float | None:
    """``value`` as a finite float; null / missing / "" -> ``default``; malformed -> ``default`` + warning."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        _bad_number(field, value)
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        _bad_number(field, value)
        return default
    if not math.isfinite(f):
        _bad_number(field, value)
        return default
    return f


def to_int(value: Any, field: str, default: int = 0) -> int:
    """Counter as int (``"12"`` -> 12, ``"12.0"`` -> 12); null / missing / malformed -> ``default``."""
    f = to_float(value, field)
    return default if f is None else int(f)


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def unavailable(name: str, exc: BaseException) -> dict[str, Any]:
    """Marker for a section whose loader failed (rendered as an inline "data unavailable" box)."""
    return {"unavailable": True, "section": name, "error": type(exc).__name__}


# ---------------------------------------------------------------------- files
def tail_lines(path: Path, max_bytes: int) -> list[str]:
    """Complete lines of the last ``max_bytes`` of a text file (all of it when smaller)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()  # drop the partial first line
        raw = fh.read()
    return [x for x in raw.decode("utf-8", errors="replace").splitlines() if x.strip()]


def read_jsonl(path: Path, max_bytes: int) -> list[dict[str, Any]]:
    out = []
    for line in tail_lines(path, max_bytes):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def open_ro(db: Path) -> sqlite3.Connection:
    """SQLite connection that cannot write (``mode=ro`` URI)."""
    return sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def _thin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if len(df) <= n:
        return df
    step = -(-len(df) // n)
    return pd.concat([df.iloc[::step], df.iloc[[-1]]]).drop_duplicates(subset="ts")


def _ts(value: Any, n: int) -> str:
    return str(value or "")[:n].replace("T", " ")


class DashboardData:
    def __init__(self, cfg: AppConfig) -> None:
        p = cfg.base.paths
        self.cfg = cfg
        self.d = cfg.base.dashboard
        self.logs, self.state, self.data, self.reports = (
            Path(p.logs),
            Path(p.state),
            Path(p.data),
            Path(p.reports),
        )
        self._api_cache: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    def section(self, name: str, fn: Callable[[], Any]) -> Any:
        """``fn()``, or an :func:`unavailable` marker (and a warning) when it raises."""
        try:
            return fn()
        except Exception as e:
            log.warning("dashboard_section_failed", section=name, error=f"{type(e).__name__}: {e}")
            return unavailable(name, e)

    # ------------------------------------------------------------------ raw sources
    def decisions(self) -> list[dict[str, Any]]:
        return read_jsonl(self.logs / "decisions.jsonl", self.d.tail_bytes)

    def candidates(self) -> list[dict[str, Any]]:
        return [d for d in self.decisions() if d.get("type") == "ENTRY_CANDIDATE"]

    def llm_calls(self) -> list[dict[str, Any]]:
        """``calls.jsonl`` rows with ``prompt_tokens`` / ``completion_tokens`` coerced to int."""
        rows = read_jsonl(self.logs / "llm" / "calls.jsonl", self.d.tail_bytes)
        for c in rows:
            for k in ("prompt_tokens", "completion_tokens"):
                c[k] = to_int(c.get(k), f"calls.{k}")
        return rows

    def store(self) -> dict[str, Any]:
        """Open positions, breaker and kv values from ``state.sqlite`` (read-only)."""
        db = self.state / "state.sqlite"
        out: dict[str, Any] = {"positions": [], "breaker": None, "kv": {}, "error": None}
        if not db.exists():
            out["error"] = "state.sqlite not found"
            return out
        try:
            con = open_ro(db)
            try:
                for d, s in con.execute("SELECT data, status FROM positions").fetchall():
                    try:
                        pos = json.loads(d)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if s in OPEN_STATUSES and isinstance(pos, dict):
                        out["positions"].append(pos)
                br = con.execute("SELECT state, reason, since FROM breaker WHERE id = 1").fetchone()
                out["breaker"] = {"state": br[0], "reason": br[1], "since": br[2]} if br else None
                for k, v in con.execute("SELECT k, v FROM kv").fetchall():
                    try:
                        out["kv"][k] = json.loads(v)
                    except (TypeError, json.JSONDecodeError):
                        out["kv"][k] = v
            finally:
                con.close()
        except sqlite3.Error as e:
            out["error"] = f"state.sqlite: {e}"
        return out

    def llm_budget_used(self) -> dict[str, Any]:
        """``kv.llm_budget`` with the counters coerced to int (0 when missing or malformed)."""
        raw = as_dict(self.store()["kv"].get("llm_budget"))
        used: dict[str, Any] = {k: to_int(raw.get(k), f"llm_budget.{k}") for k in BUDGET_COUNTERS}
        used["hour"], used["day"] = raw.get("hour"), raw.get("day")
        return used

    def last_regime(self) -> dict[str, Any] | None:
        recs = read_jsonl(self.logs / "regime.jsonl", 200_000)
        return self._regime_row(recs[-1]) if recs else None

    @staticmethod
    def _regime_row(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "ts": _ts(r.get("ts"), 16),
            "regime": r.get("regime"),
            "rule_regime": r.get("rule_regime"),
            "source": r.get("source"),
            "status": r.get("status"),
            "risk_multiplier": to_float(r.get("risk_multiplier"), "regime.risk_multiplier"),
            "reasons": "؛ ".join(str(x) for x in as_list(r.get("reasons"))[:3]),
        }

    def last_prices(self) -> dict[str, float]:
        """Last best bid per symbol from the newest recorded ``data/live/<day>/books.jsonl``."""
        days = sorted(p for p in (self.data / "live").glob("*/books.jsonl"))
        prices: dict[str, float] = {}
        for rec in read_jsonl(days[-1], 2_000_000) if days else []:
            bids = as_list(rec.get("bids"))
            sym = rec.get("symbol")
            if isinstance(sym, str) and bids:
                bid = to_float(as_dict(bids[0]).get("price"), "books.bid")
                if bid is not None:
                    prices[sym] = bid
        return prices

    def heartbeat_age_s(self) -> float | None:
        try:
            return round(time.time() - (self.state / "heartbeat").stat().st_mtime, 1)
        except OSError:
            return None

    def health(self) -> tuple[bool, dict[str, Any]]:
        age = self.heartbeat_age_s()
        ok = age is not None and age <= self.cfg.base.loop.watchdog_timeout_s
        return ok, {"status": "ok" if ok else "stale", "heartbeat_age_s": age}

    def trades(self) -> pd.DataFrame:
        """Closed round trips from ``fills.csv``; exit reasons / regimes from the (tolerant) decision tail."""
        recs = [
            {**{k: d.get(k) for k in DECISION_COLUMNS}, "regime": as_dict(d.get("regime")).get("regime")}
            for d in self.decisions()
        ]
        decisions = pd.DataFrame(recs, columns=list(DECISION_COLUMNS))
        return build_trades(load_fills(self.logs / "fills.csv"), decisions)

    # ------------------------------------------------------------------ overview
    def overview(self) -> dict[str, Any]:
        return {
            "equity": self.section("equity", self._equity),
            "status": self.section("status", self._status),
            "activity": self.section("activity", self._activity),
            "regime": self.section("regime", self.last_regime),
            "endgame": self.section("endgame", self.endgame),
        }

    def _equity(self) -> dict[str, Any]:
        eq = load_equity(self.logs / "equity.csv")
        if not eq.empty:
            eq = eq.dropna(subset=["equity_tmn"]).reset_index(drop=True)
        last = eq.iloc[-1] if not eq.empty else None
        equity = to_float(last["equity_tmn"], "equity.equity_tmn") if last is not None else None
        dd = pd.Series(dtype=float)
        if not eq.empty:
            peak = eq["equity_tmn"].cummax()
            dd = (peak - eq["equity_tmn"]) / peak * 100
        chart = _thin(eq.assign(dd=dd), self.d.max_chart_points) if not eq.empty else eq
        return {
            "equity_tmn": equity,
            "pnl_pct": (equity - OFFICIAL_CAPITAL_TMN) / OFFICIAL_CAPITAL_TMN * 100
            if equity is not None
            else None,
            "capital_base": OFFICIAL_CAPITAL_TMN,
            "drawdown_pct": to_float(dd.iloc[-1], "equity.drawdown") if len(dd) else None,
            "max_drawdown_pct": to_float(dd.max(), "equity.drawdown") if len(dd) else None,
            "daily_pnl_pct": to_float(last.get("daily_pnl_pct"), "equity.daily_pnl_pct")
            if last is not None
            else None,
            "exposure_tmn": to_float(last.get("exposure_tmn"), "equity.exposure_tmn")
            if last is not None
            else None,
            "equity_ts": str(last["ts"])[:19] if last is not None else None,
            "chart": {
                "ts": [t.strftime("%Y-%m-%d %H:%M") for t in chart["ts"]] if not eq.empty else [],
                "equity": [round(float(v), 0) for v in chart["equity_tmn"]] if not eq.empty else [],
                "dd": [round(-(to_float(v, "equity.drawdown") or 0.0), 3) for v in chart["dd"]]
                if not eq.empty
                else [],
            },
        }

    def _status(self) -> dict[str, Any]:
        st = self.store()
        return {
            "breaker": st["breaker"] or {"state": "NORMAL", "reason": "", "since": None},
            "paused": bool(st["kv"].get("operator_pause", False)),  # same truthiness as the engine
            "store_error": st["error"],
            "open_positions": len(st["positions"]),
        }

    def _activity(self) -> dict[str, int]:
        day0 = trading_day_start(datetime.now(UTC), self.cfg.base.timezone)
        fills = load_fills(self.logs / "fills.csv")
        trades = self.trades()
        if not fills.empty:
            fills = fills.dropna(subset=["ts"])
        return {
            "fills_today": int((fills["ts"] >= day0).sum()) if not fills.empty else 0,
            "closed_today": int((pd.to_datetime(trades["exit_ts"], utc=True) >= day0).sum())
            if not trades.empty
            else 0,
            "entries_today": int(
                fills[(fills["ts"] >= day0) & (fills["side"] == "buy")]["position_id"].nunique()
            )
            if not fills.empty
            else 0,
        }

    # ------------------------------------------------------------------ opportunities
    def opportunities(self, symbol: str = "", strategy: str = "", outcome: str = "") -> dict[str, Any]:
        cands = self.candidates()
        options = {
            "symbols": sorted({str(d["symbol"]) for d in cands if d.get("symbol")}),
            "strategies": sorted({str(d["strategy"]) for d in cands if d.get("strategy")}),
            "outcomes": sorted({str(d["outcome_action"]) for d in cands if d.get("outcome_action")}),
        }
        sel = [
            d
            for d in cands
            if (not symbol or d.get("symbol") == symbol)
            and (not strategy or d.get("strategy") == strategy)
            and (not outcome or d.get("outcome_action") == outcome)
        ]
        rows = []
        for d in reversed(sel[-self.d.max_decisions :]):
            sig, ml, llm, risk = (as_dict(d.get(k)) for k in ("signal", "ml", "llm_review", "risk"))
            conds = as_dict(sig.get("conditions"))
            rows.append(
                {
                    "decision_id": d.get("decision_id"),
                    "ts": _ts(d.get("ts"), 19),
                    "symbol": d.get("symbol"),
                    "strategy": d.get("strategy"),
                    "conditions": ", ".join(f"{k}={_fmt_cond(v)}" for k, v in list(conds.items())[:4]),
                    "p_win": to_float(ml.get("p_win"), "ml.p_win"),
                    "threshold": to_float(ml.get("threshold"), "ml.threshold"),
                    "ml_status": ml.get("status"),
                    "llm_status": llm.get("status"),
                    "llm_verdict": llm.get("verdict"),
                    "llm_reasons": "؛ ".join(str(r) for r in as_list(llm.get("reasons"))[:3]),
                    "risk": None if not risk else ("OK" if risk.get("approved") is True else "REJECT"),
                    "risk_checks": ", ".join(str(c) for c in as_list(risk.get("checks_failed"))),
                    "outcome": d.get("outcome_action"),
                    "rejected_at": d.get("rejected_at"),
                    "reject_code": d.get("reject_code"),
                    "final_reason": d.get("final_reason"),
                }
            )
        return {"rows": rows, "total": len(sel), "options": options}

    def decision(self, decision_id: str) -> dict[str, Any] | None:
        for d in reversed(self.decisions()):
            if d.get("decision_id") == decision_id:
                return d
        return None

    # ------------------------------------------------------------------ positions
    def positions(self) -> dict[str, Any]:
        return {
            "open": self.section("open_positions", self._open_positions),
            "closed": self.section("closed_trades", self._closed_trades),
        }

    def _open_positions(self) -> dict[str, Any]:
        st = self.store()
        prices = self.last_prices()
        rows = []
        for p in st["positions"]:
            entry = to_float(p.get("avg_entry"), "position.avg_entry")
            qty = to_float(p.get("qty"), "position.qty", 0.0) or 0.0
            sym = p.get("symbol")
            px = prices.get(sym) if isinstance(sym, str) else None
            rows.append(
                {
                    **{k: p.get(k) for k in ("symbol", "strategy", "status")},
                    "qty": qty,
                    "entry": entry,
                    "sl": to_float(p.get("stop_loss"), "position.stop_loss"),
                    "tp": to_float(p.get("take_profit"), "position.take_profit"),
                    "trailing": to_float(p.get("trailing_stop"), "position.trailing_stop"),
                    "time_stop": _ts(p.get("time_stop_at"), 16),
                    "opened": _ts(p.get("opened_at"), 16),
                    "price": px,
                    "upnl_tmn": (px - entry) * qty if px is not None and entry is not None else None,
                    "upnl_pct": (px / entry - 1) * 100 if px is not None and entry else None,
                }
            )
        return {"rows": rows, "store_error": st["error"], "prices_known": bool(prices)}

    def _closed_trades(self) -> dict[str, Any]:
        trades = self.trades()
        rows = [
            {
                "symbol": t.symbol,
                "strategy": t.strategy,
                "entry_ts": str(t.entry_ts)[:16],
                "exit_ts": str(t.exit_ts)[:16],
                **{
                    k: to_float(getattr(t, k), f"trades.{k}", 0.0)
                    for k in ("buy_tmn", "sell_tmn", "fees_tmn", "pnl_tmn", "ret_pct")
                },
                "exit_reason": t.exit_reason,
            }
            for t in trades.iloc[::-1].itertuples(index=False)
        ]
        return {
            "rows": rows,
            "realized_tmn": sum(r["pnl_tmn"] or 0.0 for r in rows),
            "fees_tmn": sum(r["fees_tmn"] or 0.0 for r in rows),
        }

    # ------------------------------------------------------------------ AI
    def ai(self) -> dict[str, Any]:
        return {
            "calls": self.section("llm_calls", self._ai_calls),
            "reviews": self.section("llm_reviews", self._ai_reviews),
            "budget": self.section("llm_budget", self._ai_budget),
            "regimes": self.section("regimes", self._ai_regimes),
            "counterfactual": self.section("counterfactual", self.counterfactual),
        }

    def _ai_calls(self) -> dict[str, Any]:
        calls = self.llm_calls()
        return {
            "status": dict(Counter(str(c.get("status")) for c in calls).most_common()),
            "n": len(calls),
            "tokens": sum(c["prompt_tokens"] + c["completion_tokens"] for c in calls),
            "max_input_tokens_per_call": self.cfg.llm.budget.max_input_tokens_per_call,
        }

    def _ai_reviews(self) -> dict[str, Any]:
        reviews = [r for r in (as_dict(d.get("llm_review")) for d in self.candidates()) if r]
        ok = [r for r in reviews if r.get("status") == "ok"]
        approve = sum(1 for r in ok if r.get("verdict") == "APPROVE")
        return {
            "status": dict(Counter(str(r.get("status")) for r in reviews).most_common()),
            "n_ok": len(ok),
            "approve_pct": approve / len(ok) * 100 if ok else None,
            "reject_pct": (len(ok) - approve) / len(ok) * 100 if ok else None,
        }

    def _ai_budget(self) -> list[dict[str, Any]]:
        b = self.cfg.llm.budget
        used = self.llm_budget_used()
        rows = [
            ("calls this hour", used["hour_calls"], b.max_calls_per_hour, used["hour"]),
            ("calls today", used["day_calls"], b.max_calls_per_day, used["day"]),
            ("tokens today", used["day_tokens"], b.max_tokens_per_day, used["day"]),
        ]
        return [
            {"name": n, "used": u, "limit": lim, "pct": u / lim * 100 if lim else None, "period": per}
            for n, u, lim, per in rows
        ]

    def _ai_regimes(self) -> dict[str, Any]:
        regimes = read_jsonl(self.logs / "regime.jsonl", 2_000_000)[-self.d.regime_history :]
        return {
            "rows": [self._regime_row(r) for r in reversed(regimes)],
            "sources": dict(Counter(str(r.get("source")) for r in regimes).most_common()),
        }

    def counterfactual(self) -> dict[str, Any] | None:
        for sub in ("live", "shadow"):
            p = self.reports / sub / "metrics.json"
            try:
                cf = as_dict(json.loads(p.read_text(encoding="utf-8"))).get("counterfactual")
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(cf, dict) and cf:
                gates = {
                    str(g): {
                        **{k: to_int(as_dict(v).get(k), f"counterfactual.{k}") for k in CF_INT_FIELDS},
                        **{k: to_float(as_dict(v).get(k), f"counterfactual.{k}") for k in CF_FLOAT_FIELDS},
                    }
                    for g, v in cf.items()
                }
                return {"source": f"reports/{sub}/metrics.json", "gates": gates}
        return None

    # ------------------------------------------------------------------ system
    def system(self) -> dict[str, Any]:
        return {
            "health": self.section("health", self._sys_health),
            "identity": self.section("identity", self._sys_identity),
            "endgame": self.section("endgame", self.endgame),
            "api": self.section("api", self._api),
            "latency": self.section("latency", self._latency),
        }

    def _sys_health(self) -> dict[str, Any]:
        return {
            "heartbeat_age_s": self.heartbeat_age_s(),
            "watchdog_timeout_s": self.cfg.base.loop.watchdog_timeout_s,
        }

    def engine_identity(self) -> dict[str, Any]:
        """``state/engine.json`` written by the running engine at startup ({} when missing / unreadable)."""
        try:
            return as_dict(json.loads((self.state / "engine.json").read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return {}

    def engine_mode(self) -> str:
        """The engine's real mode (``engine.json``), else this process's runtime mode."""
        mode = self.engine_identity().get("mode")
        return mode if isinstance(mode, str) and mode else get_runtime().mode

    def _sys_identity(self) -> dict[str, Any]:
        decs = read_jsonl(self.logs / "decisions.jsonl", 200_000)
        last = decs[-1] if decs else None
        rt = get_runtime()
        process = {
            "mode": rt.mode,
            "git_sha": rt.git_sha,
            "version_tag": rt.version_tag,
            "config_hash": rt.config_hash,
        }
        eng = self.engine_identity()
        engine = {k: eng.get(k) for k in (*process, "started_at", "pid")} if eng else None
        record = {k: last.get(k) for k in process} if last else None
        fees = as_dict(eng.get("fees"))
        return {
            "record": record,
            "engine": engine,
            "process": process,
            "fees": {
                "source": fees.get("source"),
                "effective": as_dict(fees.get("effective")),
                "config": as_dict(fees.get("config")),
                "error": fees.get("error"),
            }
            if fees
            else None,
            "last_decision_ts": _ts((last or {}).get("ts"), 19) or None,
        }

    def endgame(self) -> dict[str, Any]:
        """Endgame phase and its start times (UTC + Tehran) from the engine's config (else this config)."""
        raw = as_dict(self.engine_identity().get("endgame"))
        eg = EndgameConfig.model_validate(raw) if raw else self.cfg.risk.endgame
        source = "engine.json" if raw else "config"
        now = datetime.now(UTC)
        starts = phase_starts(eg)
        if not starts:
            return {"configured": False, "phase": "NONE", "source": source, "rows": []}
        tz = ZoneInfo(self.cfg.base.timezone)
        current = phase_at(now, eg)
        rows = []
        for name, ts in starts.items():
            rows.append(
                {
                    "phase": name,
                    "utc": ts.strftime("%Y-%m-%d %H:%M"),
                    "tehran": ts.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
                    "state": "current" if name == current else ("past" if ts <= now else "future"),
                    "in_h": round((ts - now).total_seconds() / 3600, 2),
                }
            )
        end = starts["DONE"]
        return {
            "configured": True,
            "phase": current,
            "source": source,
            "end_tehran": end.astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            "end_utc": end.strftime("%Y-%m-%d %H:%M"),
            "hours_left": round(max((end - now).total_seconds(), 0) / 3600, 2),
            "timezone": self.cfg.base.timezone,
            "rows": rows,
        }

    def _latency(self) -> dict[str, dict[str, float]]:
        recs = []
        for r in read_jsonl(self.logs / "latency.jsonl", self.d.tail_bytes):
            ms = to_float(r.get("ms"), "latency.ms")
            if isinstance(r.get("kind"), str) and ms is not None:
                recs.append({"kind": r["kind"], "ms": ms})
        return latency.summarize(recs)

    def _api(self) -> dict[str, Any]:
        files = sorted(self.logs.glob("system.log*"))
        key = tuple((f.name, f.stat().st_mtime, f.stat().st_size) for f in files)
        if self._api_cache is None or self._api_cache[0] != key:
            self._api_cache = (key, api_stats(self.logs))
        return self._api_cache[1]

    # ------------------------------------------------------------------ reports
    def report_list(self) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for p in sorted(self.reports.rglob("*.md")) if self.reports.exists() else []:
            rel = p.relative_to(self.reports).as_posix()
            top = rel.split("/", 1)[0] if "/" in rel else "(root)"
            groups.setdefault(top, []).append(
                {
                    "path": rel,
                    "mtime": datetime.fromtimestamp(p.stat().st_mtime, UTC).strftime("%Y-%m-%d %H:%M"),
                }
            )
        order = {g: i for i, g in enumerate(REPORT_GROUPS)}
        return dict(sorted(groups.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])))

    def safe_report_path(self, rel: str, suffixes: set[str]) -> Path | None:
        """``reports/<rel>`` if it stays inside ``reports/`` and has an allowed suffix."""
        if not rel or "\\" in rel or rel.startswith("/"):
            return None
        root = self.reports.resolve()
        p = (root / rel).resolve()
        if not p.is_relative_to(root) or p.suffix.lower() not in suffixes or not p.is_file():
            return None
        return p


def _fmt_cond(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


_IMG_SRC = re.compile(r'(<img[^>]*\ssrc=")([^"]+)(")')


def rewrite_images(html: str, report_rel: str) -> str:
    """Point relative ``<img src>`` of a rendered report at ``/reports/file/...``."""
    base = report_rel.rsplit("/", 1)[0] + "/" if "/" in report_rel else ""

    def sub(m: re.Match[str]) -> str:
        src = m.group(2)
        if re.match(r"^(https?:|data:|/)", src):
            return m.group(0)
        return f"{m.group(1)}/reports/file/{base}{src}{m.group(3)}"

    return _IMG_SRC.sub(sub, html)
