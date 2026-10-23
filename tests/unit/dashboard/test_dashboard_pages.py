"""Dashboard UI: every page renders on the demo dataset and on an empty install (structure snapshots)."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from starlette.testclient import TestClient

from siavash.config import EnvSettings
from siavash.dashboard import app as dash_app
from siavash.dashboard.app import create_app
from siavash.dashboard.demo import build_demo, demo_config

PASSWORD = "demo-pass-91ab"
UNAVAILABLE = "داده در دسترس نیست"
NOW = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
PAGES = ["/", "/opportunities", "/positions", "/ai", "/reports", "/system"]


def _env() -> EnvSettings:
    return EnvSettings(_env_file=None, dashboard_user="u", dashboard_password=SecretStr(PASSWORD))  # type: ignore[call-arg]


AUTH = {"Authorization": "Basic " + base64.b64encode(f"u:{PASSWORD}".encode()).decode()}


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    root = tmp_path_factory.mktemp("demo")
    return TestClient(create_app(build_demo(root, now=NOW), _env()))


@pytest.fixture()
def empty(tmp_path: Path) -> TestClient:
    return TestClient(create_app(demo_config(tmp_path), _env()), raise_server_exceptions=False)


def _get(c: TestClient, path: str) -> str:
    r = c.get(path, headers=AUTH)
    assert r.status_code == 200, (path, r.text[:300])
    return r.text


# ---------------------------------------------------------------- shell (every page)


def _all_pages(c: TestClient) -> list[str]:
    return [
        *PAGES,
        "/opportunities?outcome=REJECTED&strategy=S1_TREND_BREAKOUT",
        "/opportunities/01JDEMO0000000000000000000",
        "/reports/view/live/REPORT.md",
        "/reports/view/research_v2/SUMMARY.md",
    ]


def test_every_page_renders_with_the_shell(demo: TestClient) -> None:
    for path in _all_pages(demo):
        t = _get(demo, path)
        assert UNAVAILABLE not in t, path
        assert 'dir="rtl"' in t and "Vazirmatn" in t and 'class="side"' in t and 'id="top"' in t, path
        assert t.count('class="ic"') >= 6 and "<svg" in t, path  # sidebar icons are inline SVG
        for href, label, _ in dash_app.NAV:
            assert f'href="{href}"' in t and label in t, (path, href)
        assert t.count('aria-current="page"') == 1, path
        assert "حالت: paper" in t and 'class="pill mode paper"' in t, path
        assert "data-countdown" in t and 'data-end="2026-10-26T19:30:00Z"' in t, path
        assert "demo · demo123" in t and 'id="refreshed"' in t and 'id="theme"' in t, path


def test_read_only_markup_and_pinned_assets(demo: TestClient) -> None:
    for path in _all_pages(demo):
        t = _get(demo, path)
        assert "<form" not in t and 'method="post"' not in t.lower(), path
        # the only buttons: theme toggle + drawer close (no trade / pause / config controls)
        buttons = re.findall(r'<button[^>]*id="([^"]+)"', t)
        assert sorted(buttons) == ["drawer-close", "theme"], (path, buttons)
        external = set(re.findall(r'(?:src|href)="(https?://[^"]+)"', t))
        for url in external:
            assert url.startswith(
                ("https://fonts.googleapis.com", "https://fonts.gstatic.com", dash_app.CHART_JS)
            ), url
    js = _get(demo, "/static/dashboard.js")
    assert "POST" not in js and "method" not in js  # fetch() is GET only
    assert "innerHTML" not in js  # JSON colouring builds DOM nodes


def test_no_script_fallback_keeps_meta_refresh(demo: TestClient) -> None:
    t = _get(demo, "/")
    assert '<noscript><meta http-equiv="refresh" content="30"></noscript>' in t and 'data-refresh="30"' in t
    assert 'data-refresh="' not in _get(demo, "/reports/view/live/REPORT.md")  # reports do not auto-refresh


# ---------------------------------------------------------------- page snapshots (structure)


def test_overview_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/")
    assert t.count('class="card kpi"') == 6  # equity, net return, drawdown, exposure, trades today, fees
    for label in (
        "ارزش حساب",
        "بازده خالص",
        "افت از اوج",
        "در معرض بازار",
        "معاملات امروز",
        "کارمزد پرداختی",
    ):
        assert label in t, label
    assert t.count('<svg class="spark"') == 2 and 'id="charts"' in t and 'class="chart skeleton"' in t
    assert re.search(r'class="v (up|down|flat)">[+-]?\d+\.\d\d %<', t)  # signed net return, 2 decimals
    assert (
        "فاز endgame" in t and 'class="tl"' in t and t.count('<span class="dotc') == 8
    )  # base.yaml timeline: 8
    assert "/opportunities/01JDEMO" in t


def test_opportunities_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/opportunities")
    assert t.count('class="click"') == 36 and 'tabindex="0"' in t and "<thead>" in t
    assert 'class="badge good"' in t and "ORDER_PLACED" in t and "REJECTED" in t
    for chip in ("FEE", "ML", "EV", "LLM", "RISK"):
        assert f">{chip}</span>" in t, chip
    assert (
        'class="pw"' in t
        and 'class="vd good"' in t
        and 'class="vd bad"' in t
        and 'class="chip warn">timeout' in t
    )
    assert 'class="trunc"' in t and 'class="fp on"' in t and "<select" not in t
    f = _get(demo, "/opportunities?outcome=REJECTED")
    assert (
        'href="/opportunities?strategy=S1_TREND_BREAKOUT&amp;outcome=REJECTED"' in f
    )  # pills keep other filters
    assert "ORDER_PLACED</span>" not in f.split("<tbody>")[1]
    d = _get(demo, "/opportunities/01JDEMO0000000000000000000")
    assert '<pre class="json" id="json">' in d and 'id="decision-title"' in d and "&#34;p_win&#34;" in d


def test_positions_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/positions")
    assert t.count('class="card pcard"') == 2 and t.count('class="range"') == 2
    assert t.count('class="mk px"') == 2 and t.count('class="mk tr"') == 1  # ETH has no trailing stop
    assert "<tfoot>" in t and "4</span> معامله" in t
    assert re.search(r'class="n (up|down)">[+-][\d,]+</td>', t)  # signed, coloured PnL


def test_ai_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/ai")
    assert 'class="donut"' in t and "fallback" in t and t.count('<div class="row">') == 3
    assert t.count('<span class="seg-') == 40 and "TREND_UP" in t and "PANIC" in t
    assert "<b>64</b> / 200" in t and "131,000" in t  # budget used vs llm.budget limits
    assert "reports/live/metrics.json" in t and "+1.40 %" in t and "-0.30 %" in t


def test_reports_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/reports")
    assert t.count('class="card rcard"') == 4 and "مسابقه (live)" in t and "پژوهش پیش از مسابقه" in t
    assert t.index("live/REPORT.md") < t.index("research_v2/SUMMARY.md")
    r = _get(demo, "/reports/view/live/REPORT.md")
    assert (
        '<article class="md" dir="ltr">' in r and "<table>" in r and "<pre><code" in r and "<blockquote>" in r
    )


def test_system_snapshot(demo: TestClient) -> None:
    t = _get(demo, "/system")
    assert t.count('class="card tile') == 5 and t.count('class="card step') == 4
    assert 'class="card tile good"' in t  # fresh heartbeat
    assert "2.50 %" in t and 'class="card tile warn"' in t  # 10 of 400 http_request errors: amber at >= 1 %
    assert '<b class="ltr">' in t and "demo123" in t and "0.250" in t
    assert "2026-10-26 23:00" in t and "2026-10-26 19:30" in t  # end: Tehran + UTC


# ---------------------------------------------------------------- empty install


EMPTY_MESSAGES = {
    "/": ["هنوز منحنی ارزش حسابی ثبت نشده", "هنوز تصمیمی ثبت نشده"],
    "/opportunities": ["هنوز فرصتی ثبت نشده"],
    "/positions": ["پوزیشن بازی نیست", "هنوز معامله‌ای انجام نشده"],
    "/ai": ["هنوز رژیمی ثبت نشده", "هنوز گزارشی با بخش counterfactual نیست"],
    "/reports": ["هنوز گزارشی ساخته نشده"],
    "/system": ["هنوز داده تأخیری ثبت نشده"],
}


@pytest.mark.parametrize("path", PAGES)
def test_empty_install_shows_friendly_states(empty: TestClient, path: str) -> None:
    t = _get(empty, path)
    for msg in EMPTY_MESSAGES[path]:
        assert msg in t and 'class="empty"' in t, (path, msg)


def test_every_dashboard_asset_is_package_data() -> None:
    # the image does `pip install .`: a template or static file not matched by package-data is missing there
    import tomllib

    root = Path(__file__).resolve().parents[3]
    patterns = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"][
        "package-data"
    ]["siavash"]
    pkg = root / "src" / "siavash"
    for f in [*(pkg / "dashboard" / "templates").iterdir(), *(pkg / "dashboard" / "static").iterdir()]:
        rel = f.relative_to(pkg).as_posix()
        assert any(Path(rel).match(p) for p in patterns), rel


# ---------------------------------------------------------------- formatting helpers


def test_number_formatting() -> None:
    assert dash_app._signed(1234.4) == "+1,234" and dash_app._signed(-2000) == "-2,000"
    assert dash_app._signed(0.4) == "0" and dash_app._signed(None) == "—" and dash_app._signed(True) == "—"
    assert (
        dash_app._pct(2.4691) == "+2.47 %"
        and dash_app._pct(-0.005) == "-0.01 %"
        and dash_app._pct(0) == "0.00 %"
    )
    assert dash_app._tone(-1) == "down" and dash_app._tone(None) == "flat"
    assert dash_app._countdown(50.5) == "2d 02:30:00" and dash_app._countdown(-1) == "00:00:00"
    assert dash_app._spark([1]) == "" and dash_app._spark([1, None, 3], 10, 10).split() == [
        "0.0,8.0",
        "10.0,2.0",
    ]
    f = {"symbol": "BTC/TMN", "strategy": "", "outcome": "REJECTED"}
    assert dash_app._qs(f, outcome="") == "?symbol=BTC%2FTMN" and dash_app._qs({}, symbol="") == ""
