"""Read-only dashboard: auth, GET-only routes, pages render on fixture logs, no secrets leak."""

from __future__ import annotations

import base64
import csv
import json
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from starlette.routing import Route
from starlette.testclient import TestClient
from structlog.testing import capture_logs

from siavash.config import AppConfig, EnvSettings, Secrets, load_config
from siavash.dashboard import data as dash_data
from siavash.dashboard.app import bind_address, create_app
from siavash.dashboard.data import DashboardData, open_ro, to_float, to_int
from siavash.engine.store import SCHEMA
from siavash.runtime import detect_git_sha

ROOT = Path(__file__).resolve().parents[3]
PASSWORD = "dash-pass-7c1f9e"
SECRETS = ["wallex-key-0b5d2a91", "llm-key-6e3f8c27", "https://hooks.example/abc-secret-4411"]
NOW = "2026-10-22T10:00:00Z"


def _decision(i: int, **kw: object) -> dict[str, object]:
    rec: dict[str, object] = {
        "decision_id": f"01J{i:023d}",
        "ts": f"2026-10-22T0{i}:00:00Z",
        "mode": "paper",
        "git_sha": "abc1234",
        "version_tag": "v-test",
        "config_hash": "sha256:feedbeef",
        "type": "ENTRY_CANDIDATE",
        "outcome_action": "INFO",
        "final_reason": f"reason {i}",
    }
    rec.update(kw)
    return rec


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    logs, state, reports = tmp_path / "logs", tmp_path / "state", tmp_path / "reports"
    for d in (
        logs / "llm",
        state,
        reports / "live",
        reports / "research_v2",
        tmp_path / "data" / "live" / "2026-10-22",
    ):
        d.mkdir(parents=True)
    decisions = [
        _decision(
            1,
            symbol="BTC/TMN",
            strategy="S1_TREND_PULLBACK",
            outcome_action="ORDER_PLACED",
            position_id="P1",
            signal={"conditions": {"ema_ok": True, "rsi": 41.25}},
            ml={"status": "ok", "p_win": 0.612, "threshold": 0.55},
            llm_review={
                "status": "ok",
                "verdict": "APPROVE",
                "size_multiplier": 1.0,
                "reasons": ["trend intact"],
            },
            risk={"approved": True, "checks_failed": [], "equity_tmn": "5000000"},
            final_reason="BTC pullback approved by all gates",
        ),
        _decision(
            2,
            symbol="ETH/TMN",
            strategy="S2_MEANREV",
            outcome_action="REJECTED",
            rejected_at="LLM",
            reject_code="LLM_REJECT",
            llm_review={
                "status": "ok",
                "verdict": "REJECT",
                "size_multiplier": 0.25,
                "reasons": ["news risk"],
            },
            final_reason="ETH mean reversion rejected by reviewer",
        ),
        _decision(3, type="REGIME_UPDATE", final_reason="regime updated"),
    ]
    (logs / "decisions.jsonl").write_text(
        "\n".join(json.dumps(d) for d in decisions) + "\n", encoding="utf-8"
    )
    with (logs / "fills.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
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
        )
        w.writerow(
            [
                "2026-10-22T01:00:00Z",
                "BTC/TMN",
                "buy",
                "100",
                "10",
                "1000",
                "2",
                "TMN",
                "false",
                "c1",
                "d1",
                "P1",
                "S1_TREND_PULLBACK",
            ]
        )
        w.writerow(
            [
                "2026-10-22T03:00:00Z",
                "BTC/TMN",
                "sell",
                "110",
                "10",
                "1100",
                "2",
                "TMN",
                "true",
                "c2",
                "d1",
                "P1",
                "S1_TREND_PULLBACK",
            ]
        )
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
        for h, eq in enumerate([5_000_000, 5_100_000, 5_049_000, 5_123_456]):
            w.writerow([f"2026-10-22T0{h}:00:00Z", eq, eq - 250_000, 250_000, max(eq, 5_100_000), 0, 0.5])
    (logs / "regime.jsonl").write_text(
        json.dumps(
            {
                "ts": NOW,
                "regime": "TREND_UP",
                "risk_multiplier": 1.0,
                "enabled_strategies": [],
                "reasons": ["ema stack up"],
                "source": "llm",
                "rule_regime": "RANGE",
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (logs / "llm" / "calls.jsonl").write_text(
        json.dumps({"ts": NOW, "status": "ok", "prompt_tokens": 900, "completion_tokens": 100})
        + "\n"
        + json.dumps({"ts": NOW, "status": "timeout"})
        + "\n",
        encoding="utf-8",
    )
    (logs / "latency.jsonl").write_text(
        json.dumps({"ts": NOW, "kind": "candle_close_to_decision", "ms": 420.0}) + "\n", encoding="utf-8"
    )
    (logs / "system.log").write_text(
        json.dumps({"event": "http_request", "status": 200, "ms": 120})
        + "\n"
        + json.dumps({"event": "http_request", "status": 503, "ms": 900})
        + "\n",
        encoding="utf-8",
    )
    con = sqlite3.connect(state / "state.sqlite")
    con.executescript(SCHEMA)
    pos = {
        "position_id": "P2",
        "symbol": "ETH/TMN",
        "strategy": "S1_TREND_PULLBACK",
        "status": "OPEN",
        "entry_decision_id": "d2",
        "qty": "0.5",
        "avg_entry": "200000",
        "stop_loss": "195000",
        "take_profit": "210000",
        "trailing_stop": "198000",
        "time_stop_at": "2026-10-22T18:00:00Z",
    }
    con.execute("INSERT INTO positions VALUES (?,?,?,?)", ("P2", "ETH/TMN", "OPEN", json.dumps(pos)))
    con.execute("INSERT INTO breaker VALUES (1, 'NO_NEW_ENTRIES', 'API errors: 5 in 5 min', ?)", (NOW,))
    con.execute(
        "INSERT INTO kv VALUES ('llm_budget', ?)",
        (
            json.dumps(
                {
                    "day": "2026-10-22",
                    "day_calls": 50,
                    "day_tokens": 100000,
                    "hour": "2026-10-22T13",
                    "hour_calls": 5,
                }
            ),
        ),
    )
    con.commit()
    con.close()
    (state / "heartbeat").write_text(NOW, encoding="utf-8")
    (tmp_path / "data" / "live" / "2026-10-22" / "books.jsonl").write_text(
        json.dumps({"symbol": "ETH/TMN", "ts": NOW, "bids": [{"price": "204000", "qty": "1"}], "asks": []})
        + "\n",
        encoding="utf-8",
    )
    (reports / "live" / "REPORT.md").write_text(
        "# Live report\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n![equity](equity.png)\n\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )
    (reports / "live" / "equity.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (reports / "live" / "metrics.json").write_text(
        json.dumps(
            {
                "counterfactual": {
                    "LLM": {
                        "rejected": 3,
                        "correct_rejections": 2,
                        "missed_opportunities": 1,
                        "avoided_loss_pct": 1.5,
                        "missed_profit_pct": 0.4,
                        "net_value_pct": 1.1,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (reports / "research_v2" / "SUMMARY.md").write_text("# research v2\n", encoding="utf-8")
    return tmp_path


def _cfg(root: Path) -> AppConfig:
    cfg = load_config(ROOT / "config", env_file=None)
    paths = cfg.base.paths.model_copy(
        update={k: str(root / k) for k in ("logs", "state", "data", "models", "reports")}
    )
    secrets = Secrets(
        wallex_api_key=SecretStr(SECRETS[0]),
        llm_api_key=SecretStr(SECRETS[1]),
        alert_webhook_url=SecretStr(SECRETS[2]),
    )
    return cfg.model_copy(update={"base": cfg.base.model_copy(update={"paths": paths}), "secrets": secrets})


def _env(password: str | None = PASSWORD, **kw: object) -> EnvSettings:
    return EnvSettings(_env_file=None, dashboard_user="judge", dashboard_password=password, **kw)  # type: ignore[call-arg]


def _auth(user: str = "judge", pw: str = PASSWORD) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


@pytest.fixture()
def client(root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("WALLEX_API_KEY", SECRETS[0])
    monkeypatch.setenv("LLM_API_KEY", SECRETS[1])
    return TestClient(create_app(_cfg(root), _env()))


PAGES = [
    "/",
    "/opportunities",
    "/positions",
    "/ai",
    "/reports",
    "/system",
    "/reports/view/live/REPORT.md",
    f"/opportunities/01J{1:023d}",
]


# ---------------------------------------------------------------- auth


def test_health_is_public_and_tracks_heartbeat(client: TestClient, root: Path) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    old = time.time() - 3600
    os.utime(root / "state" / "heartbeat", (old, old))
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "stale"


@pytest.mark.parametrize("path", PAGES)
def test_pages_require_auth(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get(path, headers=_auth(pw="wrong")).status_code == 401
    assert client.get(path, headers=_auth(user="admin")).status_code == 401
    assert client.get(path, headers={"Authorization": "Basic !!notbase64"}).status_code == 401
    assert client.get(path, headers=_auth()).status_code == 200


def test_disabled_without_password(root: Path) -> None:
    c = TestClient(create_app(_cfg(root), _env(password=None)))
    assert c.get("/", headers=_auth()).status_code == 503
    assert "disabled" in c.get("/").text
    assert c.get("/health").status_code == 200


def test_default_bind_is_loopback(root: Path) -> None:
    cfg = _cfg(root)
    assert bind_address(cfg, _env()) == ("127.0.0.1", 8080)
    assert bind_address(cfg, _env(dashboard_host="0.0.0.0", port=9000)) == ("0.0.0.0", 9000)


# ---------------------------------------------------------------- read-only


def test_only_get_routes(client: TestClient) -> None:
    app = client.app
    routes = [r for r in app.routes if isinstance(r, Route)]  # type: ignore[attr-defined]
    assert routes
    for r in routes:
        assert r.methods is not None and r.methods <= {"GET", "HEAD"}, r.path
    for path in [*PAGES, "/health"]:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, path, headers=_auth()).status_code == 405, (method, path)


def test_store_is_never_written(client: TestClient, root: Path) -> None:
    db = root / "state" / "state.sqlite"
    before = db.read_bytes()
    for path in PAGES:
        client.get(path, headers=_auth())
    assert db.read_bytes() == before
    con = open_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM positions")
    con.close()


# ---------------------------------------------------------------- pages


def test_overview(client: TestClient) -> None:
    t = client.get("/", headers=_auth()).text
    assert "5,123,456" in t  # equity
    assert "2.47" in t  # (5,123,456 − 5,000,000) / 5,000,000
    assert "NO_NEW_ENTRIES" in t and "TREND_UP" in t and "llm" in t
    assert "cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/" in t
    assert 'dir="rtl"' in t and "Vazirmatn" in t
    assert '<meta http-equiv="refresh" content="30">' in t


def test_opportunities_filters_and_detail(client: TestClient) -> None:
    t = client.get("/opportunities", headers=_auth()).text
    assert "BTC pullback approved by all gates" in t and "ETH mean reversion" in t
    assert "0.612" in t and "trend intact" in t and "LLM_REJECT" in t
    assert t.index("ETH/TMN</td>") < t.index("BTC/TMN</td>")  # newest first
    assert "regime updated" not in t  # only ENTRY_CANDIDATE
    t = client.get("/opportunities?outcome=REJECTED", headers=_auth()).text
    assert "ETH mean reversion" in t and "BTC pullback" not in t
    t = client.get("/opportunities?symbol=BTC/TMN&strategy=S1_TREND_PULLBACK", headers=_auth()).text
    assert "BTC pullback" in t and "ETH mean reversion" not in t
    d = client.get(f"/opportunities/01J{1:023d}", headers=_auth())
    assert d.status_code == 200 and "&#34;p_win&#34;: 0.612" in d.text
    assert client.get("/opportunities/nope", headers=_auth()).status_code == 404


def test_positions(client: TestClient) -> None:
    t = client.get("/positions", headers=_auth()).text
    assert "195,000" in t and "210,000" in t and "198,000" in t and "2026-10-22 18:00" in t
    assert "2,000" in t  # unrealized (204,000 − 200,000) × 0.5
    assert "96" in t  # realized 1100 − 1000 − 4 fees


def test_ai(client: TestClient) -> None:
    t = client.get("/ai", headers=_auth()).text
    assert "50.0 %" in t  # approve rate 1 of 2
    assert "timeout" in t and "100,000" in t and "400,000" in t  # budget tokens used vs limit
    assert "ema stack up" in t
    assert "reports/live/metrics.json" in t and "1.10" in t


def test_reports(client: TestClient) -> None:
    t = client.get("/reports", headers=_auth()).text
    assert "live/REPORT.md" in t and "research_v2/SUMMARY.md" in t
    t = client.get("/reports/view/live/REPORT.md", headers=_auth()).text
    assert "<table>" in t and 'src="/reports/file/live/equity.png"' in t
    assert "<script>alert(1)</script>" not in t  # raw HTML in markdown is escaped
    img = client.get("/reports/file/live/equity.png", headers=_auth())
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    for bad in (
        "/reports/view/../logs/decisions.jsonl",
        "/reports/file/live/metrics.json",
        "/reports/view/%2e%2e/%2e%2e/etc/passwd",
        "/reports/file/live/missing.png",
    ):
        assert client.get(bad, headers=_auth()).status_code == 404, bad


def test_system(client: TestClient) -> None:
    t = client.get("/system", headers=_auth()).text
    assert "abc1234" in t and "v-test" in t and "sha256:feedbeef" in t
    assert "50.00 %" in t  # 1 of 2 http_request errors
    assert "candle_close_to_decision" in t


def test_no_secrets_in_responses(client: TestClient) -> None:
    for path in [*PAGES, "/health", "/reports/file/live/equity.png", "/static/dashboard.js"]:
        r = client.get(path, headers=_auth())
        assert r.status_code == 200, path
        body = r.content.decode("utf-8", errors="replace")
        for s in [*SECRETS, PASSWORD]:
            assert s not in body, (path, s)
        assert r.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


# ---------------------------------------------------------------- robustness (bad numbers, failing sections)

UNAVAILABLE = "داده در دسترس نیست"


def test_numeric_coercion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dash_data, "_warned_fields", set())
    assert to_int("12", "f") == 12 and to_int("12.7", "f") == 12 and to_int(7, "f") == 7
    assert to_int(None, "f") == 0 and to_int("", "f") == 0 and to_int(None, "f", default=3) == 3
    assert to_float("0.5", "f") == 0.5 and to_float(None, "f") is None and to_float("nan", "f") is None
    with capture_logs() as logs:
        assert to_int("abc", "calls.prompt_tokens") == 0
        assert to_int([1], "calls.prompt_tokens") == 0  # same field: warned once
        assert to_int(True, "llm_budget.day_calls") == 0
        assert to_float("inf", "regime.risk_multiplier") is None
    bad = [x for x in logs if x["event"] == "dashboard_bad_number"]
    assert [x["field"] for x in bad] == [
        "calls.prompt_tokens",
        "llm_budget.day_calls",
        "regime.risk_multiplier",
    ]


def _jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


@pytest.fixture()
def bad_root(root: Path) -> Path:
    """Fixture logs where numeric fields are strings, null, missing or malformed (Liara /ai crash)."""
    logs, state = root / "logs", root / "state"
    _jsonl(
        logs / "llm" / "calls.jsonl",
        [
            {
                "ts": NOW,
                "status": "ok",
                "prompt_tokens": None,
                "completion_tokens": "100",
            },  # the int + str crash
            {"ts": NOW, "status": "ok", "prompt_tokens": "900", "completion_tokens": "abc"},
            {"ts": NOW, "status": "ok", "prompt_tokens": [1], "completion_tokens": True},
            {"ts": NOW, "status": "timeout"},
            "{not json",
            "[1, 2]",
        ],
    )
    _jsonl(
        logs / "decisions.jsonl",
        [
            _decision(
                1,
                symbol="BTC/TMN",
                strategy="S1_TREND_PULLBACK",
                signal="garbage",
                ml={"status": "ok", "p_win": "0.7", "threshold": "n/a"},
                llm_review="garbage",
                risk="bad",
                regime="TREND_UP",
            ),
            {"type": "ENTRY_CANDIDATE", "ts": 12345},  # no decision_id
            "{broken",
            _decision(2, symbol="ETH/TMN", llm_review={"status": "ok", "verdict": "APPROVE", "reasons": "x"}),
        ],
    )
    _jsonl(
        logs / "regime.jsonl",
        [{"ts": 123, "regime": "RANGE", "risk_multiplier": "high", "reasons": "not a list", "source": None}],
    )
    _jsonl(
        logs / "latency.jsonl", [{"kind": "candle_close_to_decision", "ms": "slow"}, {"kind": None, "ms": 5}]
    )
    _jsonl(logs / "system.log", [{"event": "http_request", "status": "abc", "ms": "x"}])
    with (logs / "equity.csv").open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(["2026-10-22T05:00:00Z", "n/a", "", "", "", "", "oops"])
    with (logs / "fills.csv").open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(
            ["2026-10-22T04:00:00Z", "ETH/TMN", "buy", "abc", None, "", "x", "TMN", "", "c3", "d3", "P3", ""]
        )
    (root / "reports" / "live" / "metrics.json").write_text(
        json.dumps({"counterfactual": {"LLM": {"rejected": "3", "net_value_pct": "lots"}, "ML": "bad"}}),
        encoding="utf-8",
    )
    (root / "data" / "live" / "2026-10-22" / "books.jsonl").write_text(
        json.dumps({"symbol": "ETH/TMN", "bids": [{"price": "x"}]})
        + "\n"
        + json.dumps({"symbol": 5, "bids": "no"}),
        encoding="utf-8",
    )
    con = sqlite3.connect(state / "state.sqlite")
    con.execute("DELETE FROM kv")
    con.execute(
        "INSERT INTO kv VALUES ('llm_budget', ?)",
        (json.dumps({"day": "2026-10-22", "day_calls": "50", "day_tokens": None, "hour_calls": "x"}),),
    )
    pos = {"symbol": "BTC/TMN", "status": "OPEN", "qty": "abc", "avg_entry": None, "stop_loss": "1e400"}
    con.execute("INSERT INTO positions VALUES (?,?,?,?)", ("P4", "BTC/TMN", "OPEN", json.dumps(pos)))
    con.execute("INSERT INTO positions VALUES (?,?,?,?)", ("P5", "BTC/TMN", "OPEN", "{not json"))
    con.commit()
    con.close()
    return root


@pytest.fixture()
def bad_client(bad_root: Path) -> TestClient:
    return TestClient(create_app(_cfg(bad_root), _env()), raise_server_exceptions=False)


@pytest.mark.parametrize("path", PAGES)
def test_pages_survive_bad_numbers(bad_client: TestClient, path: str) -> None:
    r = bad_client.get(path, headers=_auth())
    assert r.status_code == 200, (path, r.text[:500])


def test_ai_with_string_numbers(bad_client: TestClient) -> None:
    t = bad_client.get("/ai", headers=_auth()).text
    assert UNAVAILABLE not in t  # every /ai section loads; bad values become 0 / —
    assert "1,000" in t  # tokens: 0 + 100, 900 + 0, 0 + 0, 0 + 0
    assert ">50<" in t and "25.0 %" in t  # day_calls "50" of 200 calls/day; hour_calls "x" -> 0
    assert "RANGE" in t and "not a list" not in t
    assert "lots" not in t and "reports/live/metrics.json" in t


def test_other_pages_with_bad_values(bad_client: TestClient) -> None:
    t = bad_client.get("/", headers=_auth()).text
    assert "5,123,456" in t  # the malformed equity row is dropped, not fatal
    t = bad_client.get("/opportunities", headers=_auth()).text
    assert "0.700" in t and "reason 2" in t
    t = bad_client.get("/system", headers=_auth()).text
    assert UNAVAILABLE in t and "(api: ValueError)" in t  # http status "abc" -> only the api section fails
    assert "abc1234" in t  # identity section still renders


# section loaders: one failing section -> inline box, the rest of the page still renders
SECTIONS = [
    ("/", "_equity", "equity", "NO_NEW_ENTRIES"),
    ("/", "_status", "status", "5,123,456"),
    ("/", "_activity", "activity", "TREND_UP"),
    ("/", "last_regime", "regime", "5,123,456"),
    ("/", "endgame", "endgame", "5,123,456"),
    ("/system", "endgame", "endgame", "abc1234"),
    ("/positions", "_open_positions", "open_positions", "96"),
    ("/positions", "_closed_trades", "closed_trades", "195,000"),
    ("/ai", "_ai_calls", "llm_calls", "50.0 %"),
    ("/ai", "_ai_reviews", "llm_reviews", "timeout"),
    ("/ai", "_ai_budget", "llm_budget", "ema stack up"),
    ("/ai", "_ai_regimes", "regimes", "400,000"),
    ("/ai", "counterfactual", "counterfactual", "ema stack up"),
    ("/system", "_sys_health", "health", "abc1234"),
    ("/system", "_sys_identity", "identity", "candle_close_to_decision"),
    ("/system", "_api", "api", "abc1234"),
    ("/system", "_latency", "latency", "50.00 %"),
]


@pytest.mark.parametrize(("path", "method", "name", "still_shown"), SECTIONS)
def test_failing_section_renders_inline_box(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, method: str, name: str, still_shown: str
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(DashboardData, method, boom)
    r = client.get(path, headers=_auth())
    assert r.status_code == 200
    assert UNAVAILABLE in r.text and f"({name}: RuntimeError)" in r.text
    assert still_shown in r.text


# whole-page loaders: failure outside any section -> 200 with a page-level box, never a 500
PAGE_LOADERS = [
    ("/", "overview"),
    ("/opportunities", "opportunities"),
    (f"/opportunities/01J{1:023d}", "decision"),
    ("/positions", "positions"),
    ("/ai", "ai"),
    ("/reports", "report_list"),
    ("/reports/view/live/REPORT.md", "safe_report_path"),
    ("/system", "system"),
    ("/", "store"),
    ("/ai", "decisions"),
]


@pytest.mark.parametrize(("path", "method"), PAGE_LOADERS)
def test_failing_page_loader_never_500(
    root: Path, monkeypatch: pytest.MonkeyPatch, path: str, method: str
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(DashboardData, method, boom)
    c = TestClient(create_app(_cfg(root), _env()), raise_server_exceptions=False)
    r = c.get(path, headers=_auth())
    assert r.status_code == 200 and UNAVAILABLE in r.text, path
    assert "boom" not in r.text  # only the exception type is shown; details go to the log


# ---------------------------------------------------------------- git_sha (Liara showed "unknown")


def test_dockerfile_bakes_git_sha() -> None:
    lines = [x.strip() for x in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()]
    arg = lines.index("ARG GIT_SHA")
    env = next(i for i, x in enumerate(lines) if x.startswith("ENV GIT_SHA=$GIT_SHA"))
    assert arg < env


def test_runtime_reads_env_git_sha_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "deadbee")
    assert detect_git_sha() == "deadbee"


# ---------------------------------------------------------------- engine identity + endgame (go-live)


def _engine_json(root: Path, end: datetime | None, **kw: object) -> None:
    eg = {
        "competition_end_utc": end.isoformat() if end else None,
        "no_new_entries_hours_before": 6,
        "start_unwind_hours_before": 3,
        "force_liquidate_hours_before": 1,
        "unwind_passive_timeout_s": 30,
    }
    rec = {
        "run_id": "r1",
        "git_sha": "liv3sha",
        "version_tag": "live-v1.0",
        "mode": "live",
        "config_hash": "sha256:eng1ne",
        "started_at": "2026-10-23T10:00:00+00:00",
        "pid": 7,
        "fees": {
            "source": "account",
            "effective": {"maker_pct": 0.3, "taker_pct": 0.3},
            "config": {"maker_pct": 0.25, "taker_pct": 0.25},
        },
        "endgame": eg,
        **kw,
    }
    (root / "state" / "engine.json").write_text(json.dumps(rec), encoding="utf-8")


def test_system_shows_engine_identity_and_endgame(client: TestClient, root: Path) -> None:
    end = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=5)  # 5 h left -> NO_NEW
    _engine_json(root, end)
    t = client.get("/system", headers=_auth()).text
    assert "liv3sha" in t and "live-v1.0" in t and "sha256:eng1ne" in t
    assert "حالت: live" in t  # header badge = the engine's mode, not base.yaml's paper
    assert '<b class="ltr">NO_NEW</b>' in t and "engine.json" in t
    tehran = end.astimezone(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")
    assert tehran in t and end.strftime("%Y-%m-%d %H:%M") in t
    assert "0.300" in t and "account" in t  # effective fees + source
    o = client.get("/", headers=_auth()).text
    assert "فاز endgame" in o and "NO_NEW" in o and tehran in o


def test_endgame_phases_and_fallbacks(root: Path) -> None:
    data = DashboardData(_cfg(root))
    now = datetime.now(UTC)
    for hours, phase in ((10, "NONE"), (5, "NO_NEW"), (2, "UNWIND"), (0.5, "FORCE"), (-1, "DONE")):
        _engine_json(root, now + timedelta(hours=hours))
        eg = data.endgame()
        assert eg["phase"] == phase and eg["source"] == "engine.json", hours
        assert [r["phase"] for r in eg["rows"]] == ["NO_NEW", "UNWIND", "FORCE", "DONE"]
    _engine_json(root, None)
    assert data.endgame()["configured"] is False
    (root / "state" / "engine.json").unlink()
    assert data.endgame()["source"] == "config" and data.engine_identity() == {}
    (root / "state" / "engine.json").write_text("{not json", encoding="utf-8")
    assert data.engine_identity() == {} and data._sys_identity()["engine"] is None
