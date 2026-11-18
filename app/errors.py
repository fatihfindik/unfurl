"""Error pages (D-125, SPEC §10.1): our own page instead of FastAPI's JSON.

- 404 and other HTTP errors (403, 405, 409, …): a short page in a plain layout.
- 422 (a request we can't read): a message page; a bad id in the path is a 404.
- 500 (a bug): "Something went wrong", never the error itself.
- htmx requests get an empty body and a toast in the HX-Trigger header, so the
  page stays as it is (app.js shows the toast).
- Paths under /api/ keep JSON (the browser extension, M6).

The pages use their own templates object without the shared context processors:
those read the session and the user, and after a crash the database session can
already be closed. Nothing here touches the database.
"""

import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from app.security import SECURITY_HEADERS, UserFacingHTTPException
from app.templating import TEMPLATES_DIR, THEMES, is_htmx, static_url

logger = logging.getLogger(__name__)

error_templates = Jinja2Templates(directory=TEMPLATES_DIR)
error_templates.env.globals["static_url"] = static_url

# (heading, text) for the page.
PAGE_TEXT = {
    404: ("Page not found", "This page doesn't exist or was deleted."),
    403: ("Not allowed", "You can't do this right now. Please reload the page and try again."),
    405: ("Not allowed", "This address can't be used like this."),
    409: ("This changed in the meantime", "Please reload the page and try again."),
    422: ("This request couldn't be read", "Please reload the page and try again."),
    500: ("Something went wrong", "Please try again. If it keeps happening, reload the page."),
}
OTHER_TEXT = ("Something went wrong", "Please reload the page and try again.")

# The toast an htmx request gets instead of the page.
TOAST_TEXT = {
    404: "This item no longer exists. Reload the page to see the latest.",
    403: "Your session changed. Please reload the page.",
    422: "This request couldn't be read. Please reload the page.",
    429: "Too many requests. Please wait a minute.",
    500: "Something went wrong. Please try again.",
}
OTHER_TOAST = "Something went wrong. Please reload the page."


def our_detail(exc: HTTPException) -> str | None:
    """The text a route wrote for the user (UserFacingHTTPException), or None: then
    the page shows the fixed text for the status code."""
    if isinstance(exc, UserFacingHTTPException) and isinstance(exc.detail, str):
        return exc.detail or None
    return None


def toast_headers(message: str) -> dict:
    toast = {"toast": {"message": message, "undo_url": "", "undo_token": ""}}
    return {"HX-Trigger": json.dumps(toast)}


def error_response(
    request: Request, status: int, *, detail: str | None = None, headers: dict | None = None
) -> Response:
    """The error answer for `status`: a toast for htmx, otherwise the page."""
    headers = dict(headers or {})
    if is_htmx(request):
        message = detail or TOAST_TEXT.get(status, OTHER_TOAST)
        return Response(status_code=status, headers=headers | toast_headers(message))
    heading, text = PAGE_TEXT.get(status, OTHER_TEXT)
    theme = request.cookies.get("theme")
    return error_templates.TemplateResponse(
        request,
        "errors/page.html",
        {
            "heading": heading,
            "text": detail or text,
            "status": status,
            "theme": theme if theme in THEMES else "system",
        },
        status_code=status,
        headers=headers,
    )


def wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


async def http_error_response(request: Request, exc: HTTPException) -> Response:
    """404, 403, 405, 409, …: raised by routes or by the router itself."""
    if wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return error_response(
        request, exc.status_code, detail=our_detail(exc), headers=getattr(exc, "headers", None)
    )


async def validation_error_response(request: Request, exc: RequestValidationError) -> Response:
    """A request FastAPI couldn't read. A bad value in the path (an id that isn't
    a number) means the page doesn't exist: 404. Anything else keeps 422."""
    if wants_json(request):
        return JSONResponse({"detail": exc.errors()}, status_code=422)
    in_path = any(error.get("loc", ("",))[0] == "path" for error in exc.errors())
    return error_response(request, 404 if in_path else 422)


def server_error_response(request: Request, exc: Exception) -> Response:
    """A bug (500). This runs in Starlette's outermost middleware, outside
    SecurityHeaders, so it adds the headers itself. Only the method and path are
    logged: the query string can hold a token."""
    logger.exception("Server error on %s %s", request.method, request.url.path)
    headers = {name.decode(): value.decode() for name, value in SECURITY_HEADERS.items()}
    headers["cache-control"] = "no-store"
    try:
        if wants_json(request):
            return JSONResponse({"detail": "Server error"}, status_code=500, headers=headers)
        return error_response(request, 500, headers=headers)
    except Exception:  # even the page failed: plain text, still with the headers
        logger.exception("The error page failed too")
        return PlainTextResponse("Something went wrong.", status_code=500, headers=headers)


def add_error_handlers(app) -> None:
    app.add_exception_handler(HTTPException, http_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    app.add_exception_handler(Exception, server_error_response)
