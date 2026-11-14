"""Register, log in and log out (SPEC §6.1).

Email verification, password reset and the login lockout come in M1b.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH
from app.security import safe_redirect_path
from app.services.auth_service import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    EmailTaken,
    authenticate,
    create_user,
    current_user,
    end_session,
    start_session,
)
from app.templating import templates

router = APIRouter()

# Annotated types for the route parameters: the type and where FastAPI gets the value.
DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User | None, Depends(current_user)]
FormText = Annotated[str, Form()]

FULL_NAME_MAX_LENGTH = 200
LOGIN_ERROR = "Invalid email or password."

# Checks the email format (no DNS lookup), the same rules as a Pydantic EmailStr field.
email_adapter = TypeAdapter(EmailStr)


def redirect(url: str) -> RedirectResponse:
    # 303 "See Other": after a POST, the browser loads the next page with GET.
    return RedirectResponse(url, status_code=303)


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
            # Logged in right away. "Check your inbox" comes with email verification (M1b).
            start_session(request, user)
            return redirect("/")

    return templates.TemplateResponse(
        request,
        "auth/register.html",
        {"form": form, "errors": errors, "email_taken": email_taken},
        status_code=400,
    )


# --- Log out ------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request):
    # POST only (with a CSRF token), so another site can't log the user out with a link.
    end_session(request)
    return redirect("/login")
