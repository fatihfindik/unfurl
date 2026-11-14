"""Register, log in, log out, email verification and password reset (SPEC §6.1).

Limits (SPEC §6.1, D-020): login lockout per IP + email, emails per address,
and requests per IP address (slowapi decorators, under the route decorator).
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session, select

from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email
from app.security import client_ip, email_limiter, limiter, login_lockout, safe_redirect_path
from app.services import email_service
from app.services.auth_service import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    EmailTaken,
    authenticate,
    create_user,
    current_user,
    end_session,
    make_reset_token,
    make_verify_token,
    mark_verified,
    require_user,
    reset_password,
    start_session,
    user_from_reset_token,
    user_from_verify_token,
)
from app.templating import templates

router = APIRouter()

# Annotated types for the route parameters: the type and where FastAPI gets the value.
DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User | None, Depends(current_user)]
RequiredUser = Annotated[User, Depends(require_user)]
FormText = Annotated[str, Form()]

FULL_NAME_MAX_LENGTH = 200
LOGIN_ERROR = "Invalid email or password."
LOCKED_ERROR = "Too many attempts. Try again in 15 minutes."
RESENT_MESSAGE = "Sent again. Check your spam folder too."
WAIT_MESSAGE = "Please wait a few minutes before asking for another email."
# The same answer whether the email has an account or not (no account guessing).
FORGOT_MESSAGE = "If an account exists, we sent an email."
PASSWORD_CHANGED_MESSAGE = "Your password was changed. Sign in with your new password."

# Checks the email format (no DNS lookup), the same rules as a Pydantic EmailStr field.
email_adapter = TypeAdapter(EmailStr)


def redirect(url: str) -> RedirectResponse:
    # 303 "See Other": after a POST, the browser loads the next page with GET.
    return RedirectResponse(url, status_code=303)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def send_verification_email(background_tasks: BackgroundTasks, user: User) -> None:
    """Send the "Verify your email" link after the response is sent (D-028)."""
    email = email_service.render_email(
        "verify_email",
        to=user.email,
        full_name=user.full_name,
        path="/verify-email?token=" + make_verify_token(user),
    )
    background_tasks.add_task(email_service.send_email, email)


# --- Log in -------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: CurrentUser, next: str = "", reset: str = ""):
    if user is not None:
        return redirect(safe_redirect_path(next))
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "next": safe_redirect_path(next),
            "email": "",
            "notice": PASSWORD_CHANGED_MESSAGE if reset else None,
        },
    )


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("10/minute")
def login(
    request: Request,
    session: DbSession,
    email: FormText = "",
    password: FormText = "",
    next: FormText = "",
):
    ip = client_ip(request)
    if login_lockout.is_locked(ip, email):
        # While locked, the password is not even checked (also the right one).
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"next": safe_redirect_path(next), "email": email, "error": LOCKED_ERROR},
            status_code=429,
        )

    user = authenticate(session, email, password)
    if user is None:
        login_lockout.record_failure(ip, email)
        # One message for every failure: it never tells whether the email has an account.
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"next": safe_redirect_path(next), "email": email, "error": LOGIN_ERROR},
            status_code=400,
        )

    login_lockout.clear(ip, email)
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    start_session(request, user)
    return redirect(safe_redirect_path(next))


# --- Register -----------------------------------------------------------------------


def password_error(password: str) -> str | None:
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters."
    return None


def validate_registration(full_name: str, email: str, password: str, privacy: str) -> dict:
    """Check the register form. Returns {field: error message}; empty means valid."""
    errors = {}
    if not full_name:
        errors["full_name"] = "Enter your full name."
    elif len(full_name) > FULL_NAME_MAX_LENGTH:
        errors["full_name"] = f"Name must be at most {FULL_NAME_MAX_LENGTH} characters."

    try:
        if len(email) > EMAIL_MAX_LENGTH:
            raise ValueError
        email_adapter.validate_python(email)
    except (ValidationError, ValueError):
        errors["email"] = "Enter a valid email address."

    if error := password_error(password):
        errors["password"] = error

    if privacy != "yes":
        errors["privacy"] = "Please accept the privacy policy and terms to continue."
    return errors


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: CurrentUser):
    if user is not None:
        return redirect("/")
    return templates.TemplateResponse(request, "auth/register.html", {"form": {}, "errors": {}})


@router.post("/register", response_class=HTMLResponse)
@limiter.limit("5/hour")
def register(
    request: Request,
    session: DbSession,
    background_tasks: BackgroundTasks,
    full_name: FormText = "",
    email: FormText = "",
    password: FormText = "",
    privacy: FormText = "",
):
    full_name = full_name.strip()
    email = email.strip()
    # Shown again in the form after an error. The password never is.
    form = {"full_name": full_name, "email": email, "privacy": privacy == "yes"}

    errors = validate_registration(full_name, email, password, privacy)
    email_taken = False
    if not errors:
        try:
            user = create_user(session, email=email, password=password, full_name=full_name)
        except EmailTaken:
            email_taken = True
        else:
            # Logged in right away; the app can be used before the email is verified,
            # only AI features wait for it (SPEC §6.1).
            send_verification_email(background_tasks, user)
            start_session(request, user)
            return redirect("/check-inbox")

    return templates.TemplateResponse(
        request,
        "auth/register.html",
        {"form": form, "errors": errors, "email_taken": email_taken},
        status_code=400,
    )


# --- Email verification -------------------------------------------------------------


@router.get("/check-inbox", response_class=HTMLResponse)
def check_inbox(request: Request, user: RequiredUser, resent: str = ""):
    if user.is_verified:
        return redirect("/")
    return templates.TemplateResponse(
        request,
        "auth/check_inbox.html",
        {"email": user.email, "message": RESENT_MESSAGE if resent else None},
    )


@router.post("/resend-verification", response_class=HTMLResponse)
def resend_verification(request: Request, user: RequiredUser, background_tasks: BackgroundTasks):
    allowed = user.is_verified or email_limiter.allow(user.email)
    if allowed and not user.is_verified:
        send_verification_email(background_tasks, user)
    message = RESENT_MESSAGE if allowed else WAIT_MESSAGE

    if is_htmx(request):
        # Only the button is replaced, by this message (banner and Check your inbox page).
        # Always 200: htmx does not put 4xx responses into the page.
        return templates.TemplateResponse(
            request, "components/resend_result.html", {"message": message}
        )
    if not allowed:
        return templates.TemplateResponse(
            request,
            "auth/check_inbox.html",
            {"email": user.email, "message": message},
            status_code=429,
        )
    return redirect("/check-inbox?resent=1")


@router.get("/verify-email", response_class=HTMLResponse)
def verify_email(request: Request, session: DbSession, user: CurrentUser, token: str = ""):
    # token is optional: without it the page says the link is invalid (not a 422 error).
    # The link may be opened on another device, so it works without a session too.
    account = user_from_verify_token(session, token)
    if account is None:
        text = "Verification links work for 48 hours."
        show_resend = user is not None and not user.is_verified
        if show_resend:
            text += " Ask for a new one below."
        elif user is None:
            text += " Sign in to ask for a new one."
        return templates.TemplateResponse(
            request,
            "auth/message.html",
            {
                "heading": "This link has expired or is invalid",
                "text": text,
                "show_resend": show_resend,
            },
            status_code=400,
        )

    if account.is_verified:
        heading = "Email already verified"
    else:
        mark_verified(session, account)
        heading = "Email verified"
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {"heading": heading, "text": "AI features are now unlocked.", "show_resend": False},
    )


# --- Password reset -----------------------------------------------------------------
# The token is in the link (URL). "Referrer-Policy: no-referrer" stops the browser from
# sending that URL to any other page opened from here.
NO_REFERRER = {"Referrer-Policy": "no-referrer"}


def invalid_reset_link(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "This link has expired or is invalid",
            "text": "Reset links work for 1 hour and only once.",
            "action_url": "/forgot-password",
            "action_label": "Request a new link",
        },
        status_code=400,
        headers=NO_REFERRER,
    )


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"email": ""})


@router.post("/forgot-password", response_class=HTMLResponse)
@limiter.limit("5/hour")
def forgot_password(
    request: Request,
    session: DbSession,
    background_tasks: BackgroundTasks,
    email: FormText = "",
):
    email = email.strip()
    if not email:
        return templates.TemplateResponse(
            request,
            "auth/forgot_password.html",
            {"email": "", "error": "Enter your email address."},
            status_code=400,
        )

    # No account can have a longer email, so the database is skipped.
    user = None
    if len(email) <= EMAIL_MAX_LENGTH:
        user = session.exec(select(User).where(User.email == normalize_email(email))).first()
    # Over the per-address limit, nothing is sent, but the answer stays the same.
    if user is not None and email_limiter.allow(user.email):
        reset_email = email_service.render_email(
            "reset_password",
            to=user.email,
            full_name=user.full_name,
            path="/reset-password?token=" + make_reset_token(user),
        )
        # Sent after the response (D-028), so the answer takes as long with or
        # without an account.
        background_tasks.add_task(email_service.send_email, reset_email)

    return templates.TemplateResponse(
        request, "auth/forgot_password.html", {"email": email, "message": FORGOT_MESSAGE}
    )


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, session: DbSession, token: str = ""):
    if user_from_reset_token(session, token) is None:
        return invalid_reset_link(request)
    return templates.TemplateResponse(
        request, "auth/reset_password.html", {"token": token}, headers=NO_REFERRER
    )


@router.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request, session: DbSession, token: FormText = "", password: FormText = ""
):
    user = user_from_reset_token(session, token)
    if user is None:
        return invalid_reset_link(request)
    if error := password_error(password):
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {"token": token, "error": error},
            status_code=400,
            headers=NO_REFERRER,
        )

    # All sessions of this user end, this browser's too; they sign in with the new password.
    reset_password(session, user, password)
    login_lockout.clear_email(user.email)
    end_session(request)
    return redirect("/login?reset=1")


# --- Log out ------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request):
    # POST only (with a CSRF token), so another site can't log the user out with a link.
    end_session(request)
    return redirect("/login")
