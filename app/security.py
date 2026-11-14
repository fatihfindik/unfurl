"""Request safety shared by every page and form (D-026).

- safe_redirect_path(): only same-site paths for `next=` and `back_url` (SPEC §6.1, #4)
- CSRF: one token per session, checked on every POST/PUT/PATCH/DELETE (SPEC §6.1, D-022)
- Limits: login lockout, emails per address, requests per IP (SPEC §6.1, D-020)
"""

import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email

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


# --- Limits (SPEC §6.1, D-020) ----------------------------------------------------
# Kept in memory: a restart clears them and they only work with one app process
# (D-020). Each store keeps at most MAX_TRACKED_KEYS entries, so someone who tries
# thousands of different emails can't fill the server's memory.

MAX_TRACKED_KEYS = 10_000


def _email_key(email: str) -> str | None:
    """The email as a counter key, or None for values no account can have."""
    email = normalize_email(email)
    if not email or len(email) > EMAIL_MAX_LENGTH:
        return None
    return email


def _drop_oldest(entries: OrderedDict) -> None:
    while len(entries) > MAX_TRACKED_KEYS:
        entries.popitem(last=False)  # the key that was changed least recently


# Both stores below keep their keys ordered from least to most recently changed
# (move_to_end on every change). An entry that changed earlier also runs out earlier,
# so cleaning up only looks at the oldest entries and stops at the first one still in
# use: fast, even with thousands of keys.


@dataclass
class _LockoutEntry:
    failures: list[datetime] = field(default_factory=list)
    locked_until: datetime | None = None

    def refresh(self, now: datetime, window: timedelta) -> None:
        """Forget an ended lock and failures older than the window."""
        if self.locked_until is not None and self.locked_until <= now:
            self.locked_until = None
        self.failures = [t for t in self.failures if now - t < window]

    def is_empty(self) -> bool:
        return self.locked_until is None and not self.failures


class LoginLockout:
    """5 failed logins for the same IP + email within 15 minutes lock that pair
    for 15 minutes, counted from the 5th failure (SPEC §6.1)."""

    MAX_FAILURES = 5
    WINDOW = timedelta(minutes=15)
    LOCK_TIME = timedelta(minutes=15)

    def __init__(self) -> None:
        self._entries: OrderedDict[tuple[str, str], _LockoutEntry] = OrderedDict()

    def _prune(self, now: datetime) -> None:
        while self._entries:
            key, entry = next(iter(self._entries.items()))  # the oldest
            entry.refresh(now, self.WINDOW)
            if not entry.is_empty():
                break
            del self._entries[key]

    def is_locked(self, ip: str, email: str) -> bool:
        now = utcnow()
        self._prune(now)
        entry = self._entries.get((ip, _email_key(email)))
        if entry is None:
            return False
        entry.refresh(now, self.WINDOW)
        return entry.locked_until is not None

    def record_failure(self, ip: str, email: str) -> None:
        email_key = _email_key(email)
        if email_key is None:
            return
        now = utcnow()
        self._prune(now)
        entry = self._entries.setdefault((ip, email_key), _LockoutEntry())
        entry.refresh(now, self.WINDOW)
        entry.failures.append(now)
        if len(entry.failures) >= self.MAX_FAILURES:
            # A fixed lock time: it doesn't end early when old failures leave the window.
            entry.locked_until = now + self.LOCK_TIME
            entry.failures = []
        self._entries.move_to_end((ip, email_key))
        _drop_oldest(self._entries)

    def clear(self, ip: str, email: str) -> None:
        """After a successful login."""
        self._entries.pop((ip, _email_key(email)), None)

    def clear_email(self, email: str) -> None:
        """After a password reset: the owner proved who they are, on any IP."""
        email_key = _email_key(email)
        for key in [key for key in self._entries if key[1] == email_key]:
            del self._entries[key]

    def reset(self) -> None:
        self._entries.clear()


class EmailLimiter:
    """At most 3 emails per address per hour (forgot-password, resend; SPEC §6.1)."""

    MAX_EMAILS = 3
    WINDOW = timedelta(hours=1)

    def __init__(self) -> None:
        self._sent: OrderedDict[str, list[datetime]] = OrderedDict()

    def _recent(self, email_key: str, now: datetime) -> list[datetime]:
        return [t for t in self._sent.get(email_key, []) if now - t < self.WINDOW]

    def allow(self, email: str) -> bool:
        """True (and counted) if another email may be sent to this address now."""
        email_key = _email_key(email)
        if email_key is None:
            return False
        now = utcnow()
        while self._sent:
            key = next(iter(self._sent))  # the oldest
            if self._recent(key, now):
                break
            del self._sent[key]

        sent = self._recent(email_key, now)
        if len(sent) >= self.MAX_EMAILS:
            return False
        self._sent[email_key] = [*sent, now]
        self._sent.move_to_end(email_key)
        _drop_oldest(self._sent)
        return True

    def reset(self) -> None:
        self._sent.clear()


login_lockout = LoginLockout()
email_limiter = EmailLimiter()

# Requests per IP address (slowapi). Routes use it as a decorator, for example
# @limiter.limit("10/minute"); the route must have a `request: Request` parameter.
# Behind a proxy, the real IP needs uvicorn's --proxy-headers (deploy, M13).
limiter = Limiter(key_func=get_remote_address)


def client_ip(request: Request) -> str:
    return get_remote_address(request)
