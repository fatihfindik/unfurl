"""FastAPI app: static files and router registration.

Run locally with: uv run fastapi dev app/main.py
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.routers import applications, placeholders


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
    )

    # CSS, JS, fonts and images are served from our own origin (no CDNs).
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

    app.include_router(applications.router)
    app.include_router(placeholders.router)
    return app


app = create_app(get_settings())
