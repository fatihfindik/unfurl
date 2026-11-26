"""Request safety shared by every page and form (D-026).

- safe_redirect_path(): only same-site paths for `next=` and `back_url` (SPEC §6.1, #4)
- CSRF: one token per session, checked on every POST/PUT/PATCH/DELETE (SPEC §6.1, D-022)
- Limits: login lockout, emails per address, requests per IP (SPEC §6.1, D-020)
- Access log: tokens in URLs are hidden (CLAUDE.md: no tokens in logs)
- Security headers for every answer (D-120); app/main.py and the error pages use them
"""

import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import Engine
from sqlmodel import Session, col, delete, func, select
from starlette.datastructures import FormData

from app.models.base import utcnow
from app.models.security_counter import SecurityCounter
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

# The most fields any form of the app sends (D-171): the CV editor, with 50
# experiences × 20 bullet checkboxes, 7 sections and about 10 other fields.
MAX_ANY_FORM_FIELDS = 1100


async def read_limited_form(request: Request, max_fields: int, max_files: int = 1000) -> FormData:
    """The submitted form, with at most `max_fields` fields (400 otherwise).

    Starlette parses a form only once and keeps it: if the CSRF check already parsed
    it (a normal form sends the token as a field), that parse used the app-wide limit
    and a smaller limit given here would be skipped (#88). So the fields are counted
    again after parsing."""
    form = await request.form(max_fields=max_fields, max_files=max_files)
    if len(form.multi_items()) > max_fields:
        raise HTTPException(status_code=400, detail="Too many fields.")
    return form


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
        # Form(...) parameters still get the same data afterwards. Parsed with the
        # app-wide limit (D-171); a route with a smaller limit counts again
        # afterwards (read_limited_form), since this parse is the one that is kept.
        form = await request.form(max_fields=MAX_ANY_FORM_FIELDS)
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


# --- Limits (SPEC §6.1, D-020, D-149, D-198) ----------------------------------------
# Kept in the database (table securitycounter): a restart keeps every lock and count,
# and more than one app process sees the same numbers. Each call opens its own short
# session and commits it. Rule: call these only while the route's session has no
# written but uncommitted changes (every caller does so today): on SQLite the counter's
# write would wait for the route's lock. Keys are HMAC hashes: the IP address and the
# email are never stored. Two failures at the same moment may both pass the count
# before either is saved, so a lock can come one or two failures late; that is
# accepted like D-149 (no row lock: the counts only need to be about right).
# slowapi's per-IP request limits (below) stay in memory: their windows are a minute,
# so a restart clearing them does no harm (D-198).


def _email_key(email: str) -> str | None:
    """The email as a counter key, or None for values no account can have."""
    email = normalize_email(email)
    if not email or len(email) > EMAIL_MAX_LENGTH:
        return None
    return email


def _hash(*parts: object) -> str:
    """A fixed-length key for a counter row: HMAC-SHA256 with SECRET_KEY, so the IP or
    email can't be found again by hashing every IPv4 address or a known email. A new
    SECRET_KEY only starts the counts again. "\\x1f" can't appear in an IP or email."""
    from app.config import get_settings

    secret = get_settings().secret_key.get_secret_value().encode()
    message = "\x1f".join(str(part) for part in parts).encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


# Rows older than this are of no use any more: the longest window and lock is an hour.
COUNTER_KEEP = timedelta(hours=2)


