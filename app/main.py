"""FastAPI app: middleware, static files and router registration.

Run locally with: uv run fastapi dev app/main.py
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.datastructures import Headers
from starlette.middleware.sessions import SessionMiddleware

from app import scheduler
from app.config import Settings, get_settings
from app.errors import add_error_handlers, api_error, error_response, wants_json
from app.routers import (
    applications,
    auth,
    cvs,
    ext_api,
    learning,
    legal,
    notifications,
    profile,
    reminders,
    undo,
)
from app.routers import settings as settings_routes  # `settings` is the app config below
from app.security import (
    CONTENT_SECURITY_POLICY,  # noqa: F401 (tests read it from here)
    HEAD_SCOPE_KEY,
    SECURITY_HEADERS,
    hide_tokens_in_access_log,
    limiter,
    verify_csrf,
)
from app.services import clock
from app.services.auth_service import SESSION_TIMEZONE, LoginRequired, login_required_response
from app.templating import is_htmx, templates

SESSION_COOKIE = "unfurl_session"
# In production the cookie name starts with "__Host-": browsers then accept it only
# with Secure, path=/ and no Domain, so a (compromised) subdomain can't set a session
# cookie for us ("cookie tossing"). Locally there is no HTTPS, so the plain name is used.
SESSION_COOKIE_PRODUCTION = "__Host-unfurl_session"
SESSION_MAX_AGE = 14 * 24 * 60 * 60  # 14 days, in seconds (SPEC §6.1)

# GET requests the page sends by itself (the bell's badge every two minutes, its panel).
BACKGROUND_GETS = frozenset({"/notifications/badge", "/notifications/panel"})


class KeepSessionCookie:
    """Drop the Set-Cookie header from the answers to BACKGROUND_GETS.

    The session lives in the cookie, and the session middleware sends it again with
    every answer. A background request that is still on its way when the user signs
    out would then put the old session back, and an open tab would keep a session
    alive for ever. These answers only read, so they don't need to send it.
    A small "ASGI middleware": it sees every request and can change the answer.
    """

    def __init__(self, app, paths: frozenset[str]):
        self.app = app
        self.paths = paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET" or scope["path"] not in self.paths:
            await self.app(scope, receive, send)
            return

        async def send_without_cookie(message):
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"set-cookie"
                ]
            await send(message)

        await self.app(scope, receive, send_without_cookie)


class SecurityHeaders:
    """Add SECURITY_HEADERS to every answer, and `Cache-Control: no-store` to everything
    but /static/: after signing out, the back button doesn't show a cached page with
    the user's data. A header the route set itself is kept (e.g. no-referrer on pages
    with a token in the address)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        wanted = dict(SECURITY_HEADERS)
        if not scope["path"].startswith("/static/"):
            wanted[b"cache-control"] = b"no-store"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in wanted.items() if name not in present]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class HeadAsGet:
    """Answer HEAD like GET, without the body (#5, D-142).

    HEAD asks "what would GET answer?": the same status and headers, no body.
    Uptime monitors and link checkers use it. FastAPI's GET routes don't accept
    HEAD (405), so this layer hands the request on as a GET and drops the body.
    /static/ is left alone: StaticFiles answers HEAD itself.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "HEAD"
            or scope["path"].startswith("/static/")
        ):
            await self.app(scope, receive, send)
            return

        # A copy, not a change in place: the server keeps seeing HEAD in its own
        # scope and drops the body itself for answers that go around this layer
        # (Starlette's 500 page is sent from outside all our middleware).
        # The mark lets a GET route skip a change it would make (is_head_request).
        get_scope = {**scope, "method": "GET", HEAD_SCOPE_KEY: True}

        async def send_without_body(message):
            if message["type"] == "http.response.pathsend":  # a file the server would send
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            elif message["type"] == "http.response.body":
                if message.get("more_body", False):
                    return  # a streamed answer: skip the parts in between
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app(get_scope, receive, send_without_body)


class UserTimezone:
    """Use the logged-in user's time zone for this request (D-109).

    Reads "tz" from the session (written at login and when Settings are saved) and
    puts it into app/services/clock.py's ContextVar. It must run inside the session
    middleware, which fills scope["session"]. Sync routes run on a thread pool, but
    the thread gets a copy of this context, so they see the value too.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with clock.use_timezone(scope.get("session", {}).get(SESSION_TIMEZONE)):
            await self.app(scope, receive, send)


EXTENSION_API = "/api/ext/"
MAX_API_BODY = 256 * 1024  # bytes; a job's text is at most 50,000 characters (D-137)


