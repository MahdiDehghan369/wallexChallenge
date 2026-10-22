"""Read-only data access for the dashboard.

Sources (nothing is ever written): ``logs/decisions.jsonl``, ``logs/regime.jsonl``,
``logs/llm/calls.jsonl``, ``logs/latency.jsonl``, ``logs/system.log*``, ``logs/fills.csv``,
``logs/equity.csv``, ``state/state.sqlite`` (opened with ``mode=ro``), ``state/heartbeat``,
``data/live/*/books.jsonl`` (last best bid for unrealized PnL) and markdown files under ``reports/``.
Missing files give empty sections, never errors.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from siavash.config import AppConfig
from siavash.reporting import latency
from siavash.reporting.equity import trading_day_start
from siavash.reporting.judge_report import OFFICIAL_CAPITAL_TMN, api_stats
from siavash.reporting.metrics import build_trades, load_decisions, load_equity, load_fills
from siavash.runtime import get_runtime

OPEN_STATUSES = ("PENDING_ENTRY", "OPEN", "PENDING_EXIT")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
REPORT_GROUPS = ("live", "shadow", "backtest", "diagnostics", "research_v2")


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


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _thin(df: pd.DataFrame, n: int) -> pd.DataFrame:
    if len(df) <= n:
        return df
    step = -(-len(df) // n)
    return pd.concat([df.iloc[::step], df.iloc[[-1]]]).drop_duplicates(subset="ts")


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

    # ------------------------------------------------------------------ raw sources
    def decisions(self) -> list[dict[str, Any]]:
        return read_jsonl(self.logs / "decisions.jsonl", self.d.tail_bytes)

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
                rows = con.execute("SELECT data, status FROM positions").fetchall()
                out["positions"] = [json.loads(d) for d, s in rows if s in OPEN_STATUSES]
                br = con.execute("SELECT state, reason, since FROM breaker WHERE id = 1").fetchone()
                out["breaker"] = {"state": br[0], "reason": br[1], "since": br[2]} if br else None
                for k, v in con.execute("SELECT k, v FROM kv").fetchall():
                    try:
                        out["kv"][k] = json.loads(v)
                    except json.JSONDecodeError:
                        out["kv"][k] = v
            finally:
                con.close()
        except sqlite3.Error as e:
            out["error"] = f"state.sqlite: {e}"
        return out

    def last_regime(self) -> dict[str, Any] | None:
        recs = read_jsonl(self.logs / "regime.jsonl", 200_000)
        return recs[-1] if recs else None

    def last_prices(self) -> dict[str, float]:
        """Last best bid per symbol from the newest recorded ``data/live/<day>/books.jsonl``."""
        days = sorted(p for p in (self.data / "live").glob("*/books.jsonl"))
        prices: dict[str, float] = {}
        for rec in read_jsonl(days[-1], 2_000_000) if days else []:
            bids = rec.get("bids") or []
            if rec.get("symbol") and bids:
                bid = _num(bids[0].get("price"))
                if bid is not None:
                    prices[rec["symbol"]] = bid
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

    # ------------------------------------------------------------------ pages
    def overview(self) -> dict[str, Any]:
        eq = load_equity(self.logs / "equity.csv")
        st = self.store()
        last = eq.iloc[-1] if not eq.empty else None
        equity = _num(last["equity_tmn"]) if last is not None else None
        dd_series = pd.Series(dtype=float)
        if not eq.empty:
            peak = eq["equity_tmn"].cummax()
            dd_series = (peak - eq["equity_tmn"]) / peak * 100
        tz = self.cfg.base.timezone
        day0 = trading_day_start(datetime.now(UTC), tz)
        fills = load_fills(self.logs / "fills.csv")
        trades = build_trades(fills, load_decisions(self.logs / "decisions.jsonl"))
        fills_today = int((fills["ts"] >= day0).sum()) if not fills.empty else 0
        closed_today = (
            int((pd.to_datetime(trades["exit_ts"], utc=True) >= day0).sum()) if not trades.empty else 0
        )
        entries_today = (
            int(fills[(fills["ts"] >= day0) & (fills["side"] == "buy")]["position_id"].nunique())
            if not fills.empty
            else 0
        )
        chart = _thin(eq.assign(dd=dd_series), self.d.max_chart_points) if not eq.empty else eq
        regime = self.last_regime()
        return {
            "equity_tmn": equity,
            "pnl_pct": (equity - OFFICIAL_CAPITAL_TMN) / OFFICIAL_CAPITAL_TMN * 100
            if equity is not None
            else None,
            "capital_base": OFFICIAL_CAPITAL_TMN,
            "drawdown_pct": float(dd_series.iloc[-1]) if len(dd_series) else None,
            "max_drawdown_pct": float(dd_series.max()) if len(dd_series) else None,
            "daily_pnl_pct": _num(last.get("daily_pnl_pct")) if last is not None else None,
            "exposure_tmn": _num(last.get("exposure_tmn")) if last is not None else None,
            "equity_ts": str(last["ts"])[:19] if last is not None else None,
            "breaker": st["breaker"] or {"state": "NORMAL", "reason": "", "since": None},
            "paused": bool(st["kv"].get("operator_pause", False)),
            "store_error": st["error"],
            "regime": regime,
            "open_positions": len(st["positions"]),
            "entries_today": entries_today,
            "closed_today": closed_today,
            "fills_today": fills_today,
            "chart": {
                "ts": [t.strftime("%Y-%m-%d %H:%M") for t in chart["ts"]] if not eq.empty else [],
                "equity": [round(float(v), 0) for v in chart["equity_tmn"]] if not eq.empty else [],
                "dd": [round(-float(v), 3) for v in chart["dd"]] if not eq.empty else [],
            },
        }

    def opportunities(self, symbol: str = "", strategy: str = "", outcome: str = "") -> dict[str, Any]:
        cands = [d for d in self.decisions() if d.get("type") == "ENTRY_CANDIDATE"]
        options = {
            "symbols": sorted({d["symbol"] for d in cands if d.get("symbol")}),
            "strategies": sorted({d["strategy"] for d in cands if d.get("strategy")}),
            "outcomes": sorted({d["outcome_action"] for d in cands if d.get("outcome_action")}),
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
            sig, ml, llm, risk = (d.get(k) or {} for k in ("signal", "ml", "llm_review", "risk"))
            conds = sig.get("conditions") or {}
            rows.append(
                {
                    "decision_id": d.get("decision_id"),
                    "ts": str(d.get("ts", ""))[:19].replace("T", " "),
                    "symbol": d.get("symbol"),
                    "strategy": d.get("strategy"),
                    "conditions": ", ".join(f"{k}={_fmt_cond(v)}" for k, v in list(conds.items())[:4]),
                    "p_win": _num(ml.get("p_win")),
                    "threshold": _num(ml.get("threshold")),
                    "ml_status": ml.get("status"),
                    "llm_status": llm.get("status"),
                    "llm_verdict": llm.get("verdict"),
                    "llm_reasons": "؛ ".join(str(r) for r in (llm.get("reasons") or [])[:3]),
                    "risk": None if not risk else ("OK" if risk.get("approved") else "REJECT"),
                    "risk_checks": ", ".join(risk.get("checks_failed") or []),
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

    def positions(self) -> dict[str, Any]:
        st = self.store()
        prices = self.last_prices()
        open_rows = []
        for p in st["positions"]:
            entry, qty = _num(p.get("avg_entry")), _num(p.get("qty")) or 0.0
            px = prices.get(p.get("symbol", ""))
            upnl = (px - entry) * qty if px is not None and entry is not None else None
            open_rows.append(
                {
                    **{k: p.get(k) for k in ("symbol", "strategy", "status", "qty")},
                    "entry": entry,
                    "sl": _num(p.get("stop_loss")),
                    "tp": _num(p.get("take_profit")),
                    "trailing": _num(p.get("trailing_stop")),
                    "time_stop": str(p.get("time_stop_at") or "")[:16].replace("T", " "),
                    "opened": str(p.get("opened_at") or "")[:16].replace("T", " "),
                    "price": px,
                    "upnl_tmn": upnl,
                    "upnl_pct": (px / entry - 1) * 100 if px is not None and entry else None,
                }
            )
        fills = load_fills(self.logs / "fills.csv")
        trades = build_trades(fills, load_decisions(self.logs / "decisions.jsonl"))
        closed = []
        for t in trades.iloc[::-1].itertuples(index=False):
            closed.append(
                {
                    "symbol": t.symbol,
                    "strategy": t.strategy,
                    "entry_ts": str(t.entry_ts)[:16],
                    "exit_ts": str(t.exit_ts)[:16],
                    "buy_tmn": float(t.buy_tmn),
                    "sell_tmn": float(t.sell_tmn),
                    "fees_tmn": float(t.fees_tmn),
                    "pnl_tmn": float(t.pnl_tmn),
                    "ret_pct": float(t.ret_pct),
                    "exit_reason": t.exit_reason,
                }
            )
        return {
            "open": open_rows,
            "closed": closed,
            "store_error": st["error"],
            "realized_tmn": float(trades["pnl_tmn"].sum()) if not trades.empty else 0.0,
            "fees_tmn": float(trades["fees_tmn"].sum()) if not trades.empty else 0.0,
            "prices_known": bool(prices),
        }

    def ai(self) -> dict[str, Any]:
        calls = read_jsonl(self.logs / "llm" / "calls.jsonl", self.d.tail_bytes)
        cands = [d for d in self.decisions() if d.get("type") == "ENTRY_CANDIDATE"]
        reviews = [d["llm_review"] for d in cands if d.get("llm_review")]
        ok = [r for r in reviews if r.get("status") == "ok"]
        approve = sum(1 for r in ok if r.get("verdict") == "APPROVE")
        regimes = read_jsonl(self.logs / "regime.jsonl", 2_000_000)[-self.d.regime_history :]
        b = self.cfg.llm.budget
        used = self.store()["kv"].get("llm_budget") or {}
        budget = [
            ("calls this hour", used.get("hour_calls"), b.max_calls_per_hour, used.get("hour")),
            ("calls today", used.get("day_calls"), b.max_calls_per_day, used.get("day")),
            ("tokens today", used.get("day_tokens"), b.max_tokens_per_day, used.get("day")),
        ]
        tokens = sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in calls)
        return {
            "call_status": dict(Counter(str(c.get("status")) for c in calls).most_common()),
            "n_calls": len(calls),
            "tokens_logged": tokens,
            "review_status": dict(Counter(str(r.get("status")) for r in reviews).most_common()),
            "n_reviewed": len(ok),
            "approve_pct": approve / len(ok) * 100 if ok else None,
            "reject_pct": (len(ok) - approve) / len(ok) * 100 if ok else None,
            "budget": [
                {
                    "name": n,
                    "used": u or 0,
                    "limit": lim,
                    "pct": (u or 0) / lim * 100 if lim else None,
                    "period": per,
                }
                for n, u, lim, per in budget
            ],
            "max_input_tokens_per_call": b.max_input_tokens_per_call,
            "regimes": [
                {
                    "ts": str(r.get("ts", ""))[:16].replace("T", " "),
                    "regime": r.get("regime"),
                    "rule_regime": r.get("rule_regime"),
                    "source": r.get("source"),
                    "status": r.get("status"),
                    "risk_multiplier": r.get("risk_multiplier"),
                    "reasons": "؛ ".join(str(x) for x in (r.get("reasons") or [])[:3]),
                }
                for r in reversed(regimes)
            ],
            "regime_sources": dict(Counter(str(r.get("source")) for r in regimes).most_common()),
            "counterfactual": self.counterfactual(),
        }

    def counterfactual(self) -> dict[str, Any] | None:
        for sub in ("live", "shadow"):
            p = self.reports / sub / "metrics.json"
            try:
                cf = json.loads(p.read_text(encoding="utf-8")).get("counterfactual")
            except (OSError, json.JSONDecodeError, AttributeError):
                continue
            if cf:
                return {"source": f"reports/{sub}/metrics.json", "gates": cf}
        return None

    def system(self) -> dict[str, Any]:
        decs = read_jsonl(self.logs / "decisions.jsonl", 200_000)
        last = decs[-1] if decs else None
        rt = get_runtime()
        ident = {k: (last or {}).get(k) for k in ("mode", "git_sha", "version_tag", "config_hash")}
        if last is None:
            ident = {
                "mode": self.cfg.mode,
                "git_sha": rt.git_sha,
                "version_tag": rt.version_tag,
                "config_hash": rt.config_hash,
            }
        return {
            "heartbeat_age_s": self.heartbeat_age_s(),
            "watchdog_timeout_s": self.cfg.base.loop.watchdog_timeout_s,
            "identity": ident,
            "identity_source": "last decision record" if last else "dashboard process",
            "last_decision_ts": str((last or {}).get("ts", ""))[:19] or None,
            "api": self._api(),
            "latency": latency.summarize(read_jsonl(self.logs / "latency.jsonl", self.d.tail_bytes)),
        }

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
