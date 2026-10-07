"""HTTP adapter: local authentication, role-scoped pages and JSON operations."""

from collections import defaultdict, deque
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import secrets
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool
import psycopg

from app.auth import csrf_token, valid_csrf, verify_password, hash_password
from app.config import Settings
from app.db import Database, ActorDatabase, database_error
from app.errors import DomainError
from app.repository import Repository, require_role

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("matcher")
ALLOWED_ACTIONS = {
    "employee.create", "employee.update", "employee.skills", "calendar.capacity", "calendar.unavailability", "calendar.remove",
    "project.create", "project.update", "project.transition", "position.create", "position.update", "invitation.create",
    "invitation.respond", "invitation.cancel", "terms.replace", "assignment.complete", "assignment.cancel",
    "review.create", "review.update", "competency.create", "competency.update",
}


def create_app(settings: Settings) -> FastAPI:
    """Build one independently authenticated regional application.

    :param settings: Validated configuration with a single local DSN.
    :return: ASGI application suitable for Uvicorn or integration tests.
    """
    app = FastAPI(title="Cross-Team Matcher", version="4.1.0", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.database = Database(settings.database_url)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie=f"ctm_session_r{settings.node_region}",
                       max_age=8 * 60 * 60, same_site="lax", https_only=settings.secure_cookies)
    app.mount("/static", StaticFiles(directory=ROOT / "app" / "static", check_dir=False), name="static")
    templates = Jinja2Templates(directory=ROOT / "app" / "templates")
    attempts: dict[str, deque] = defaultdict(deque)
    dummy_hash = hash_password(secrets.token_urlsafe(20))

    @app.middleware("http")
    async def headers(request: Request, call_next):
        request.state.csp_nonce = secrets.token_urlsafe(20)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; font-src 'self'; "
            f"script-src 'self' 'nonce-{request.state.csp_nonce}'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def error_response(request: Request, exc: DomainError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)
        if exc.code == "unauthorized":
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(f'<html lang="ru"><meta charset="utf-8"><title>Cross-Team</title><body><h1>{str(exc)}</h1><p><a href="/">Вернуться в рабочее пространство</a></p></body></html>', status_code=exc.status)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error_response(request, exc)

    @app.exception_handler(psycopg.Error)
    async def sql_error(request, exc):
        LOG.warning("Database request rejected: SQLSTATE=%s", exc.sqlstate)
        return error_response(request, database_error(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error_response(request, DomainError("payload_invalid", 422))

    def account(identifier: str) -> dict | None:
        with app.state.database.connect() as conn:
            row = conn.execute("""SELECT a.account_id,a.owner_region,a.employee_id,a.login,
                COALESCE(array_agg(DISTINCT r.role_code) FILTER(WHERE r.role_code IS NOT NULL),'{}') AS roles
                FROM private.accounts a LEFT JOIN private.account_roles r USING(account_id)
                WHERE a.account_id=%s AND a.active GROUP BY a.account_id""", (identifier,)).fetchone()
            if row:
                row["account_id"] = str(row["account_id"])
                row["employee_id"] = str(row["employee_id"]) if row["employee_id"] else None
            return row

    def user(request: Request) -> dict:
        identifier = request.session.get("account_id")
        if not identifier:
            raise DomainError("unauthorized", 401)
        found = account(identifier)
        if not found or found["owner_region"] != settings.node_region:
            request.session.clear()
            raise DomainError("unauthorized", 401)
        return found

    def repository(request: Request) -> Repository:
        current = user(request)
        return Repository(ActorDatabase(settings.database_url, current["account_id"]), current, settings.node_region)

    def context(request: Request, current: dict | None = None, page: str = "dashboard", error: str | None = None) -> dict:
        token = csrf_token(request.session)
        return {"request": request, "user": current, "node_region": settings.node_region,
                "csrf_token": token, "page": page, "error": error, "csp_nonce": request.state.csp_nonce,
                "config": {"user": current, "nodeRegion": settings.node_region, "csrfToken": token, "page": page}}

    @app.get("/health")
    def health():
        with app.state.database.connect() as conn:
            region = conn.execute("SELECT private.node_region() AS region").fetchone()["region"]
        if region != settings.node_region:
            return JSONResponse({"status": "misconfigured"}, status_code=503)
        return {"status": "ok", "region": region, "version": "4.1.0"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return templates.TemplateResponse(request=request, name="login.html", context=context(request))

    def authenticate(login: str, password: str) -> dict | None:
        with app.state.database.connect() as conn:
            row = conn.execute("SELECT account_id,password_hash,active FROM private.accounts WHERE login=%s", (login,)).fetchone()
        valid = verify_password(password, row["password_hash"] if row else dummy_hash)
        return account(str(row["account_id"])) if row and row["active"] and valid else None

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        if not valid_csrf(request.session, form.get("csrf_token")):
            raise DomainError("csrf_invalid", 403)
        login_name, password = str(form.get("login", "")).strip()[:100], str(form.get("password", ""))
        if len(password) > 1024:
            raise DomainError("payload_invalid", 422)
        key = f"{request.client.host if request.client else 'local'}:{login_name}"
        recent = attempts[key]
        now = time.monotonic()
        while recent and recent[0] < now - 60:
            recent.popleft()
        if len(recent) >= 8:
            return templates.TemplateResponse(request=request, name="login.html", context=context(request, error="Слишком много попыток. Подождите минуту."), status_code=429)
        current = await run_in_threadpool(authenticate, login_name, password)
        if not current or current["owner_region"] != settings.node_region:
            recent.append(now)
            if len(attempts) > 5000:
                attempts.clear()
            return templates.TemplateResponse(request=request, name="login.html", context=context(request, error="Неверный логин или пароль для этого региона."), status_code=401)
        attempts.pop(key, None)
        request.session.clear()
        request.session["account_id"] = current["account_id"]
        csrf_token(request.session)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        form = await request.form()
        if not valid_csrf(request.session, form.get("csrf_token")):
            raise DomainError("csrf_invalid", 403)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/api/me")
    def me(request: Request):
        return {"user": user(request), "node_region": settings.node_region, "csrf_token": csrf_token(request.session)}

    @app.get("/api/docs", include_in_schema=False)
    def api_documentation():
        return RedirectResponse("/documentation/operations.html", status_code=302)

    @app.get("/api/employees/{owner}/{identifier}")
    def employee_detail(request: Request, owner: int, identifier: UUID):
        return repository(request).employee(owner, identifier)

    @app.get("/api/projects/{owner}/{identifier}")
    def project_detail(request: Request, owner: int, identifier: UUID):
        return repository(request).project(owner, identifier)

    @app.get("/api/search")
    def search(request: Request, region_mode: str | None = None, competencies: str = "", experience_months: int = Query(0, ge=0, le=1200),
               date_from: date | None = None, date_to: date | None = None, hours_per_week: float = Query(8, gt=0, le=40),
               page: int = Query(1, ge=1, le=10000), query: str = Query("", max_length=100), position_id: UUID | None = None, position_owner: int | None = None):
        date_from = date_from or date.today()
        date_to = date_to or date_from + timedelta(days=30)
        if date_to < date_from or (date_to-date_from).days > 1095 or hours_per_week * 2 != int(hours_per_week * 2):
            raise DomainError("payload_invalid", 422)
        try:
            skills = [UUID(value.strip()) for value in competencies.split(",") if value.strip()]
        except ValueError:
            raise DomainError("payload_invalid", 422) from None
        if len(skills) > 150:
            raise DomainError("payload_invalid", 422)
        return repository(request).search({"region_mode": region_mode, "competencies": skills, "experience_months": experience_months,
            "date_from": date_from, "date_to": date_to, "hours_per_week": hours_per_week, "page": page, "query": query,
            "position_id": position_id, "position_owner": position_owner})

    @app.get("/api/{resource}")
    def collection(request: Request, resource: str, query: str = ""):
        repo = repository(request)
        if resource == "employees":
            return repo.employees(query)
        readers = {"dashboard": repo.dashboard, "projects": repo.projects, "invitations": repo.invitations,
                   "assignments": repo.assignments, "reviews": repo.reviews, "catalog": repo.catalog,
                   "analytics": repo.analytics, "events": repo.events, "freshness": repo.freshness}
        if resource not in readers:
            raise DomainError("not_found", 404)
        return readers[resource]()

    @app.post("/api/actions/{action}")
    async def mutate(request: Request, action: str):
        current = await run_in_threadpool(user, request)
        if not valid_csrf(request.session, request.headers.get("X-CSRF-Token")):
            raise DomainError("csrf_invalid", 403)
        raw = await request.body()
        if len(raw) > 65536:
            raise DomainError("payload_invalid", 413)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise DomainError("payload_invalid", 422) from None
        if action not in ALLOWED_ACTIONS or not isinstance(payload, dict) or any(k in payload for k in ("actor_id", "source_region", "target_region", "owner_region", "manager_id", "author_id")):
            raise DomainError("forbidden", 403)
        payload.setdefault("command_id", str(uuid4()))
        try:
            UUID(str(payload["command_id"]))
        except ValueError:
            raise DomainError("payload_invalid", 422) from None
        db = ActorDatabase(settings.database_url, current["account_id"])
        return await run_in_threadpool(db.mutate, action, payload)

    @app.get("/docs", include_in_schema=False)
    def documentation():
        return RedirectResponse("/documentation/index.html", status_code=302)

    @app.get("/documentation/{path:path}", include_in_schema=False)
    def documentation_file(path: str):
        base = (ROOT / "docs" / "_build" / "html").resolve()
        target = (base / path).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return HTMLResponse('<html lang="ru"><meta charset="utf-8"><h1>Документация</h1><p>Соберите документацию командой <code>python scripts/build_docs.py</code>.</p><p><a href="/">Вернуться</a></p></html>', status_code=404)
        return FileResponse(target)

    @app.get("/", response_class=HTMLResponse)
    @app.get("/{page}", response_class=HTMLResponse)
    @app.get("/{page}/{owner}/{identifier}", response_class=HTMLResponse)
    def workspace(request: Request, page: str = "dashboard", owner: int | None = None, identifier: UUID | None = None):
        current = user(request)
        pages = {"dashboard": (), "employees": ("EMPLOYEE", "EDITOR", "MANAGER", "ANALYST"),
                 "projects": ("MANAGER", "ANALYST"), "search": ("MANAGER",), "invitations": ("EMPLOYEE",),
                 "assignments": ("EMPLOYEE", "MANAGER", "ANALYST"), "reviews": ("EMPLOYEE", "MANAGER", "ANALYST"),
                 "catalog": ("CATALOG_ADMIN",), "analytics": ("ANALYST",), "events": ("EDITOR", "MANAGER", "ANALYST")}
        if page not in pages:
            raise DomainError("not_found", 404)
        if pages[page]:
            require_role(current, *pages[page])
        if page in ("catalog", "analytics") and settings.node_region != 0:
            raise DomainError("forbidden", 403)
        return templates.TemplateResponse(request=request, name="app.html", context=context(request, current, page))

    return app


app = create_app(Settings.from_env())
