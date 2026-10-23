"""Demo dataset for previewing the read-only dashboard locally (``make dashboard-demo``).

Writes synthetic logs / state / reports into a scratch directory (never the real ``logs/`` or ``state/``)
and serves the dashboard on it. Also used by the page render tests.

    python -m siavash.dashboard.demo                 # http://127.0.0.1:8090  user demo / password demo
    python -m siavash.dashboard.demo --port 9000 --dir /tmp/siavash-demo
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sqlite3
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from siavash.config import AppConfig, EnvSettings, load_config
from siavash.engine.store import SCHEMA

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
SYMBOLS = {
    "BTC/TMN": 7_350_000_000.0,
    "ETH/TMN": 265_000_000.0,
    "USDT/TMN": 104_500.0,
    "SOL/TMN": 15_800_000.0,
}
STRATEGIES = ("S1_TREND_BREAKOUT", "S2_MEAN_REVERSION", "S3_IMPLIED_DEV")
STAGES = ("FEE_FILTER", "ML", "EV", "LLM", "RISK")
REGIMES = ("TREND_UP", "RANGE", "HIGH_VOL", "RANGE", "TREND_UP", "PANIC")
FILL_COLUMNS = [
    "ts",
    "symbol",
    "side",
    "price",
    "qty",
    "notional_tmn",
    "fee",
    "fee_asset",
    "is_maker",
    "client_order_id",
    "decision_id",
    "position_id",
    "strategy",
]


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def demo_config(root: Path, config_dir: Path = CONFIG_DIR) -> AppConfig:
    cfg = load_config(config_dir, env_file=None)
    keys = ("logs", "state", "data", "models", "reports", "submission")
    paths = cfg.base.paths.model_copy(update={k: str(root / k) for k in keys})
    return cfg.model_copy(update={"base": cfg.base.model_copy(update={"paths": paths})})


def build_demo(
    root: Path, now: datetime | None = None, seed: int = 7, config_dir: Path = CONFIG_DIR
) -> AppConfig:
    """Write the demo dataset under ``root`` and return a config whose paths point there."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    rnd = random.Random(seed)
    logs, state, reports = root / "logs", root / "state", root / "reports"
    day = now.strftime("%Y-%m-%d")
    for d in (
        logs / "llm",
        state,
        reports / "live",
        reports / "backtest" / "walkforward",
        reports / "research_v2",
        reports / "diagnostics",
        root / "data" / "live" / day,
    ):
        d.mkdir(parents=True, exist_ok=True)

    # equity: 3 days of 15-minute points, a gentle random walk from the official 5,000,000 TMN
    eq_rows, eq = [], 5_000_000.0
    start = now - timedelta(days=3)
    for k in range(3 * 96 + 1):
        eq *= 1 + rnd.gauss(0.00012, 0.0016)
        eq_rows.append((start + timedelta(minutes=15 * k), round(eq)))
    with (logs / "equity.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "ts",
                "equity_tmn",
                "cash_tmn",
                "exposure_tmn",
                "peak_equity_tmn",
                "drawdown_pct",
                "daily_pnl_pct",
            ]
        )
        peak = 0.0
        for t, v in eq_rows:
            peak = max(peak, v)
            w.writerow(
                [_iso(t), v, round(v * 0.78), round(v * 0.22), peak, round((peak - v) / peak * 100, 3), 0.42]
            )

    # decisions: entry candidates through every gate, plus other record types
    decisions: list[dict[str, Any]] = []
    for n in range(36):
        t = now - timedelta(minutes=37 * (36 - n))
        sym = rnd.choice(list(SYMBOLS))
        strat = rnd.choice(STRATEGIES)
        p_win = round(rnd.uniform(0.38, 0.74), 3)
        outcome = "ORDER_PLACED" if n % 6 == 0 else ("REJECTED" if n % 3 else "INFO")
        stage = None if outcome != "REJECTED" else STAGES[n % len(STAGES)]
        verdict = "APPROVE" if stage != "LLM" else "REJECT"
        rec: dict[str, Any] = {
            "decision_id": f"01JDEMO{n:019d}",
            "ts": _iso(t),
            "mode": "paper",
            "git_sha": "demo123",
            "version_tag": "demo",
            "config_hash": "sha256:demo",
            "type": "ENTRY_CANDIDATE",
            "symbol": sym,
            "strategy": strat,
            "signal": {"conditions": {"ema_ok": True, "rsi": round(rnd.uniform(25, 60), 2), "atr_pct": 0.84}},
            "ml": {"status": "ok", "p_win": p_win, "threshold": 0.55},
            "llm_review": {
                "status": "ok" if n % 11 else "timeout",
                "verdict": verdict if n % 11 else None,
                "size_multiplier": 1.0 if verdict == "APPROVE" else 0.0,
                "reasons": ["روند سالم" if verdict == "APPROVE" else "ریسک خبر", "حجم کافی"],
            },
            "risk": {
                "approved": stage != "RISK",
                "checks_failed": ["max_exposure"] if stage == "RISK" else [],
            },
            "outcome_action": outcome,
            "final_reason": (
                f"{sym} {strat}: همه دروازه‌ها تأیید کردند، سفارش LIMIT ثبت شد"
                if outcome == "ORDER_PLACED"
                else (f"{sym}: رد در مرحله {stage}" if stage else f"{sym}: فقط ثبت اطلاعات")
            ),
        }
        if stage:
            rec |= {"rejected_at": stage, "reject_code": f"{stage}_REJECT"}
        decisions.append(rec)
    decisions.append(
        {
            "decision_id": "01JDEMOREGIME0000000000001",
            "ts": _iso(now - timedelta(minutes=9)),
            "type": "REGIME_UPDATE",
            "final_reason": "رژیم: TREND_UP (llm)",
            "mode": "paper",
        }
    )
    _jsonl(logs / "decisions.jsonl", decisions)

    # fills: four closed round trips (two today) + the buys of the two open positions
    fills: list[list[Any]] = []
    for k, (sym, ret) in enumerate(
        [("BTC/TMN", 0.012), ("ETH/TMN", -0.006), ("SOL/TMN", 0.018), ("BTC/TMN", 0.004)]
    ):
        px = SYMBOLS[sym]
        t0 = now - timedelta(hours=40 - 11 * k)
        qty = round(450_000 / px, 6)
        for side, t, p in (("buy", t0, px), ("sell", t0 + timedelta(hours=3), px * (1 + ret))):
            notional = p * qty
            fills.append(
                [
                    _iso(t),
                    sym,
                    side,
                    round(p, 2),
                    qty,
                    round(notional),
                    round(notional * 0.0025),
                    "TMN",
                    "false",
                    f"c{k}{side}",
                    f"d{k}",
                    f"PD{k}",
                    STRATEGIES[k % 3],
                ]
            )
    with (logs / "fills.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(FILL_COLUMNS)
        w.writerows(fills)

    _jsonl(
        logs / "regime.jsonl",
        [
            {
                "ts": _iso(now - timedelta(minutes=30 * (40 - j))),
                "regime": REGIMES[(j // 6) % len(REGIMES)],
                "rule_regime": "RANGE",
                "risk_multiplier": 1.0 if j % 5 else 0.5,
                "source": "llm" if j % 4 else "rule",
                "status": "ok",
                "reasons": ["EMA ها هم‌جهت", "نوسان معمولی"],
                "enabled_strategies": [],
            }
            for j in range(40)
        ],
    )
    _jsonl(
        logs / "llm" / "calls.jsonl",
        [
            {
                "ts": _iso(now - timedelta(minutes=20 * j)),
                "status": "ok" if j % 9 else "timeout",
                "prompt_tokens": 1800 + 7 * j,
                "completion_tokens": 160,
            }
            for j in range(30)
        ],
    )
    _jsonl(
        logs / "latency.jsonl",
        [
            {"ts": _iso(now), "kind": k, "ms": rnd.uniform(lo, hi)}
            for k, lo, hi in (("candle_close_to_decision", 180, 900), ("decision_to_order", 60, 400))
            for _ in range(40)
        ],
    )
    _jsonl(
        logs / "system.log",
        [
            {"event": "http_request", "status": 200 if j % 40 else 503, "ms": rnd.uniform(70, 420)}
            for j in range(400)
        ],
    )

    con = sqlite3.connect(state / "state.sqlite")
    try:
        con.executescript(SCHEMA)
        for pid, sym, entry, sl, tp, trail in (
            ("PO1", "BTC/TMN", 7_290_000_000, 7_150_000_000, 7_580_000_000, 7_240_000_000),
            ("PO2", "ETH/TMN", 268_000_000, 262_500_000, 279_000_000, None),
        ):
            pos = {
                "position_id": pid,
                "symbol": sym,
                "strategy": "S1_TREND_BREAKOUT",
                "status": "OPEN",
                "entry_decision_id": "01JDEMO0000000000000000000",
                "qty": str(round(600_000 / entry, 6)),
                "avg_entry": str(entry),
                "stop_loss": str(sl),
                "take_profit": str(tp),
                "trailing_stop": str(trail) if trail else None,
                "time_stop_at": _iso(now + timedelta(hours=5)),
                "opened_at": _iso(now - timedelta(hours=2)),
            }
            con.execute(
                "INSERT OR REPLACE INTO positions VALUES (?,?,?,?)", (pid, sym, "OPEN", json.dumps(pos))
            )
        con.execute("INSERT OR REPLACE INTO breaker VALUES (1, 'NORMAL', '', ?)", (_iso(now),))
        budget = {
            "day": day,
            "day_calls": 64,
            "day_tokens": 131_000,
            "hour": now.strftime("%Y-%m-%dT%H"),
            "hour_calls": 7,
        }
        con.execute("INSERT OR REPLACE INTO kv VALUES ('llm_budget', ?)", (json.dumps(budget),))
        con.commit()
    finally:
        con.close()
    (state / "heartbeat").write_text(_iso(now), encoding="utf-8")
    cfg = demo_config(root, config_dir)
    end = cfg.risk.endgame.competition_end_utc
    (state / "engine.json").write_text(
        json.dumps(
            {
                "mode": "paper",
                "git_sha": "demo123",
                "version_tag": "demo",
                "config_hash": "sha256:demo",
                "started_at": _iso(now - timedelta(hours=6)),
                "pid": 1,
                "fees": {
                    "source": "account",
                    "effective": {"maker_pct": 0.25, "taker_pct": 0.25},
                    "config": {"maker_pct": 0.25, "taker_pct": 0.25},
                },
                "endgame": cfg.risk.endgame.model_dump(mode="json")
                | ({} if end else {"competition_end_utc": _iso(now + timedelta(days=3))}),
            }
        ),
        encoding="utf-8",
    )
    books = [
        {"symbol": "BTC/TMN", "ts": _iso(now), "bids": [{"price": "7412000000", "qty": "0.1"}], "asks": []},
        {"symbol": "ETH/TMN", "ts": _iso(now), "bids": [{"price": "266100000", "qty": "1"}], "asks": []},
    ]
    _jsonl(root / "data" / "live" / day / "books.jsonl", books)

    (reports / "live" / "REPORT.md").write_text(
        "# Live report (demo)\n\nSynthetic numbers for the dashboard preview.\n\n"
        "| metric | value |\n|---|---|\n| net return | +1.84 % |\n| max drawdown | -0.92 % |\n| trades | 4 |\n\n"
        "## Notes\n\n- fees floored at the account rate\n- `initial_live_risk_scale` = 0.5\n\n"
        "```text\nsiavash report --config config\n```\n\n> Demo data only.\n",
        encoding="utf-8",
    )
    (reports / "live" / "metrics.json").write_text(
        json.dumps(
            {
                "counterfactual": {
                    "LLM": {
                        "rejected": 7,
                        "correct_rejections": 5,
                        "missed_opportunities": 2,
                        "avoided_loss_pct": 2.1,
                        "missed_profit_pct": 0.7,
                        "net_value_pct": 1.4,
                    },
                    "ML": {
                        "rejected": 9,
                        "correct_rejections": 5,
                        "missed_opportunities": 4,
                        "avoided_loss_pct": 1.2,
                        "missed_profit_pct": 1.5,
                        "net_value_pct": -0.3,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    for rel, title in (
        ("backtest/walkforward/REPORT.md", "Walk-forward"),
        ("research_v2/SUMMARY.md", "Research v2 summary"),
        ("diagnostics/SUMMARY.md", "Edge diagnostics"),
    ):
        (reports / rel).write_text(
            f"# {title} (demo)\n\nPre-challenge research placeholder.\n", encoding="utf-8"
        )
    return cfg


def _keep_alive(heartbeat: Path, every_s: float) -> None:
    while True:  # demo only: keeps the scratch heartbeat fresh so /system shows a healthy engine
        heartbeat.touch()
        time.sleep(every_s)


def main(argv: list[str] | None = None) -> int:
    from siavash.dashboard.app import serve

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(CONFIG_DIR), help="config directory (default: the repo config/)")
    ap.add_argument("--dir", default=None, help="scratch directory (default: a new temp dir)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--user", default="demo")
    ap.add_argument("--password", default="demo")
    args = ap.parse_args(argv)
    root = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="siavash-demo-"))
    cfg = build_demo(root, config_dir=Path(args.config))
    threading.Thread(target=_keep_alive, args=(root / "state" / "heartbeat", 10.0), daemon=True).start()
    env = EnvSettings(  # type: ignore[call-arg]
        _env_file=None,
        dashboard_host=args.host,
        port=args.port,
        dashboard_user=args.user,
        dashboard_password=SecretStr(args.password),
    )
    print(
        f"demo data: {root}\nopen http://{args.host}:{args.port}/  (user {args.user} / password {args.password})"
    )
    serve(cfg, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
