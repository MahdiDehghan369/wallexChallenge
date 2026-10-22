"""Read-only monitoring dashboard + ``/health`` on one port (Starlette, server-rendered Jinja2).

- Every route is GET-only; nothing writes files, the store or config, and there is no
  order / pause / resume control. SQLite is opened with ``mode=ro``.
- ``/health`` is public (platform healthcheck): 200 while ``state/heartbeat`` is younger than
  ``loop.watchdog_timeout_s``, else 503.
- Every other path needs HTTP Basic auth (``DASHBOARD_USER`` / ``DASHBOARD_PASSWORD``). With no
  password set the dashboard is disabled (503) and only ``/health`` works.
- Bind address and port: ``base.yaml > dashboard`` overridden by env ``DASHBOARD_HOST`` / ``PORT``.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import threading
from importlib import resources
from typing import Any

import uvicorn
from jinja2 import Environment, PackageLoader, select_autoescape
from markdown_it import MarkdownIt
from markupsafe import Markup
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from siavash.config import AppConfig, EnvSettings
from siavash.dashboard.data import IMAGE_TYPES, DashboardData, rewrite_images
from siavash.logging import get_logger, register_secrets

log = get_logger(__name__)

DEFAULT_USER = "admin"
CHART_JS = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
CSP = (
    "default-src 'none'; script-src 'self' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
NAV = [
    ("/", "نمای کلی"),
    ("/opportunities", "فرصت‌ها"),
    ("/positions", "پوزیشن‌ها و معاملات"),
    ("/ai", "هوش مصنوعی"),
    ("/reports", "گزارش‌ها"),
    ("/system", "سیستم"),
]


def dashboard_env(env_file: str | None = ".env") -> EnvSettings:
    from pathlib import Path

    p = Path(env_file) if env_file else None
    return EnvSettings(_env_file=p if p and p.exists() else None)  # type: ignore[call-arg]


def bind_address(cfg: AppConfig, env: EnvSettings) -> tuple[str, int]:
    return env.dashboard_host or cfg.base.dashboard.host, env.port or cfg.base.dashboard.port


class BasicAuth(BaseHTTPMiddleware):
    def __init__(self, app: Any, user: str, password: str | None) -> None:
        super().__init__(app)
        self.user = user.encode()
        self.password = password.encode() if password else None

    def _authorized(self, header: str) -> bool:
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "basic" or not token or self.password is None:
            return False
        try:
            user, _, pw = base64.b64decode(token, validate=True).partition(b":")
        except (binascii.Error, ValueError):
            return False
        # evaluate both comparisons (constant time, no short-circuit on the user name)
        ok_user = hmac.compare_digest(user, self.user)
        ok_pw = hmac.compare_digest(pw, self.password)
        return ok_user and ok_pw

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/health":
            response = await call_next(request)
        elif self.password is None:
            response = PlainTextResponse("dashboard disabled: DASHBOARD_PASSWORD is not set", 503)
        elif not self._authorized(request.headers.get("authorization", "")):
            response = PlainTextResponse(
                "authentication required", 401, headers={"WWW-Authenticate": 'Basic realm="siavash"'}
            )
        else:
            response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


def _fmt(v: Any, nd: int = 0) -> str:
    if v is None or v == "":
        return "—"
    if isinstance(v, bool):
        return "بله" if v else "خیر"
    if isinstance(v, int | float):
        return f"{v:,.{nd}f}"
    return str(v)


def create_app(cfg: AppConfig, env: EnvSettings | None = None) -> Starlette:
    env = env if env is not None else dashboard_env()
    password = env.dashboard_password.get_secret_value() if env.dashboard_password else None
    if password:
        register_secrets([password])
    data = DashboardData(cfg)
    jinja = Environment(
        loader=PackageLoader("siavash.dashboard", "templates"), autoescape=select_autoescape(["html"])
    )
    jinja.filters["fmt"] = _fmt
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    refresh = cfg.base.dashboard.refresh_s

    def page(request: Request, name: str, refresh_page: bool = True, **ctx: Any) -> HTMLResponse:
        html = jinja.get_template(name).render(
            nav=NAV,
            path=request.url.path,
            refresh_s=refresh if refresh_page else None,
            chart_js=CHART_JS,
            mode=cfg.mode,
            **ctx,
        )
        return HTMLResponse(html)

    async def health(request: Request) -> JSONResponse:
        ok, body = data.health()
        return JSONResponse(body, status_code=200 if ok else 503)

    async def overview(request: Request) -> HTMLResponse:
        o = data.overview()
        return page(request, "overview.html", o=o, chart_data=json.dumps(o["chart"]))

    async def opportunities(request: Request) -> HTMLResponse:
        q = request.query_params
        f = {k: q.get(k, "") for k in ("symbol", "strategy", "outcome")}
        return page(request, "opportunities.html", r=data.opportunities(**f), f=f)

    async def decision(request: Request) -> Response:
        rec = data.decision(request.path_params["decision_id"])
        if rec is None:
            return PlainTextResponse("decision not found", 404)
        pretty = json.dumps(rec, ensure_ascii=False, indent=2)
        return page(request, "decision.html", refresh_page=False, rec=rec, pretty=pretty)

    async def positions(request: Request) -> HTMLResponse:
        return page(request, "positions.html", p=data.positions())

    async def ai(request: Request) -> HTMLResponse:
        return page(request, "ai.html", a=data.ai())

    async def reports(request: Request) -> HTMLResponse:
        return page(request, "reports.html", refresh_page=False, groups=data.report_list())

    async def report(request: Request) -> Response:
        rel = request.path_params["path"]
        p = data.safe_report_path(rel, {".md"})
        if p is None:
            return PlainTextResponse("report not found", 404)
        body = rewrite_images(md.render(p.read_text(encoding="utf-8", errors="replace")), rel)
        return page(request, "report.html", refresh_page=False, rel=rel, body=Markup(body))

    async def report_file(request: Request) -> Response:
        p = data.safe_report_path(request.path_params["path"], set(IMAGE_TYPES))
        if p is None:
            return PlainTextResponse("file not found", 404)
        return FileResponse(p, media_type=IMAGE_TYPES[p.suffix.lower()])

    async def system(request: Request) -> HTMLResponse:
        return page(request, "system.html", s=data.system())

    async def script(request: Request) -> Response:
        js = resources.files("siavash.dashboard").joinpath("static/dashboard.js").read_text(encoding="utf-8")
        return Response(js, media_type="application/javascript")

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/", overview, methods=["GET"]),
        Route("/opportunities", opportunities, methods=["GET"]),
        Route("/opportunities/{decision_id}", decision, methods=["GET"]),
        Route("/positions", positions, methods=["GET"]),
        Route("/ai", ai, methods=["GET"]),
        Route("/reports", reports, methods=["GET"]),
        Route("/reports/view/{path:path}", report, methods=["GET"]),
        Route("/reports/file/{path:path}", report_file, methods=["GET"]),
        Route("/system", system, methods=["GET"]),
        Route("/static/dashboard.js", script, methods=["GET"]),
    ]
    user = env.dashboard_user or DEFAULT_USER
    return Starlette(routes=routes, middleware=[Middleware(BasicAuth, user=user, password=password)])


def _server(cfg: AppConfig, env: EnvSettings) -> uvicorn.Server:
    host, port = bind_address(cfg, env)
    if not env.dashboard_password:
        log.warning("dashboard_disabled", reason="DASHBOARD_PASSWORD not set; only /health is served")
    log.info("dashboard_listening", host=host, port=port)
    config = uvicorn.Config(
        create_app(cfg, env), host=host, port=port, log_config=None, access_log=False, log_level="warning"
    )
    return uvicorn.Server(config)


def serve(cfg: AppConfig, env: EnvSettings | None = None) -> None:
    """Blocking server (``siavash dashboard`` / ``make dashboard-serve``)."""
    _server(cfg, env if env is not None else dashboard_env()).run()


def start_in_thread(cfg: AppConfig, env: EnvSettings | None = None) -> threading.Thread:
    """Daemon thread next to the engine (``siavash run`` with env ``PORT``); no signal handlers there."""
    server = _server(cfg, env if env is not None else dashboard_env())
    t = threading.Thread(target=server.run, name="dashboard", daemon=True)
    t.start()
    return t
