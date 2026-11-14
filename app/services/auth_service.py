"""Accounts and sessions: password hashing, register, login, logout, current user,
email verification tokens.

SPEC §6.1, D-021, D-027. CSRF and redirect checks live in app/security.py.
"""

import secrets
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pwdlib import PasswordHash
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email
from app.security import get_csrf_token, safe_redirect_path

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

# Email links (D-027). Each kind of token has its own "salt", so a token made for
# one purpose is rejected everywhere else.
VERIFY_TOKEN_SALT = "verify-email"
VERIFY_TOKEN_MAX_AGE = 48 * 60 * 60  # 48 hours, in seconds (SPEC §6.1)
# Our tokens are about 100 characters. Longer ones are rejected before any work.
TOKEN_MAX_LENGTH = 512


class EmailTaken(Exception):
    """Register was called with an email that already has an account."""


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def create_user(session: Session, *, email: str, password: str, full_name: str) -> User:
    """Save a new user on the free plan. Raises EmailTaken if the email is in use."""
    email = normalize_email(email)
    if session.exec(select(User).where(User.email == email)).first():
        raise EmailTaken(email)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        privacy_accepted_at=utcnow(),
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
    get_csrf_token(request)


def end_session(request: Request) -> None:
    """Log out: forget everything in the session cookie."""
    request.session.clear()


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
    if user is None or user.session_version != request.session.get(SESSION_VERSION):
        end_session(request)
        return None
    request.state.user = user
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


def _token_serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key.get_secret_value(), salt=salt)


def _read_token(token: str, salt: str, max_age: int) -> dict | None:
    """The token's data, or None if it is missing, too long, changed or too old."""
    if not token or len(token) > TOKEN_MAX_LENGTH:
        return None
    try:
        # Too old raises SignatureExpired, which is a kind of BadSignature.
        data = _token_serializer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None


def make_verify_token(user: User) -> str:
    # The email is in the token too: if the address ever changes, old links stop working.
    return _token_serializer(VERIFY_TOKEN_SALT).dumps({"user_id": user.id, "email": user.email})


def user_from_verify_token(session: Session, token: str) -> User | None:
    """The user a verification link belongs to, or None if the link is not valid."""
    data = _read_token(token, VERIFY_TOKEN_SALT, VERIFY_TOKEN_MAX_AGE)
    if data is None or not isinstance(data.get("user_id"), int):
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
        raise HTTPException(status_code=403, detail="Please verify your email first.")
    return user
