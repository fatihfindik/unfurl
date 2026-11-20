"""Extension tokens: make, check and revoke them (SPEC §5.12, D-136).

A token works while it is not revoked, not expired, and made for the user's current
session version. That one version check covers password change and reset, email
change and "Sign out" (D-021, D-128, D-134): each of them raises the version.
Every query filters by user_id, or looks a token up by its hash.
"""

import datetime as dt
import hashlib
import secrets

from sqlmodel import Session, col, delete, or_, select

from app.models import ApiToken, User
from app.models.base import as_utc, utcnow

TOKEN_NAME = "Chrome extension"
TOKEN_DAYS = 90
MAX_ACTIVE_TOKENS = 10  # one per browser profile is plenty
# last_used_at is a hint for Settings, not a log: writing it on every request would
# turn every read into a write.
LAST_USED_EVERY = dt.timedelta(minutes=5)
# token_urlsafe(32) gives 43 characters; anything much longer isn't one of ours.
MAX_TOKEN_LENGTH = 100


def hash_token(raw: str) -> str:
    """The stored form of a token. SHA-256 without salt is enough here: the token is
    32 random bytes, so there is nothing to guess (unlike a password)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_active(token: ApiToken, user: User, now: dt.datetime) -> bool:
    return (
        not token.revoked
        and as_utc(token.expires_at) > now
        and token.session_version == user.session_version
    )


def _dead(now: dt.datetime, current_version):
    """The rows that no longer work: the exact opposite of _is_active. `current_version`
    is the user's session version: a number, or a subquery that reads it per row."""
    return or_(
        col(ApiToken.revoked),
        col(ApiToken.expires_at) <= now,
        ApiToken.session_version != current_version,
    )


def purge_dead(session: Session, now: dt.datetime) -> int:
    """Delete every user's dead tokens (D-148); the nightly job calls this. Returns how
    many rows were deleted. Built with SQLAlchemy only, so the "user" table, a reserved
    word on Postgres, is quoted for us."""
    users_version = (
        select(User.session_version).where(User.id == ApiToken.user_id).scalar_subquery()
    )
    result = session.exec(delete(ApiToken).where(_dead(now, users_version)))
    session.commit()
    return result.rowcount


def active_tokens(session: Session, user: User) -> list[ApiToken]:
    """The user's tokens that still work, newest first."""
    now = utcnow()
    tokens = session.exec(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(col(ApiToken.created_at).desc(), col(ApiToken.id).desc())
    ).all()
    return [token for token in tokens if _is_active(token, user, now)]


def issue(session: Session, user: User) -> tuple[str, ApiToken]:
    """A new token for `user`, returned as (raw token, row). The raw token is never
    stored, so this is the only time anyone sees it.

    First the user's dead tokens (revoked, expired, or from an older session version)
    are deleted, and if MAX_ACTIVE_TOKENS still work, the oldest ones are revoked."""
    now = utcnow()
    session.exec(
        delete(ApiToken).where(ApiToken.user_id == user.id, _dead(now, user.session_version))
    )
    active = active_tokens(session, user)  # newest first
    for old in active[MAX_ACTIVE_TOKENS - 1 :]:
        old.revoked = True
        session.add(old)

    raw = secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        name=TOKEN_NAME,
        token_hash=hash_token(raw),
        session_version=user.session_version,
        expires_at=now + dt.timedelta(days=TOKEN_DAYS),
    )
    session.add(token)
    session.commit()
    session.refresh(token)
    return raw, token


def check(session: Session, raw: str | None) -> tuple[User, ApiToken] | None:
    """The user and token for a Bearer token, or None if it doesn't work (unknown,
    revoked, expired, or the user signed out / changed the password or email since)."""
    if not raw or len(raw) > MAX_TOKEN_LENGTH:
        return None
    token = session.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(raw))).first()
    if token is None:
        return None
    user = session.get(User, token.user_id)
    now = utcnow()
    if user is None or not _is_active(token, user, now):
        return None

    if token.last_used_at is None or now - as_utc(token.last_used_at) >= LAST_USED_EVERY:
        token.last_used_at = now
        session.add(token)
        session.commit()
        session.refresh(token)
    return user, token


def get_owned_token(session: Session, user: User, token_id: int) -> ApiToken | None:
    """The token `token_id` if it belongs to `user` (Settings › Revoke), else None:
    another user's token and one that doesn't exist look the same (D-140)."""
    return session.exec(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
    ).first()


def revoke(session: Session, token: ApiToken) -> None:
    """Stop one token (sign-out in the extension, "Revoke" in Settings)."""
    token.revoked = True
    session.add(token)
    session.commit()
