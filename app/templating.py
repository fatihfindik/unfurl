"""The shared Jinja2 templates object.

It lives in its own module so routers can import it without importing app.main
(which imports the routers — that would be a circular import).
"""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.security import safe_redirect_path

TEMPLATES_DIR = Path(__file__).parent / "templates"

THEMES = ("light", "dark", "system")
DEFAULT_THEME = "system"


def theme_context(request: Request) -> dict:
    """Add `theme` to every template, read from the `theme` cookie.

    The theme class is put on <html> by the server, so the page never flashes
    in the wrong colours while loading (SPEC §6.0). Until users exist (M1) the
    choice lives in a cookie; in M4 it moves to User.theme.
    Unknown values fall back to "system", so the cookie can't inject HTML/CSS.
    """
    theme = request.cookies.get("theme")
    if theme not in THEMES:
        theme = DEFAULT_THEME
    return {"theme": theme}


# Autoescape is on for .html files, so user and AI text is always HTML-escaped (SPEC §10.1).
# Context processors run for every template response and add shared variables.
templates = Jinja2Templates(directory=TEMPLATES_DIR, context_processors=[theme_context])

# Functions templates can call. safe_redirect_path keeps back_url links on this site.
templates.env.globals["safe_redirect_path"] = safe_redirect_path
