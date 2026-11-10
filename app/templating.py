"""The shared Jinja2 templates object.

It lives in its own module so routers can import it without importing app.main
(which imports the routers — that would be a circular import).
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Autoescape is on for .html files, so user and AI text is always HTML-escaped (SPEC §10.1).
templates = Jinja2Templates(directory=TEMPLATES_DIR)
