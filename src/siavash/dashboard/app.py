"""Read-only monitoring dashboard + ``/health`` on one port (Starlette, server-rendered Jinja2).

- Every route is GET-only; nothing writes files, the store or config, and there is no
  order / pause / resume control. SQLite is opened with ``mode=ro``.
- ``/health`` is public (platform healthcheck): 200 while ``state/heartbeat`` is younger than
  ``loop.watchdog_timeout_s``, else 503.
- Pages never answer 500: a failing section renders an inline "data unavailable" box, and a failure
  outside any section renders a whole-page box (see ``safe_page``).
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
from collections.abc import Callable
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
from siavash.dashboard.data import IMAGE_TYPES, DashboardData, rewrite_images, unavailable
from siavash.logging import get_logger, register_secrets

log = get_logger(__name__)

DEFAULT_USER = "admin"
CHART_JS = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"
EMPTY_CHART: dict[str, list[Any]] = {"ts": [], "equity": [], "dd": []}
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
            mode=data.engine_mode(),  # the engine's real mode (state/engine.json), not base.yaml
            **ctx,
        )
        return HTMLResponse(html)

    def safe_page(
        request: Request, name: str, build: Callable[[], dict[str, Any] | Response], refresh_page: bool = True
    ) -> Response:
        """Load + render one page; any failure renders an inline "data unavailable" page (200), never a 500."""
        try:
            ctx = build()
            return ctx if isinstance(ctx, Response) else page(request, name, refresh_page, **ctx)
        except Exception as e:
            log.warning("dashboard_page_failed", path=request.url.path, error=f"{type(e).__name__}: {e}")
            return page(request, "unavailable.html", refresh_page, err=unavailable("page", e))

    async def health(request: Request) -> JSONResponse:
        ok, body = data.health()
        return JSONResponse(body, status_code=200 if ok else 503)

    async def overview(request: Request) -> Response:
        def build() -> dict[str, Any]:
            o = data.overview()
            return {"o": o, "chart_data": json.dumps(o["equity"].get("chart") or EMPTY_CHART)}

        return safe_page(request, "overview.html", build)

    async def opportunities(request: Request) -> Response:
        q = request.query_params
        f = {k: q.get(k, "") for k in ("symbol", "strategy", "outcome")}
        return safe_page(request, "opportunities.html", lambda: {"r": data.opportunities(**f), "f": f})

    async def decision(request: Request) -> Response:
        def build() -> dict[str, Any] | Response:
            rec = data.decision(request.path_params["decision_id"])
            if rec is None:
                return PlainTextResponse("decision not found", 404)
            return {"rec": rec, "pretty": json.dumps(rec, ensure_ascii=False, indent=2, default=str)}

        return safe_page(request, "decision.html", build, refresh_page=False)

    async def positions(request: Request) -> Response:
        return safe_page(request, "positions.html", lambda: {"p": data.positions()})

    async def ai(request: Request) -> Response:
        return safe_page(request, "ai.html", lambda: {"a": data.ai()})

    async def reports(request: Request) -> Response:
        return safe_page(request, "reports.html", lambda: {"groups": data.report_list()}, refresh_page=False)

    async def report(request: Request) -> Response:
        rel = request.path_params["path"]

        def build() -> dict[str, Any] | Response:
            p = data.safe_report_path(rel, {".md"})
            if p is None:
                return PlainTextResponse("report not found", 404)
            body = rewrite_images(md.render(p.read_text(encoding="utf-8", errors="replace")), rel)
            return {"rel": rel, "body": Markup(body)}

        return safe_page(request, "report.html", build, refresh_page=False)

    async def report_file(request: Request) -> Response:
        p = data.safe_report_path(request.path_params["path"], set(IMAGE_TYPES))
        if p is None:
            return PlainTextResponse("file not found", 404)
        return FileResponse(p, media_type=IMAGE_TYPES[p.suffix.lower()])

    async def system(request: Request) -> Response:
        return safe_page(request, "system.html", lambda: {"s": data.system()})

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
