"""FastAPI app: middleware, static files and router registration.

Run locally with: uv run fastapi dev app/main.py
"""

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.routers import applications, auth, legal, placeholders
from app.security import verify_csrf
from app.services.auth_service import LoginRequired, login_required_response

SESSION_COOKIE = "unfurl_session"
SESSION_MAX_AGE = 14 * 24 * 60 * 60  # 14 days, in seconds (SPEC §6.1)


def create_app(settings: Settings) -> FastAPI:
    """Build the app for the given settings (a function, so tests can build a production app)."""
    # The API docs pages (/docs, /redoc, /openapi.json) list every route and its
    # schema. Useful while developing, but they are not published in production.
    show_api_docs = settings.app_env == "development"

    app = FastAPI(
        title="Unfurl",
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
        session_cookie=SESSION_COOKIE,
        max_age=SESSION_MAX_AGE,
        same_site="lax",
        https_only=settings.app_env == "production",
    )

    # Pages that need a login raise LoginRequired; this turns it into a redirect.
    app.add_exception_handler(LoginRequired, login_required_response)

    # CSS, JS, fonts and images are served from our own origin (no CDNs).
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    app.include_router(auth.router)
    app.include_router(applications.router)
    app.include_router(placeholders.router)
    app.include_router(legal.router)
    return app


app = create_app(get_settings())
