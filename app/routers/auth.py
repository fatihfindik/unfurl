"""Register, log in, log out and email verification (SPEC §6.1).

Password reset and the login lockout come later in M1b.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH
from app.security import safe_redirect_path
from app.services import email_service
from app.services.auth_service import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    EmailTaken,
    authenticate,
    create_user,
    current_user,
    end_session,
    make_verify_token,
    mark_verified,
    require_user,
    start_session,
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
RESENT_MESSAGE = "Sent again. Check your spam folder too."

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
def login_page(request: Request, user: CurrentUser, next: str = ""):
    if user is not None:
        return redirect(safe_redirect_path(next))
    return templates.TemplateResponse(
        request, "auth/login.html", {"next": safe_redirect_path(next), "email": ""}
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    session: DbSession,
    email: FormText = "",
    password: FormText = "",
    next: FormText = "",
):
    user = authenticate(session, email, password)
    if user is None:
        # One message for every failure: it never tells whether the email has an account.
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"next": safe_redirect_path(next), "email": email, "error": LOGIN_ERROR},
            status_code=400,
        )

    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    start_session(request, user)
    return redirect(safe_redirect_path(next))


# --- Register -----------------------------------------------------------------------


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

    if len(password) < PASSWORD_MIN_LENGTH:
        errors["password"] = f"Password must be at least {PASSWORD_MIN_LENGTH} characters."
    elif len(password) > PASSWORD_MAX_LENGTH:
        errors["password"] = f"Password must be at most {PASSWORD_MAX_LENGTH} characters."

    if privacy != "yes":
        errors["privacy"] = "Please accept the privacy policy and terms to continue."
    return errors


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: CurrentUser):
    if user is not None:
        return redirect("/")
    return templates.TemplateResponse(request, "auth/register.html", {"form": {}, "errors": {}})


@router.post("/register", response_class=HTMLResponse)
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
    if not user.is_verified:
        send_verification_email(background_tasks, user)
    if is_htmx(request):
        # Only the button is replaced, by this message (banner and Check your inbox page).
        return templates.TemplateResponse(
            request, "components/resend_result.html", {"message": RESENT_MESSAGE}
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


# --- Log out ------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request):
    # POST only (with a CSRF token), so another site can't log the user out with a link.
    end_session(request)
    return redirect("/login")
