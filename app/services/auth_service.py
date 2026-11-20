"""Accounts and sessions: password hashing, register, login, logout, current user,
email verification and password reset tokens.

SPEC §6.1, D-021, D-027. CSRF and redirect checks live in app/security.py.
"""

import datetime as dt
import hashlib
import secrets
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pwdlib import PasswordHash
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.models.base import as_utc, utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email
from app.security import (
    UserFacingHTTPException,
    client_ip,
    get_csrf_token,
    login_lockout,
    safe_redirect_path,
)
from app.services import application_service, clock

PASSWORD_MIN_LENGTH = 10
# An upper limit, so nobody can make the server hash a huge text (argon2 is slow on purpose).
PASSWORD_MAX_LENGTH = 1024

# argon2 with pwdlib's recommended settings (SPEC §3, §10.1).
password_hasher = PasswordHash.recommended()

# A real hash of a random password. Login checks the password against it when the
# email is unknown, so a wrong email takes as long as a wrong password and response
# times don't tell which emails have an account.
_DUMMY_HASH = password_hasher.hash(secrets.token_urlsafe(16))

# Keys in the signed session cookie (D-021).
SESSION_USER_ID = "user_id"
SESSION_VERSION = "session_version"
# When the account was created: on SQLite a deleted user's id can be given to the
# next sign-up, so id + version alone could let an old cookie into a new account
# (security review M13b, M2).
SESSION_ACCOUNT = "account"
# The user's time zone name, for app/main.py's UserTimezone (D-109). Empty: the default.
SESSION_TIMEZONE = "tz"

# Email links (D-027). Each kind of token has its own "salt", so a token made for
# one purpose is rejected everywhere else.
VERIFY_TOKEN_SALT = "verify-email"
VERIFY_TOKEN_MAX_AGE = 48 * 60 * 60  # 48 hours, in seconds (SPEC §6.1)
RESET_TOKEN_SALT = "reset-password"
RESET_TOKEN_MAX_AGE = 60 * 60  # 1 hour (SPEC §6.1)
# Our tokens are about 100–300 characters; with two 254-character addresses (the email
# change link) close to 1000. Longer ones are rejected before any work.
TOKEN_MAX_LENGTH = 1024


class EmailTaken(Exception):
    """Register was called with an email that already has an account."""


FULL_NAME_MAX_LENGTH = 200


def password_error(password: str) -> str | None:
    """The rule for a new password (register, reset, Settings › Account), or None."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    return None


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def create_user(
    session: Session, *, email: str, password: str, full_name: str, timezone: str | None = None
) -> User:
    """Save a new user on the free plan. Raises EmailTaken if the email is in use.
    `timezone` comes from the browser (D-108); an unknown name is ignored."""
    email = normalize_email(email)
    if session.exec(select(User).where(User.email == email)).first():
        raise EmailTaken(email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        privacy_accepted_at=utcnow(),
        timezone=clock.valid_zone(timezone),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        # Two registrations with the same email at the same moment: the unique
        # index lets only one through.
        session.rollback()
        raise EmailTaken(email) from None
    session.refresh(user)
    return user


def authenticate(session: Session, email: str, password: str) -> User | None:
    """The user if email and password match, otherwise None (never says which one was wrong)."""
    if len(password) > PASSWORD_MAX_LENGTH:
        return None
    if len(email) > EMAIL_MAX_LENGTH:
        # No account can have such an email; skip the database, but do the same work.
        verify_password(password, _DUMMY_HASH)
        return None
    user = session.exec(select(User).where(User.email == normalize_email(email))).first()
    if user is None:
        verify_password(password, _DUMMY_HASH)  # same work as a real check, see _DUMMY_HASH
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def start_session(request: Request, user: User) -> None:
    """Log the user in on this browser.

    The old session is cleared first and gets a new CSRF token, so a session
    prepared by someone else before login can't be reused (session fixation).
    """
    request.session.clear()
    request.session[SESSION_USER_ID] = user.id
    request.session[SESSION_VERSION] = user.session_version
    request.session[SESSION_ACCOUNT] = account_stamp(user)
    request.session[SESSION_TIMEZONE] = user.timezone
    get_csrf_token(request)


def account_stamp(user: User) -> str:
    """The account's creation time as text, kept in the session (SESSION_ACCOUNT)."""
    return as_utc(user.created_at).isoformat()


