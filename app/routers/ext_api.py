"""The JSON API of the Chrome extension (SPEC §8, D-136, D-137, D-138, D-141).

- Only `Authorization: Bearer <token>`: the session cookie is never read here, so a
  web page can't use a logged-in browser to call this API (no CSRF needed).
- Errors are `{"error": {"code", "message"}}` (app/errors.py).
- The Origin check and the body size limit run before this, in app/main.py.
"""

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from slowapi.util import get_remote_address
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db import get_session
from app.models import ApiToken, Application, User
from app.models.base import utcnow
from app.schemas.ext_api import CheckIn, JobIn, LoginIn
from app.security import ApiError, client_ip, limiter, login_lockout
from app.services import application_service, clock, token_service
from app.services.auth_service import authenticate
from app.services.url_service import canonical_url

router = APIRouter(prefix="/api/ext", tags=["extension"])

DbSession = Annotated[Session, Depends(get_session)]

LOGIN_ERROR = "Invalid email or password."
LOCKED_ERROR = "Too many attempts. Try again in 15 minutes."
SIGN_IN_AGAIN = "Please sign in again."


def token_key(request: Request) -> str:
    """The rate limit key: the token, so users behind one proxy IP don't share a
    limit (#18). The header is hashed again here, so the raw token isn't kept in
    the limiter's memory. Without a token, the IP address."""
    header = request.headers.get("authorization", "")
    if header:
        return "token:" + hashlib.sha256(header.encode()).hexdigest()
    return get_remote_address(request)


def require_token(request: Request, session: DbSession) -> tuple[User, ApiToken]:
    """Dependency: the user and token of the Bearer header, or 401."""
    scheme, _, raw = request.headers.get("authorization", "").partition(" ")
    found = token_service.check(session, raw.strip()) if scheme.lower() == "bearer" else None
    if found is None:
        raise ApiError(401, "unauthorized", SIGN_IN_AGAIN)
    return found


TokenUser = Annotated[tuple[User, ApiToken], Depends(require_token)]


def app_url(application_id: int) -> str:
    """The job's page in the web app, for "Open in Unfurl"."""
    return f"{get_settings().app_base_url.rstrip('/')}/applications/{application_id}"


# --- Sign in and out --------------------------------------------------------------


@router.post("/auth/login")
@limiter.limit("5/minute")
def login(request: Request, body: LoginIn, session: DbSession):
    """The same checks as the login form (D-136): lockout, one message for every
    failure. Unverified accounts get a token too (SPEC §8)."""
    ip = client_ip(request)
    if login_lockout.is_locked(ip, body.email):
        raise ApiError(429, "rate_limited", LOCKED_ERROR)
    user = authenticate(session, body.email, body.password)
    if user is None:
        login_lockout.record_failure(ip, body.email)
        raise ApiError(401, "unauthorized", LOGIN_ERROR)
    login_lockout.clear(ip, body.email)
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()

    raw, token = token_service.issue(session, user)
    return {
        "token": raw,
        "expires_at": token.expires_at.isoformat(),
        "user": {"full_name": user.full_name, "email": user.email},
    }


@router.post("/auth/logout")
@limiter.limit("60/minute", key_func=token_key)
def logout(request: Request, found: TokenUser, session: DbSession):
    """Revokes only this token; the web sessions stay (D-136)."""
    _, token = found
    token_service.revoke(session, token)
    return {"ok": True}


@router.get("/me")
@limiter.limit("60/minute", key_func=token_key)
def me(request: Request, found: TokenUser):
    user, _ = found
    return {
        "full_name": user.full_name,
        "email": user.email,
        "is_verified": user.is_verified,
        # AI credits come with M6b (D-135).
        "credits_remaining": None,
        "using_byok": None,
    }


# --- Jobs ---------------------------------------------------------------------------


@router.post("/jobs/check")
@limiter.limit("60/minute", key_func=token_key)
def check_job(request: Request, body: CheckIn, found: TokenUser, session: DbSession):
    """Is this job saved already? (D-138) Archived jobs count; with several, the
    newest one."""
    user, _ = found
    canonical = canonical_url(body.url)
    if canonical is None:
        return {"exists": False}
    job = session.exec(
        select(Application)
        .where(Application.user_id == user.id, Application.canonical_url == canonical)
        .order_by(col(Application.created_at).desc(), col(Application.id).desc())
    ).first()
    if job is None:
        return {"exists": False}
    return {
        "exists": True,
        "application_id": job.id,
        "status": job.status,
        "app_url": app_url(job.id),
    }


@router.post("/jobs", status_code=201)
@limiter.limit("60/minute", key_func=token_key)
def save_job(request: Request, body: JobIn, found: TokenUser, session: DbSession):
    """Save the job from the popup's preview form (D-141)."""
    user, _ = found
    # "Applied" gets today's date where the user is (D-137). Set here, around the
    # service call: a value set inside a dependency wouldn't reach this function.
    with clock.use_timezone(user.timezone):
        try:
            job = application_service.create(
                session,
                user,
                company=body.company,
                job_title=body.job_title,
                location=body.location,
                remote_type=body.remote_type or "unknown",
                job_url=body.url,
                description_raw=body.job_text or "",
                status=body.status or "saved",
                salary_text=body.salary_text,
                posting_closes_at=body.posting_closes_at,
            )
        except application_service.TooManyApplications:
            limit = application_service.MAX_APPLICATIONS_PER_USER
            message = f"You have reached the limit of {limit} jobs."  # as on the web
            raise ApiError(422, "validation_error", message) from None
    return {"application_id": job.id, "app_url": app_url(job.id)}