class ExtensionApiGuard:
    """Two checks for /api/ext/ before any route code runs (D-137):

    - Origin: browsers send it with requests from pages and extensions. If there is
      one, it must be our extension (EXTENSION_ORIGINS); a web page gets 403. The
      Bearer token is still needed after this; this is only a second wall.
    - Body size: at most MAX_API_BODY. FastAPI reads the whole JSON body before the
      token is checked, so without this anyone could make the server read huge bodies.
      The body is read here (up to the limit) and then handed on to the route.
    """

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(EXTENSION_API):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self.settings.extension_origin_list:
            response = api_error(403, message="This page can't use the Unfurl API.")
            await response(scope, receive, send)
            return
        length = headers.get("content-length", "")
        if length and (not length.isdigit() or int(length) > MAX_API_BODY):
            await api_error(413)(scope, receive, send)
            return

        # Content-Length can be missing (chunked) or wrong, so count what arrives too.
        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the client went away
                return
            body += message.get("body", b"")
            if len(body) > MAX_API_BODY:
                await api_error(413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()  # after the body: waits for a disconnect

        await self.app(scope, replay, send)


def rate_limit_response(request: Request, exc: RateLimitExceeded):
    """Too many requests from one IP address (slowapi limits in app/routers/auth.py)."""
    # exc.detail is the limit that was hit, e.g. "10 per 1 minute" (login) or
    # "5 per 1 hour" (register, forgot password). Every try counts, also the ones
    # with a form error, so for the hourly limits the wait can be up to an hour.
    if "hour" in exc.detail:
        text = "Please try again in an hour."
    else:
        text = "Please wait a few minutes and try again."
    if wants_json(request):  # the extension (D-137)
        return api_error(429, message="Too many requests. " + text)
    if is_htmx(request):  # the page stays; a toast says why nothing happened
        return error_response(request, 429, detail="Too many requests. " + text)
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {"heading": "Too many requests", "text": text},
        status_code=429,
    )


def create_app(settings: Settings) -> FastAPI:
    """Build the app for the given settings (a function, so tests can build a production app)."""
    # The API docs pages (/docs, /redoc, /openapi.json) list every route and its
    # schema. Useful while developing, but they are not published in production.
    show_api_docs = settings.app_env == "development"
    is_production = settings.app_env == "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Runs once when the app starts (before `yield`) and once when it stops.
        The notification jobs (app/scheduler.py) run in between, if switched on."""
        running = scheduler.start(settings)
        yield
        scheduler.shutdown(running)

    app = FastAPI(
        title="Unfurl",
        lifespan=lifespan,
        debug=settings.debug,
        docs_url="/docs" if show_api_docs else None,
        redoc_url="/redoc" if show_api_docs else None,
        openapi_url="/openapi.json" if show_api_docs else None,
        # Runs before every route: POST/PUT/PATCH/DELETE need a valid CSRF token.
        dependencies=[Depends(verify_csrf)],
    )

    # Added before the session middleware, so it runs inside it and can read the
    # session (the middleware added last is the outermost one).
    app.add_middleware(UserTimezone)

    # The session lives in a cookie signed with SECRET_KEY: the browser can read it
    # but not change it. HttpOnly is always on (JavaScript can't read the cookie);
    # Secure (HTTPS only) in production. SameSite=Lax: other sites can link to us,
    # but their forms don't get the cookie on POST.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie=SESSION_COOKIE_PRODUCTION if is_production else SESSION_COOKIE,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=is_production,
    )

    # Added after the session middleware, so it wraps it: the bell's polling answers
    # don't rewrite the session cookie (M11a review L1/L2).
    app.add_middleware(KeepSessionCookie, paths=BACKGROUND_GETS)

    # Origin check and body limit for the extension API, before any route (D-137).
    app.add_middleware(ExtensionApiGuard, settings=settings)

    # Added late, so it wraps the rest: every answer gets the headers, errors too.
    app.add_middleware(SecurityHeaders)

    # Outside even that: every layer and route sees a HEAD request as GET (D-142).
    app.add_middleware(HeadAsGet)

    # Our own error pages instead of JSON; 500 too, with the security headers (D-125).
    add_error_handlers(app)

    # Pages that need a login raise LoginRequired; this turns it into a redirect.
    app.add_exception_handler(LoginRequired, login_required_response)

    # Verify and reset tokens are in URLs; keep them out of the access log.
    hide_tokens_in_access_log()

    # Requests per IP address (slowapi reads the limiter from app.state).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_response)

    # CSS, JS, fonts and images are served from our own origin (no CDNs).
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    app.include_router(auth.router)
    app.include_router(applications.router)
    app.include_router(learning.router)
    app.include_router(notifications.router)
    app.include_router(notifications.badge_router)
    app.include_router(settings_routes.router)
    app.include_router(undo.router)
    app.include_router(reminders.router)
    app.include_router(profile.router)
    app.include_router(cvs.router)
    app.include_router(legal.router)
    app.include_router(ext_api.router)
    return app


app = create_app(get_settings())