class _DbCounters:
    """The database behind LoginLockout and EmailLimiter. `engine` is the app's
    database (app.db.engine) unless a test sets its own (tests/conftest.py)."""

    engine: Engine | None = None

    @contextmanager
    def _session(self) -> Iterator[Session]:
        from app import db  # here, not at the top: app.db reads the settings on import

        with Session(self.engine or db.engine) as session:
            yield session

    @staticmethod
    def _count(session: Session, kind: str, key: str, since: datetime) -> int:
        return session.exec(
            select(func.count())
            .select_from(SecurityCounter)
            .where(
                SecurityCounter.kind == kind,
                SecurityCounter.key == key,
                col(SecurityCounter.at) > since,
            )
        ).one()

    @staticmethod
    def _forget_old(session: Session, key: str, now: datetime) -> None:
        """Rows of this key nobody looks at any more, also when the nightly job is off."""
        session.exec(
            delete(SecurityCounter).where(
                SecurityCounter.key == key, col(SecurityCounter.at) < now - COUNTER_KEEP
            )
        )

    @staticmethod
    def _delete(session: Session, kinds: tuple[str, ...], key: str) -> None:
        session.exec(
            delete(SecurityCounter).where(
                col(SecurityCounter.kind).in_(kinds), SecurityCounter.key == key
            )
        )

    def reset(self) -> None:
        """Forget every count (tests)."""
        with self._session() as session:
            session.exec(delete(SecurityCounter))
            session.commit()


class LoginLockout(_DbCounters):
    """Three counters for wrong passwords (SPEC §6.1):

    - per IP + email: 5 within 15 minutes lock that pair for 15 minutes (D-020);
    - per email, from every IP: 20 within an hour lock that email for an hour, so
      someone with many IP addresses can't guess faster (#57, D-149). A password reset
      unlocks it (clear_email). Settings checks skip it (per_email=False): a signed-in
      owner can't be blocked by someone who only knows the address;
    - per user, Settings checks only: 20 within an hour lock that user's Settings
      checks for an hour, so a stolen session can't guess from many IPs either. Only
      someone signed in to the account can fill it.
    Every lock counts from the failure that reached the limit, and a new lock starts
    the count again from zero.
    """

    MAX_FAILURES = 5
    WINDOW = timedelta(minutes=15)
    LOCK_TIME = timedelta(minutes=15)
    EMAIL_MAX_FAILURES = 20
    EMAIL_WINDOW = timedelta(hours=1)
    EMAIL_LOCK_TIME = timedelta(hours=1)

    def _locked(self, session: Session, scope: str, key: str, now: datetime) -> bool:
        # A lock row's `at` is when the lock ends.
        return self._count(session, f"lock_{scope}", key, now) > 0

    def is_locked(
        self, ip: str, email: str, *, per_email: bool = True, user_id: int | None = None
    ) -> bool:
        """per_email=False skips the email lock; user_id adds the Settings user lock."""
        email_key = _email_key(email)
        if email_key is None:
            return False  # no account can have it; record_failure doesn't count it either
        now = utcnow()
        with self._session() as session:
            if self._locked(session, "pair", _hash("pair", ip, email_key), now):
                return True
            if user_id is not None and self._locked(session, "user", _hash("user", user_id), now):
                return True
            return per_email and self._locked(session, "email", _hash("email", email_key), now)

    def _count_failure(
        self,
        session: Session,
        scope: str,
        key: str,
        email_hash: str | None,
        now: datetime,
        window: timedelta,
        limit: int,
        lock: timedelta,
    ) -> None:
        self._forget_old(session, key, now)
        session.add(SecurityCounter(kind=f"fail_{scope}", key=key, email_key=email_hash, at=now))
        session.flush()
        if self._count(session, f"fail_{scope}", key, now - window) >= limit:
            # A fixed lock time; the failures that led to it are gone, so the next lock
            # needs `limit` new ones.
            self._delete(session, (f"fail_{scope}",), key)
            session.add(
                SecurityCounter(kind=f"lock_{scope}", key=key, email_key=email_hash, at=now + lock)
            )

    def record_failure(
        self, ip: str, email: str, *, per_email: bool = True, user_id: int | None = None
    ) -> None:
        email_key = _email_key(email)
        if email_key is None:
            return
        now = utcnow()
        email_hash = _hash("email", email_key)
        with self._session() as session:
            self._count_failure(
                session,
                "pair",
                _hash("pair", ip, email_key),
                email_hash,
                now,
                self.WINDOW,
                self.MAX_FAILURES,
                self.LOCK_TIME,
            )
            # The email and user counters share the same limit and times.
            scopes = []
            if per_email:
                scopes.append(("email", email_hash, email_hash))
            if user_id is not None:
                scopes.append(("user", _hash("user", user_id), None))
            for scope, key, email_column in scopes:
                self._count_failure(
                    session,
                    scope,
                    key,
                    email_column,
                    now,
                    self.EMAIL_WINDOW,
                    self.EMAIL_MAX_FAILURES,
                    self.EMAIL_LOCK_TIME,
                )
            session.commit()

    def clear(self, ip: str, email: str) -> None:
        """After a successful login: only this pair. The email counter stays, so the
        owner signing in doesn't reset the count of someone guessing elsewhere."""
        email_key = _email_key(email)
        if email_key is None:
            return
        with self._session() as session:
            self._delete(session, ("fail_pair", "lock_pair"), _hash("pair", ip, email_key))
            session.commit()

    def clear_email(self, email: str, user_id: int | None = None) -> None:
        """After a password reset (or an undone email change, a deleted account): the
        owner proved who they are, so every lock on this email goes, on any IP, and
        the user's Settings lock too when user_id is given."""
        email_key = _email_key(email)
        with self._session() as session:
            if email_key is not None:
                # Pair rows and email rows both carry the email's hash.
                session.exec(
                    delete(SecurityCounter).where(
                        col(SecurityCounter.kind).in_(
                            ("fail_pair", "lock_pair", "fail_email", "lock_email")
                        ),
                        SecurityCounter.email_key == _hash("email", email_key),
                    )
                )
                # The emails sent to it too: a deleted account leaves nothing behind.
                self._delete(session, ("email_sent",), _hash("email", email_key))
            if user_id is not None:
                self._delete(session, ("fail_user", "lock_user"), _hash("user", user_id))
            session.commit()


