"""Request safety shared by every page and form (D-026).

- safe_redirect_path(): only same-site paths for `next=` and `back_url` (SPEC §6.1, #4)
- CSRF: one token per session, checked on every POST/PUT/PATCH/DELETE (SPEC §6.1, D-022)
"""

import secrets
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, Request

# --- Redirects ------------------------------------------------------------------


def safe_redirect_path(value: str | None, default: str = "/") -> str:
    """Return `value` if it is a path on this site, otherwise `default`.

    Used for every redirect target that comes from the user (`?next=...`) and for
    `back_url` links. Without this, a link like /login?next=https://evil.example
    would send a user to another site right after they log in (open redirect).

    Allowed: "/settings", "/applications?page=2".
    Rejected: "https://evil.com", "//evil.com" (browsers read it as a host),
    "/\\evil.com" (browsers treat "\\" like "/"), "javascript:...", "", None,
    and anything with control characters such as newlines.
    """
    if not value:
        return default
    # Check the value as given and once URL-decoded: "/%2F%2Fevil.com" must not
    # slip through as "//evil.com" if something decodes it later.
    for candidate in (value, unquote(value)):
        if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
            return default
        if "\\" in candidate:
            return default
        if not candidate.startswith("/") or candidate.startswith("//"):
            return default
        parts = urlsplit(candidate)
        if parts.scheme or parts.netloc:
            return default
    return value


# --- CSRF -----------------------------------------------------------------------
# CSRF (cross-site request forgery): another site makes the user's browser send a
# form to Unfurl, and the browser adds the user's session cookie. A secret token that
# only our own pages know stops this: the other site can't read or guess it.

CSRF_SESSION_KEY = "csrf"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"  # sent by htmx (hx-headers in base.html)

# Methods that only read data are not checked.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
# The Chrome extension API is protected by its own API tokens instead (M6).
CSRF_EXEMPT_PREFIXES = ("/api/ext/",)

FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def get_csrf_token(request: Request) -> str:
    """The session's CSRF token; created on first use (also for logged-out visitors,
    so the login and register forms are protected too)."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf(request: Request) -> None:
    """App-wide dependency: reject a changing request without the right token (403).

    The token can come from the `X-CSRF-Token` header (htmx) or the `csrf_token`
    form field (normal forms).
    """
    if request.method in SAFE_METHODS or request.url.path.startswith(CSRF_EXEMPT_PREFIXES):
        return

    sent = request.headers.get(CSRF_HEADER)
    if not sent and request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPES):
        # Starlette keeps the parsed form on the request, so the route's own
        # Form(...) parameters still get the same data afterwards.
        form = await request.form()
        sent = form.get(CSRF_FORM_FIELD)

    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not isinstance(sent, str) or not _tokens_match(sent, expected):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")


def _tokens_match(sent: str, expected: str) -> bool:
    # compare_digest takes the same time whether the first or the last character is
    # wrong, so the token can't be guessed character by character from response times.
    return secrets.compare_digest(sent.encode(), expected.encode())
