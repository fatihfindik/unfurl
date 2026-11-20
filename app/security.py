"""Request safety shared by every page and form (D-026).

- safe_redirect_path(): only same-site paths for `next=` and `back_url` (SPEC §6.1, #4)
- CSRF: one token per session, checked on every POST/PUT/PATCH/DELETE (SPEC §6.1, D-022)
- Limits: login lockout, emails per address, requests per IP (SPEC §6.1, D-020)
- Access log: tokens in URLs are hidden (CLAUDE.md: no tokens in logs)
- Security headers for every answer (D-120); app/main.py and the error pages use them
"""

import logging
import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email

# --- Errors with a message for the user -----------------------------------------


class UserFacingHTTPException(HTTPException):
    """An HTTP error whose `detail` is written for the user, e.g. "Please verify your
    email first." The error page and the htmx toast show it (D-125); any other
    HTTPException gets the fixed text for its status, so internal error text can't
    reach the page by accident (M13a security review L2)."""


class ApiError(HTTPException):
    """An error of the extension API (SPEC §8, D-137): app/errors.py answers
    `{"error": {"code": code, "message": detail}}`. Codes: unauthorized, forbidden,
    not_found, validation_error, rate_limited."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code, detail=message)
        self.code = code


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
# HeadAsGet (app/main.py) hands HEAD requests on as GET and marks them with this key.
HEAD_SCOPE_KEY = "unfurl.head"


def is_head_request(request: Request) -> bool:
    """A HEAD request that runs as GET (D-142): it only asks what GET would answer,
    so it must not change anything (verify an email, use up a toast)."""
    return bool(request.scope.get(HEAD_SCOPE_KEY))


# The Chrome extension API is protected by its own API tokens instead (M6).
CSRF_EXEMPT_PREFIXES = ("/api/ext/",)

# Normal (non-htmx) forms send the token as a form field in this format. File uploads
# (multipart/form-data) only come from htmx, which always sends the header.
URLENCODED_FORM = "application/x-www-form-urlencoded"


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
    if not sent and request.headers.get("content-type", "").startswith(URLENCODED_FORM):
        # Starlette keeps the parsed form on the request, so the route's own
        # Form(...) parameters still get the same data afterwards.
        form = await request.form()
        sent = form.get(CSRF_FORM_FIELD)
    # A file upload (multipart) without the header is refused without reading it:
    # parsing it would write the whole body to disk first, even for a logged-out
    # sender, which defeats the size check of the photo upload (M2b security review).

    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not isinstance(sent, str) or not _tokens_match(sent, expected):
        # The text users see on the error page or in the toast (D-125).
        raise UserFacingHTTPException(
            status_code=403, detail="Your session changed. Please reload the page."
        )


def _tokens_match(sent: str, expected: str) -> bool:
    # compare_digest takes the same time whether the first or the last character is
    # wrong, so the token can't be guessed character by character from response times.
    return secrets.compare_digest(sent.encode(), expected.encode())


# --- Limits (SPEC §6.1, D-020) ----------------------------------------------------
# Kept in memory: a restart clears them and they only work with one app process
# (D-020). Each store keeps at most MAX_TRACKED_KEYS entries, so someone who tries
# thousands of different emails can't fill the server's memory.
# Sync routes run in parallel threads, so every store is changed under a lock
# (threading.Lock: only one thread at a time inside a `with self._lock:` block).

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


def _prune(entries: OrderedDict, now: datetime, window: timedelta) -> None:
    """Drop entries that are empty again, oldest first (see the note above)."""
    while entries:
        key, entry = next(iter(entries.items()))  # the oldest
        entry.refresh(now, window)
        if not entry.is_empty():
            break
        del entries[key]


def _is_locked(entries: OrderedDict, key, now: datetime, window: timedelta) -> bool:
    _prune(entries, now, window)
    entry = entries.get(key)
    if entry is None:
        return False
    entry.refresh(now, window)
    return entry.locked_until is not None


# How far _drop_oldest_unlocked looks for an unlocked entry: it runs under the store's
# lock, so a store full of locks must not make every failure walk 10,000 entries.
EVICTION_SCAN = 100


def _drop_oldest_unlocked(entries: OrderedDict, now: datetime) -> None:
    """Like _drop_oldest, but an unlocked entry (or one whose lock has ended) among the
    oldest EVICTION_SCAN goes first: failures on thousands of other emails must not
    push a lock out (#57, D-149). If they are all locked, the oldest goes."""
    while len(entries) > MAX_TRACKED_KEYS:
        victim = next(iter(entries))
        for _, (key, entry) in zip(range(EVICTION_SCAN), entries.items(), strict=False):
            if entry.locked_until is None or entry.locked_until <= now:
                victim = key
                break
        del entries[victim]


def _count_failure(
    entries: OrderedDict, key, now: datetime, window: timedelta, limit: int, lock: timedelta
) -> None:
    _prune(entries, now, window)
    entry = entries.setdefault(key, _LockoutEntry())
    entry.refresh(now, window)
    entry.failures.append(now)
    if len(entry.failures) >= limit:
        # A fixed lock time: it doesn't end early when old failures leave the window.
        entry.locked_until = now + lock
        entry.failures = []
    entries.move_to_end(key)
    _drop_oldest_unlocked(entries, now)


class LoginLockout:
    """Three counters for wrong passwords (SPEC §6.1):

    - per IP + email: 5 within 15 minutes lock that pair for 15 minutes (D-020);
    - per email, from every IP: 20 within an hour lock that email for an hour, so
      someone with many IP addresses can't guess faster (#57, D-149). A password reset
      unlocks it (clear_email). Settings checks skip it (per_email=False): a signed-in
      owner can't be blocked by someone who only knows the address;
    - per user, Settings checks only: 20 within an hour lock that user's Settings
      checks for an hour, so a stolen session can't guess from many IPs either. Only
      someone signed in to the account can fill it.
    Every lock counts from the failure that reached the limit.
    """

    MAX_FAILURES = 5
    WINDOW = timedelta(minutes=15)
    LOCK_TIME = timedelta(minutes=15)
    # Window and lock time are equal, so an entry that changed earlier also runs out
    # earlier and the oldest-first clean-up stays right.
    EMAIL_MAX_FAILURES = 20
    EMAIL_WINDOW = timedelta(hours=1)
    EMAIL_LOCK_TIME = timedelta(hours=1)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], _LockoutEntry] = OrderedDict()
        self._by_email: OrderedDict[str, _LockoutEntry] = OrderedDict()
        self._by_user: OrderedDict[int, _LockoutEntry] = OrderedDict()

    def is_locked(
        self, ip: str, email: str, *, per_email: bool = True, user_id: int | None = None
    ) -> bool:
        """per_email=False skips the email lock; user_id adds the Settings user lock."""
        now = utcnow()
        email_key = _email_key(email)
        with self._lock:
            if _is_locked(self._entries, (ip, email_key), now, self.WINDOW):
                return True
            if user_id is not None and _is_locked(self._by_user, user_id, now, self.EMAIL_WINDOW):
                return True
            if per_email and email_key is not None:
                return _is_locked(self._by_email, email_key, now, self.EMAIL_WINDOW)
            return False

    def record_failure(
        self, ip: str, email: str, *, per_email: bool = True, user_id: int | None = None
    ) -> None:
        email_key = _email_key(email)
        if email_key is None:
            return
        now = utcnow()
        with self._lock:
            _count_failure(
                self._entries, (ip, email_key), now, self.WINDOW, self.MAX_FAILURES, self.LOCK_TIME
            )
            # The email and user counters share the same limit and times.
            stores = []
            if per_email:
                stores.append((self._by_email, email_key))
            if user_id is not None:
                stores.append((self._by_user, user_id))
            for entries, key in stores:
                _count_failure(
                    entries,
                    key,
                    now,
                    self.EMAIL_WINDOW,
                    self.EMAIL_MAX_FAILURES,
                    self.EMAIL_LOCK_TIME,
                )

    def clear(self, ip: str, email: str) -> None:
        """After a successful login: only this pair. The email counter stays, so the
        owner signing in doesn't reset the count of someone guessing elsewhere."""
        with self._lock:
            self._entries.pop((ip, _email_key(email)), None)

    def clear_email(self, email: str, user_id: int | None = None) -> None:
        """After a password reset (or an undone email change, a deleted account): the
        owner proved who they are, so every lock on this email goes, on any IP, and
        the user's Settings lock too when user_id is given."""
        email_key = _email_key(email)
        with self._lock:
            for key in [key for key in self._entries if key[1] == email_key]:
                del self._entries[key]
            self._by_email.pop(email_key, None)
            if user_id is not None:
                self._by_user.pop(user_id, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()
            self._by_email.clear()
            self._by_user.clear()


class EmailLimiter:
    """At most 3 emails per address per hour (forgot-password, resend; SPEC §6.1)."""

    MAX_EMAILS = 3
    WINDOW = timedelta(hours=1)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent: OrderedDict[str, list[datetime]] = OrderedDict()

    def _recent(self, email_key: str, now: datetime) -> list[datetime]:
        return [t for t in self._sent.get(email_key, []) if now - t < self.WINDOW]

    def allow(self, email: str) -> bool:
        """True (and counted) if another email may be sent to this address now."""
        email_key = _email_key(email)
        if email_key is None:
            return False
        now = utcnow()
        with self._lock:
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
        with self._lock:
            self._sent.clear()


login_lockout = LoginLockout()
email_limiter = EmailLimiter()

# Requests per IP address (slowapi). Routes use it as a decorator, for example
# @limiter.limit("10/minute"); the route must have a `request: Request` parameter.
# Behind a proxy, the real IP needs uvicorn's --proxy-headers (deploy, M13).
limiter = Limiter(key_func=get_remote_address)


def client_ip(request: Request) -> str:
    return get_remote_address(request)


# --- Access log -------------------------------------------------------------------
# uvicorn writes every request's path, query string included, to the "uvicorn.access"
# log. Verify and reset links have their token there (/reset-password?token=...), and
# a reset token in a log file would let anyone who reads the log take over the account.

_TOKEN_IN_URL = re.compile(r"(token=)[^&\s]+")


class HideTokensFilter(logging.Filter):
    """Replaces every `token=...` value in a log line's arguments with `token=***`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_IN_URL.sub(r"\1***", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True  # keep the line, only changed


def hide_tokens_in_access_log() -> None:
    """Add HideTokensFilter to uvicorn's access log (once, even if called again)."""
    access_log = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HideTokensFilter) for f in access_log.filters):
        access_log.addFilter(HideTokensFilter())


# --- Security headers -------------------------------------------------------------

# Sent with every answer unless the route set its own (D-120, SPEC §10.1).
# The CSP allows only our own origin: no inline scripts or styles, no other sites.
# Alpine's CSP build and the htmx settings in layouts/_head.html work under it.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
)
SECURITY_HEADERS = {
    b"content-security-policy": CONTENT_SECURITY_POLICY.encode(),
    b"x-content-type-options": b"nosniff",  # the browser trusts our Content-Type
    b"x-frame-options": b"DENY",  # no other site can show us in a frame
    b"referrer-policy": b"same-origin",  # links to other sites don't get our addresses
}