def remember_timezone(session: Session, user: User, name: str | None) -> None:
    """At login: save the browser's time zone if the user has none yet (D-108). A zone
    picked in Settings is never overwritten. Dates saved "without a time" so far were
    saved in the default zone; they move to the same day in the new one."""
    name = clock.valid_zone(name)
    if user.timezone is not None or name is None:
        return
    application_service.move_all_day_times(session, user, None, name)
    user.timezone = name
    session.add(user)
    session.commit()


def end_session(request: Request) -> None:
    """Log out: forget everything in the session cookie."""
    request.session.clear()


def check_password_with_lockout(request: Request, user: User, password: str) -> str:
    """Check the logged-in user's password before a sensitive change (Settings ›
    Account, Delete my account; D-131). Returns "ok", "wrong" or "locked".

    It shares the login lockout (same IP + email): an open session can't be used to
    guess the password faster than the sign-in form allows. While locked, even the
    right password is refused. A too long password counts as wrong without running
    argon2 (it is slow on purpose)."""
    ip = client_ip(request)
    # No email lock: it would let anyone who knows the address block a signed-in owner
    # here. The user lock instead stops a stolen session guessing from many IPs (D-149).
    if login_lockout.is_locked(ip, user.email, per_email=False, user_id=user.id):
        return "locked"
    if len(password) > PASSWORD_MAX_LENGTH or not verify_password(password, user.password_hash):
        login_lockout.record_failure(ip, user.email, per_email=False, user_id=user.id)
        return "wrong"
    login_lockout.clear(ip, user.email)
    return "ok"


