"""FastAPI app: middleware, static files and router registration.

Run locally with: uv run fastapi dev app/main.py
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from app import scheduler
from app.config import Settings, get_settings
from app.routers import (
    applications,
    auth,
    cvs,
    learning,
    legal,
    notifications,
    placeholders,
    profile,
    undo,
)
from app.routers import settings as settings_routes  # `settings` is the app config below
from app.security import hide_tokens_in_access_log, limiter, verify_csrf
from app.services.auth_service import LoginRequired, login_required_response
from app.templating import templates

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


def rate_limit_response(request: Request, exc: RateLimitExceeded):
    """Too many requests from one IP address (slowapi limits in app/routers/auth.py)."""
    # exc.detail is the limit that was hit, e.g. "10 per 1 minute" (login) or
    # "5 per 1 hour" (register, forgot password). Every try counts, also the ones
    # with a form error, so for the hourly limits the wait can be up to an hour.
    if "hour" in exc.detail:
        text = "Please try again in an hour."
    else:
        text = "Please wait a few minutes and try again."
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
    app.include_router(placeholders.router)
    app.include_router(settings_routes.router)
    app.include_router(undo.router)
    app.include_router(profile.router)
    app.include_router(cvs.router)
    app.include_router(legal.router)
    return app


app = create_app(get_settings())
