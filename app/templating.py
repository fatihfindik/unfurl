"""The shared Jinja2 templates object.

It lives in its own module so routers can import it without importing app.main
(which imports the routers — that would be a circular import).
"""

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.models.base import as_utc, utcnow
from app.security import get_csrf_token, safe_redirect_path

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

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


def request_context(request: Request) -> dict:
    """Add the session's CSRF token (for forms and htmx) and the logged-in user, if any.

    current_user (app/services/auth_service.py) puts the user on request.state.
    """
    return {
        "csrf_token": get_csrf_token(request),
        "current_user": getattr(request.state, "user", None),
    }


# Autoescape is on for .html files, so user and AI text is always HTML-escaped (SPEC §10.1).
# Context processors run for every template response and add shared variables.
templates = Jinja2Templates(
    directory=TEMPLATES_DIR, context_processors=[theme_context, request_context]
)


def month_year(value) -> str:
    """A profile date as CVs show it: 1 March 2021 -> "03/2021" (D-033)."""
    return value.strftime("%m/%Y") if value else ""


def time_ago(value, now=None) -> str:
    """When something happened, the way lists show it: "today", "yesterday",
    "3 days ago", "last week", "2 weeks ago", or the date for anything older."""
    if value is None:
        return ""
    now = now or utcnow()
    days = (now.date() - as_utc(value).date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    if days < 31:
        return f"{days // 7} weeks ago"
    return as_utc(value).strftime("%-d %b %Y")  # 3 Oct 2026


def is_htmx(request: Request) -> bool:
    """True for requests sent by htmx that want a piece of a page, not a whole page.

    The back button is the exception: when htmx has no saved copy of an earlier page
    (hx-history="false"), it asks for it again with HX-History-Restore-Request, and
    then it needs the whole page."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def static_url(path: str) -> str:
    """The address of one of our own files that we change (app.css, app.js), with the
    time it was last changed: "/static/css/app.css?v=1763321656".

    Browsers keep static files for a while. Without the ?v=… part a browser can show
    a new page with the old stylesheet or script; with it, a changed file has a new
    address and is fetched again. `path` is always written in a template, never user input.
    """
    try:
        version = int((STATIC_DIR / path).stat().st_mtime)
    except OSError:  # app.css before the first build
        version = 0
    return f"/static/{path}?v={version}"


# Functions templates can call. safe_redirect_path keeps back_url links on this site.
templates.env.globals["safe_redirect_path"] = safe_redirect_path
templates.env.globals["static_url"] = static_url
templates.env.filters["month_year"] = month_year
templates.env.filters["time_ago"] = time_ago