class EmailLimiter(_DbCounters):
    """At most 3 emails per address per hour (forgot-password, resend; SPEC §6.1)."""

    MAX_EMAILS = 3
    WINDOW = timedelta(hours=1)

    def allow(self, email: str) -> bool:
        """True (and counted) if another email may be sent to this address now."""
        email_key = _email_key(email)
        if email_key is None:
            return False
        now = utcnow()
        key = _hash("email", email_key)
        with self._session() as session:
            self._forget_old(session, key, now)
            session.commit()
            if self._count(session, "email_sent", key, now - self.WINDOW) >= self.MAX_EMAILS:
                return False
            session.add(SecurityCounter(kind="email_sent", key=key, at=now))
            session.commit()
            return True


def purge_security_counters(session: Session, now: datetime) -> int:
    """Delete counter rows nobody looks at any more (the nightly job, D-198). A lock
    row's `at` is its end, so a lock is only deleted an hour after it ended."""
    result = session.exec(
        delete(SecurityCounter).where(col(SecurityCounter.at) < now - COUNTER_KEEP)
    )
    session.commit()
    return result.rowcount


login_lockout = LoginLockout()
email_limiter = EmailLimiter()

# Requests per IP address (slowapi). Routes use it as a decorator, for example
# @limiter.limit("10/minute"); the route must have a `request: Request` parameter.
# Behind a proxy, `fastapi run` takes the real IP from X-Forwarded-For only when the
# proxy is in FORWARDED_ALLOW_IPS; production refuses to start without it (D-199).
limiter = Limiter(key_func=get_remote_address)


def client_ip(request: Request) -> str:
    return get_remote_address(request)


# --- Access log -------------------------------------------------------------------
# uvicorn writes every request's path, query string included, to the "uvicorn.access"
# log. Verify and reset links have their token there (/reset-password?token=...), and
# a reset token in a log file would let anyone who reads the log take over the account.

# unlock=: the value that unlocks a master for a session (D-169), not for a log either.
_TOKEN_IN_URL = re.compile(r"((?:token|unlock)=)[^&\s]+")


class HideTokensFilter(logging.Filter):
    """Replaces every `token=...` and `unlock=...` value in a log line's arguments
    with `token=***` / `unlock=***`."""

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