def change_password(session: Session, request: Request, user: User, new_password: str) -> bool:
    """Set a new password (D-132). Other devices are signed out; this session goes
    on with the new version. Only the version in the session changes: start_session
    would also make a new CSRF token, and the open page still sends the old one.

    One UPDATE that only matches while the password is the one just checked (like
    reset_password, D-121). False if it changed in the meantime."""
    result = session.exec(
        update(User)
        .where(User.id == user.id, User.password_hash == user.password_hash)
        .values(
            password_hash=hash_password(new_password),
            session_version=User.session_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.refresh(user)
    if result.rowcount != 1:
        return False
    request.session[SESSION_VERSION] = user.session_version
    return True


def sign_out(session: Session, request: Request) -> None:
    """ "Sign out" (D-128): end this session and every other one of the user.

    The session lives in the cookie, so clearing it only helps this browser; a copy
    of the cookie (or another device) would still work. Raising session_version makes
    all of them invalid (current_user compares it). Only a session that is still valid
    raises it: an old cookie can't sign out someone's other devices. One UPDATE, so
    two sign-outs at once can't lose a step (like reset_password).
    """
    user_id = request.session.get(SESSION_USER_ID)
    version = request.session.get(SESSION_VERSION)
    if type(user_id) is int and type(version) is int:  # not bool (True is an int)
        session.exec(
            update(User)
            .where(User.id == user_id, User.session_version == version)
            .values(session_version=User.session_version + 1, updated_at=utcnow())
            .execution_options(synchronize_session=False)
        )
        session.commit()
    end_session(request)


def current_user(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> User | None:
    """Dependency: the logged-in user, or None.

    The session is only valid while its session_version matches the user's: when
    the password changes the number goes up and older sessions stop working (D-021).
    The user is also put on request.state, so templates can show their name.
    """
    user_id = request.session.get(SESSION_USER_ID)
    if user_id is None:
        return None
    user = session.get(User, user_id)
    if (
        user is None
        or user.session_version != request.session.get(SESSION_VERSION)
        or account_stamp(user) != request.session.get(SESSION_ACCOUNT)
    ):
        end_session(request)
        return None
    request.state.user = user
    if request.session.get(SESSION_TIMEZONE) != user.timezone:
        # Changed on another device: this request already started with the old zone
        # (app/main.py reads the session first), the next one uses the new zone (D-109).
        request.session[SESSION_TIMEZONE] = user.timezone
    # Expired "deleted" copies go away here, a little at a time (D-086).
    from app.services import trash_service  # here: trash_service imports undo_service

    trash_service.purge_if_due(session)
    return user


class LoginRequired(Exception):
    """Raised by require_user; the handler below sends the visitor to the login page."""

    def __init__(self, next_path: str):
        self.next_path = next_path


def require_user(request: Request, user: Annotated[User | None, Depends(current_user)]) -> User:
    """Dependency for pages that need a logged-in user (SPEC §6.1)."""
    if user is None:
        # After login, come back to the page that was asked for. Only for GET:
        # a form sent without a session can't be replayed after login.
        next_path = "/"
        if request.headers.get("HX-Request") == "true":
            # An htmx request asks for a piece of a page (e.g. one card). After login
            # the user should see the whole page they were on, which htmx sends in
            # HX-Current-URL (a full URL; only its path and query are used).
            current = urlsplit(request.headers.get("HX-Current-URL", ""))
            path = current.path + ("?" + current.query if current.query else "")
            next_path = safe_redirect_path(path)
        elif request.method == "GET":
            next_path = request.url.path
            if request.url.query:
                next_path += "?" + request.url.query
        raise LoginRequired(next_path)
    return user


def login_required_response(request: Request, exc: LoginRequired) -> Response:
    """Exception handler for LoginRequired: 303 to /login?next=..., or HX-Redirect for htmx.

    htmx can't see a 303: the browser follows it inside the request and htmx would
    put the login page into the middle of the current page. With the HX-Redirect
    header htmx loads the login page as a whole page instead.
    """
    login_url = "/login"
    if exc.next_path != "/":
        # quote() also encodes "?", "&" and "=", so the whole path stays one value.
        login_url += "?next=" + quote(exc.next_path, safe="/")
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": login_url})
    return RedirectResponse(login_url, status_code=303)


# --- Email verification (SPEC §6.1, D-027) ----------------------------------------------
# A token is the data plus a signature made with SECRET_KEY and the time it was made.
# Anyone can read the data (it is only base64), but nobody can change it or make a new
# token without the key. So the database doesn't need to store tokens.


def token_serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key.get_secret_value(), salt=salt)


def read_token(token: str, salt: str, max_age: int) -> dict | None:
    """The token's data, or None if it is missing, too long, changed or too old."""
    if not token or len(token) > TOKEN_MAX_LENGTH:
        return None
    try:
        # Too old raises SignatureExpired, which is a kind of BadSignature.
        data = token_serializer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def make_verify_token(user: User) -> str:
    # The email is in the token too: if the address ever changes, old links stop working.
    return token_serializer(VERIFY_TOKEN_SALT).dumps({"user_id": user.id, "email": user.email})


def user_from_verify_token(session: Session, token: str) -> User | None:
    """The user a verification link belongs to, or None if the link is not valid."""
    data = read_token(token, VERIFY_TOKEN_SALT, VERIFY_TOKEN_MAX_AGE)
    # `type(...) is int`, not isinstance: True is an int in Python and would mean user 1.
    if data is None or type(data.get("user_id")) is not int:
        return None
    user = session.get(User, data["user_id"])
    if user is None or user.email != data.get("email"):
        return None
    return user


def mark_verified(session: Session, user: User) -> None:
    user.is_verified = True
    session.add(user)
    session.commit()


def require_verified(user: Annotated[User, Depends(require_user)]) -> User:
    """Dependency for features that need a verified email (AI features from M7, SPEC §6.1)."""
    if not user.is_verified:
        raise UserFacingHTTPException(status_code=403, detail="Please verify your email first.")
    return user


# --- Changing the email address (#56, D-134) ---------------------------------------------

EMAIL_CHANGE_TOKEN_SALT = "email-change"
EMAIL_CHANGE_TOKEN_MAX_AGE = 24 * 60 * 60  # 24 hours


def make_email_change_token(user: User, new_email: str) -> str:
    """The link sent to the new address. It also carries the old address and the
    session version: a password reset, a password change, Sign out or a change
    already made raise the version, so every pending link stops working (also after
    A→B→A, when the email alone would match again)."""
    data = {
        "user_id": user.id,
        "old_email": user.email,
        "new_email": new_email,
        "version": user.session_version,
    }
    return token_serializer(EMAIL_CHANGE_TOKEN_SALT).dumps(data)


def email_change_from_token(session: Session, token: str) -> tuple[User, str] | None:
    """(the user, the new address) a change link is for, or None if it is not valid
    (any more)."""
    data = read_token(token, EMAIL_CHANGE_TOKEN_SALT, EMAIL_CHANGE_TOKEN_MAX_AGE)
    if data is None or type(data.get("user_id")) is not int:
        return None
    new_email = data.get("new_email")
    user = session.get(User, data["user_id"])
    if (
        user is None
        or not isinstance(new_email, str)
        or user.email != data.get("old_email")
        or user.session_version != data.get("version")
        or new_email == user.email
    ):
        return None
    return user, new_email


_email_adapter = TypeAdapter(EmailStr)


def email_is_valid(email: str) -> bool:
    """The rule for an address (register, Settings › Account): length, then format.
    Only a bare address: EmailStr also accepts "Name <a@b.com>" and hands back the
    address inside, but callers save what the user typed (#56 test-writer)."""
    if len(email) > EMAIL_MAX_LENGTH or any(char in email for char in '<>"'):
        return False
    try:
        bare = _email_adapter.validate_python(email)
    except ValidationError:
        return False
    return bare.lower() == email.lower()  # EmailStr lowercases the domain


def masked_address(email: str) -> str:
    """ "jana.new@example.com" -> "j***@e***.com". The domain is masked too: the person
    asking for the change picks it, and a readable domain in a real Unfurl email could
    be used for phishing (security review #56, L2)."""
    name, _, domain = email.partition("@")
    host, dot, tld = domain.rpartition(".")
    if not host:  # no dot in the domain
        host, dot, tld = domain, "", ""
    return f"{name[:1]}***@{host[:1]}***{dot}{tld}"


def email_in_use(session: Session, email: str) -> bool:
    return session.exec(select(User.id).where(User.email == email)).first() is not None


def change_email(session: Session, user: User, new_email: str) -> str:
    """Make `new_email` the account's address (D-134): verified, and every session
    signed out (session_version + 1). One UPDATE that only matches while the address
    and the version are still the ones the link was made for. Returns "ok", "taken"
    (another account has the address) or "stale" (the link is not valid any more)."""
    old_email, version = user.email, user.session_version
    if email_in_use(session, new_email):
        return "taken"
    try:
        result = session.exec(
            update(User)
            .where(User.id == user.id, User.email == old_email, User.session_version == version)
            .values(
                email=new_email,
                is_verified=True,
                session_version=User.session_version + 1,
                email_changed_at=utcnow(),
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
    except IntegrityError:  # another account took the address a moment earlier
        session.rollback()
        return "taken"
    session.refresh(user)
    return "ok" if result.rowcount == 1 else "stale"


# --- Undoing an email change (#60, D-147) ------------------------------------------------

UNDO_EMAIL_TOKEN_SALT = "email-change-undo"
UNDO_EMAIL_TOKEN_MAX_AGE = 7 * 24 * 60 * 60  # 7 days


def old_address_can_undo(changed_at: dt.datetime | None, now: dt.datetime) -> bool:
    """Does the address being replaced get an undo link? Yes for the sign-up address
    (or one put back by an undo, whose inbox proved itself), and for one in use longer
    than the undo window plus a day; no for an address set by a change less than that
    ago. Otherwise whoever took the account could change it twice (A to B to C), get a
    link for B, their own address, and take the account back after the owner's undo
    (security review #60, H1). The extra day keeps the owner's link and the taker's
    from ever being valid at the same moment (L-a)."""
    if changed_at is None:
        return True
    window = dt.timedelta(seconds=UNDO_EMAIL_TOKEN_MAX_AGE) + dt.timedelta(days=1)
    return as_utc(now) - as_utc(changed_at) > window


def make_undo_email_token(user: User, old_email: str) -> str:
    """The "This wasn't me" link sent to the old address after a change.

    Not tied to the session version (whoever took the account could otherwise kill it
    by signing out) and not to the new address (a second change, B to C, must not
    escape it). The account stamp keeps it away from a new account with the same id."""
    data = {"user_id": user.id, "account": account_stamp(user), "old_email": old_email}
    return token_serializer(UNDO_EMAIL_TOKEN_SALT).dumps(data)


def undo_email_from_token(session: Session, token: str) -> tuple[User, str] | None:
    """(the user, the address to put back), or None if the link is not valid (any
    more): too old, changed, for another account, or the account uses that address
    again (it was undone already)."""
    data = read_token(token, UNDO_EMAIL_TOKEN_SALT, UNDO_EMAIL_TOKEN_MAX_AGE)
    if data is None or type(data.get("user_id")) is not int:
        return None
    old_email = data.get("old_email")
    user = session.get(User, data["user_id"])
    if (
        user is None
        or not isinstance(old_email, str)
        or data.get("account") != account_stamp(user)
        or user.email == old_email
    ):
        return None
    return user, old_email


def email_used_by_another(session: Session, email: str, user: User) -> bool:
    """Another account only: after a double click the first one already put the
    address back on this account, which isn't "taken" (test-writer #60)."""
    query = select(User.id).where(User.email == email, User.id != user.id)
    return session.exec(query).first() is not None


def undo_email_change(session: Session, user: User, old_email: str) -> str:
    """Put `old_email` back (D-147): verified, every session and extension token out
    (session_version + 1), and a random password nobody knows, because whoever changed
    the address knew the password (D-134). The owner sets a new one with the reset link
    sent to the old address. One UPDATE that only matches while the account still uses
    the address read here. Returns "ok", "taken" (another account uses `old_email`) or
    "stale" (something changed in the meantime)."""
    current = user.email
    if email_used_by_another(session, old_email, user):
        return "taken"
    try:
        result = session.exec(
            update(User)
            .where(User.id == user.id, User.email == current)
            .values(
                email=old_email,
                is_verified=True,
                password_hash=hash_password(secrets.token_urlsafe(32)),
                session_version=User.session_version + 1,
                # Trusted like a sign-up address: its inbox just proved itself, so it
                # gets an undo link if it is taken again (security review #60, L-b).
                email_changed_at=None,
                updated_at=utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
    except IntegrityError:  # someone signed up with that address a moment earlier
        session.rollback()
        return "taken"
    session.refresh(user)
    return "ok" if result.rowcount == 1 else "stale"


# --- Password reset (SPEC §6.1, D-027) --------------------------------------------------


def _password_fingerprint(user: User) -> str:
    """A short summary of the password hash. It changes whenever the password changes,
    so a reset link stops working once it has been used (single use, no database table).
    The hash itself never goes into the link."""
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def make_reset_token(user: User) -> str:
    # The email too, like the verify token: a link sent to an old address stops working
    # once the address changes.
    data = {"user_id": user.id, "email": user.email, "fingerprint": _password_fingerprint(user)}
    return token_serializer(RESET_TOKEN_SALT).dumps(data)


def user_from_reset_token(session: Session, token: str) -> User | None:
    """The user a reset link belongs to, or None if the link is invalid, too old or used."""
    data = read_token(token, RESET_TOKEN_SALT, RESET_TOKEN_MAX_AGE)
    if data is None or type(data.get("user_id")) is not int:
        return None
    user = session.get(User, data["user_id"])
    fingerprint = data.get("fingerprint")
    if user is None or user.email != data.get("email") or not isinstance(fingerprint, str):
        return None
    if not secrets.compare_digest(fingerprint, _password_fingerprint(user)):
        return None
    return user


def reset_password(session: Session, user: User, new_password: str) -> bool:
    """Set a new password and log the user out everywhere (D-021). False if the link
    was used in the meantime.

    One UPDATE that only matches while the password is still the one the link was made
    for: two requests with the same link at the same moment can't both succeed (#16).
    Opening the link proved the user owns the email, so it counts as verified too.
    """
    result = session.exec(
        update(User)
        .where(User.id == user.id, User.password_hash == user.password_hash)
        .values(
            password_hash=hash_password(new_password),
            session_version=User.session_version + 1,
            is_verified=True,
            updated_at=utcnow(),
        )
        # Nothing is changed in memory here; refresh() below reads what was saved.
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.refresh(user)
    return result.rowcount == 1
