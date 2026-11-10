"""FastAPI app: static files and router registration.

Run locally with: uv run fastapi dev app/main.py
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import applications

settings = get_settings()

app = FastAPI(title="Unfurl", debug=settings.debug)

# CSS, JS, fonts and images are served from our own origin (no CDNs).
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(applications.router)
