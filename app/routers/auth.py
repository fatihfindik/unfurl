"""Register, log in, log out, email verification and password reset (SPEC §6.1).

Limits (SPEC §6.1, D-020): login lockout per IP + email, emails per address,
and requests per IP address (slowapi decorators, under the route decorator).
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.models.user import EMAIL_MAX_LENGTH, normalize_email
from app.routers.reminders import TOKEN_PAGE_HEADERS
from app.security import (
    client_ip,
    email_limiter,
    limiter,
    login_lockout,
    safe_redirect_path,
)
from app.services import email_service
from app.services.auth_service import (
    FULL_NAME_MAX_LENGTH,
    SESSION_ACCOUNT,
    SESSION_USER_ID,
    SESSION_VERSION,
    EmailTaken,
    account_stamp,
    authenticate,
    change_email,
    create_user,
    current_user,
    email_change_from_token,
    email_is_valid,
    end_session,
    make_reset_token,
    make_undo_email_token,
    make_verify_token,
    mark_verified,
    masked_address,
    old_address_can_undo,
    password_error,
    remember_timezone,
    require_user,
    reset_password,
    sign_out,
    start_session,
    undo_email_change,
    undo_email_from_token,
    user_from_reset_token,
    user_from_verify_token,
)
from app.templating import is_htmx, set_flash, set_theme_cookie, templates

router = APIRouter()

# Annotated types for the route parameters: the type and where FastAPI gets the value.
DbSession = Annotated[Session, Depends(get_session)]
CurrentUser = Annotated[User | None, Depends(current_user)]
RequiredUser = Annotated[User, Depends(require_user)]
FormText = Annotated[str, Form()]

LOGIN_ERROR = "Invalid email or password."
LOCKED_ERROR = "Too many attempts. Try again later, or reset your password."
RESENT_MESSAGE = "Email sent. Check your spam folder too."
WAIT_MESSAGE = "Please wait a few minutes before asking for another email."
# The same answer whether the email has an account or not (no account guessing).
FORGOT_MESSAGE = "If an account exists, we sent an email."
PASSWORD_CHANGED_MESSAGE = "Your password was changed. Sign in with your new password."
ACCOUNT_DELETED_MESSAGE = "Your account and all your data were deleted."
EMAIL_CHANGED_MESSAGE = "Your email was changed. Sign in with the new address."

# After a password reset: the email this browser may sign in with past the per-email
# lock, and until when (D-149).
LOCKOUT_EXEMPT = "lockout_exempt"
RESET_EXEMPTION = dt.timedelta(minutes=15)


def reset_exempts(request: Request, email: str) -> bool:
    exempt = request.session.get(LOCKOUT_EXEMPT)
    if not isinstance(exempt, dict) or exempt.get("email") != normalize_email(email):
        return False
    try:
        until = dt.datetime.fromisoformat(str(exempt.get("until")))
    except ValueError:
        return False
    return utcnow() < until


# Verify and reset links carry their token in the URL. "Referrer-Policy: no-referrer"
# stops the browser from sending that URL to any other page opened from there.
NO_REFERRER = {"Referrer-Policy": "no-referrer"}


def redirect(url: str) -> RedirectResponse:
    # 303 "See Other": after a POST, the browser loads the next page with GET.
    return RedirectResponse(url, status_code=303)


def send_verification_email(background_tasks: BackgroundTasks, user: User) -> None:
    """Send the "Verify your email" link after the response is sent (D-028).

    No name in this email: anyone can register with someone else's address, and the
    name would let them put their own text into a real Unfurl email.
    """
    email = email_service.render_email(
        "verify_email", to=user.email, path="/verify-email?token=" + make_verify_token(user)
    )
    background_tasks.add_task(email_service.send_email, email)


# --- Log in -------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user: CurrentUser,
    next: str = "",
    reset: str = "",
    deleted: str = "",
    email_changed: str = "",
):
    if user is not None:
        return redirect(safe_redirect_path(next))
    notice = None
    if reset:
        notice = PASSWORD_CHANGED_MESSAGE
    elif deleted:
        notice = ACCOUNT_DELETED_MESSAGE  # after Delete my account (D-131)
    elif email_changed:
        notice = EMAIL_CHANGED_MESSAGE  # after /confirm-email in another browser (D-134)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "next": safe_redirect_path(next),
            "email": "",
            "notice": notice,
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
    timezone: FormText = "",
):
    ip = client_ip(request)
    # After a password reset this browser gets past the per-email lock (D-149).
    per_email = not reset_exempts(request, email)
    if login_lockout.is_locked(ip, email, per_email=per_email):
        # While locked, the password is not even checked (also the right one).
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"next": safe_redirect_path(next), "email": email, "error": LOCKED_ERROR},
            status_code=429,
        )

    user = authenticate(session, email, password)
    if user is None:
        login_lockout.record_failure(ip, email, per_email=per_email)
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
    # The browser's time zone (a hidden field app.js fills), only if none is saved (D-108).
    remember_timezone(session, user, timezone)
    start_session(request, user)
    response = redirect(safe_redirect_path(next))
    # The account's theme wins over this browser's old choice (D-068); the cookie keeps
    # the login and legal pages in the same colours after logging out.
    set_theme_cookie(response, user.theme)
    return response


# --- Register -----------------------------------------------------------------------


def validate_registration(full_name: str, email: str, password: str, privacy: str) -> dict:
    """Check the register form. Returns {field: error message}; empty means valid."""
    errors = {}
    if not full_name:
        errors["full_name"] = "Enter your full name."
    elif len(full_name) > FULL_NAME_MAX_LENGTH:
        errors["full_name"] = f"Name must be at most {FULL_NAME_MAX_LENGTH} characters."

    if not email_is_valid(email):
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
    timezone: FormText = "",
):
    full_name = full_name.strip()
    email = email.strip()
    # Shown again in the form after an error. The password never is.
    form = {"full_name": full_name, "email": email, "privacy": privacy == "yes"}

    errors = validate_registration(full_name, email, password, privacy)
    email_taken = False
    if not errors:
        try:
            user = create_user(
                session, email=email, password=password, full_name=full_name, timezone=timezone
            )
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
        {"email": user.email, "message": RESENT_MESSAGE if resent else None, "sent": bool(resent)},
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
            request, "components/resend_result.html", {"message": message, "sent": allowed}
        )
    if not allowed:
        return templates.TemplateResponse(
            request,
            "auth/check_inbox.html",
            {"email": user.email, "message": message, "sent": False},
            status_code=429,
        )
    return redirect("/check-inbox?resent=1")


def invalid_verify_link(request: Request, user: User | None) -> HTMLResponse:
    """The page for a verification link that is expired, changed or not ours. With a
    session, a resend button; without one, a hint to sign in."""
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
        headers=TOKEN_PAGE_HEADERS,
    )


def verified_page(request: Request, heading: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {"heading": heading, "text": "AI features are now unlocked.", "show_resend": False},
        headers=TOKEN_PAGE_HEADERS,
    )


@router.get("/verify-email", response_class=HTMLResponse)
@limiter.limit("30/minute")
def verify_email_page(request: Request, session: DbSession, user: CurrentUser, token: str = ""):
    """The link in the verification email. Opening it changes nothing: email programs
    and link scanners open links by themselves (D-146); the button does.
    token is optional: without it the page says the link is invalid (not a 422 error).
    The link may be opened on another device, so it works without a session too."""
    account = user_from_verify_token(session, token)
    if account is None:
        return invalid_verify_link(request, user)
    if account.is_verified:
        return verified_page(request, "Email already verified")
    return templates.TemplateResponse(
        request,
        "auth/verify_email.html",
        # The address only, never the name: whoever signed up chose the name.
        {
            "token": token,
            "email": account.email,
            # Signed in as someone else: say that this verifies the link's account.
            "other_account": user is not None and user.id != account.id,
        },
        headers=TOKEN_PAGE_HEADERS,
    )


@router.post("/verify-email", response_class=HTMLResponse)
@limiter.limit("10/minute")
def verify_email(request: Request, session: DbSession, user: CurrentUser, token: FormText = ""):
    account = user_from_verify_token(session, token)  # checked again: the form can be sent alone
    if account is None:
        return invalid_verify_link(request, user)
    if account.is_verified:
        return verified_page(request, "Email already verified")
    mark_verified(session, account)
    return verified_page(request, "Email verified")


# --- Password reset -----------------------------------------------------------------


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


# Stands in for a missing account in forgot_password (never saved). ".invalid" is a
# domain that can't exist (RFC 2606).
NO_ACCOUNT = User(
    id=0,
    email="no-account@unfurl.invalid",
    password_hash="no-account",
    full_name="",
    privacy_accepted_at=utcnow(),
)


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
    # The same work with and without an account (#15): a token is signed, the email
    # rendered and the limiter counted for a stand-in user, then nothing is sent. So
    # the answer's timing doesn't tell whether the address has an account.
    account = user or NO_ACCOUNT
    reset_email = email_service.render_email(
        "reset_password",
        to=account.email,
        full_name=account.full_name,
        path="/reset-password?token=" + make_reset_token(account),
    )
    # Over the per-address limit, nothing is sent, but the answer stays the same.
    allowed = email_limiter.allow(account.email)
    if user is not None and allowed:
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
    if not reset_password(session, user, password):
        return invalid_reset_link(request)  # the same link was used a moment earlier (#16)
    login_lockout.clear_email(user.email)
    end_session(request)
    # Whoever locked the email can lock it again within seconds; this browser gets
    # past that lock for a while, so the owner can sign in (D-149).
    request.session[LOCKOUT_EXEMPT] = {
        "email": normalize_email(user.email),
        "until": (utcnow() + RESET_EXEMPTION).isoformat(),
    }
    return redirect("/login?reset=1")


# --- Log out ------------------------------------------------------------------------


@router.post("/logout")
def logout(request: Request, session: DbSession):
    # POST only (with a CSRF token), so another site can't log the user out with a link.
    # Every device is signed out, not only this browser (D-128).
    sign_out(session, request)
    return redirect("/login")


# --- Confirm a new email address (#56, D-134) ---------------------------------------------


def invalid_email_link(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "This link is no longer valid",
            "text": "Email change links work for 24 hours and only once. A new password or "
            "signing out also cancels them. Ask for a new link in Settings.",
            "action_url": "/settings",
            "action_label": "Open Settings",
        },
        status_code=400,
        headers=TOKEN_PAGE_HEADERS,
    )


@router.get("/confirm-email", response_class=HTMLResponse)
@limiter.limit("30/minute")
def confirm_email_page(request: Request, session: DbSession, token: str = ""):
    """The link in the email to the new address. Opening it changes nothing (email
    programs open links by themselves, D-112); the button does."""
    found = email_change_from_token(session, token)
    if found is None:
        return invalid_email_link(request)
    user, new_email = found
    return templates.TemplateResponse(
        request,
        "auth/email_change.html",
        # Whose account: the owner of the new address can tell whether they asked
        # (security review #56, L3). The old address is in the token anyway.
        {"token": token, "new_email": new_email, "old_email": masked_address(user.email)},
        headers=TOKEN_PAGE_HEADERS,
    )


@router.post("/confirm-email", response_class=HTMLResponse)
@limiter.limit("10/minute")
def confirm_email(
    request: Request,
    session: DbSession,
    background_tasks: BackgroundTasks,
    token: FormText = "",
):
    found = email_change_from_token(session, token)  # checked again: the form can be sent alone
    if found is None:
        return invalid_email_link(request)
    user, new_email = found
    old_email, was_verified = user.email, user.is_verified
    old_can_undo = old_address_can_undo(user.email_changed_at, utcnow())
    # Is this browser signed in as this user? Then it stays signed in (D-134).
    same_user = (
        request.session.get(SESSION_USER_ID) == user.id
        and request.session.get(SESSION_VERSION) == user.session_version
        and request.session.get(SESSION_ACCOUNT) == account_stamp(user)
    )
    result = change_email(session, user, new_email)
    if result == "taken":
        return templates.TemplateResponse(
            request,
            "auth/message.html",
            {
                "heading": "This email is already in use",
                "text": "Another account uses this address now. Your email was not changed.",
                "action_url": "/settings",
                "action_label": "Open Settings",
            },
            status_code=409,
            headers=TOKEN_PAGE_HEADERS,
        )
    if result != "ok":
        return invalid_email_link(request)

    if was_verified:
        notice = email_service.render_email(
            "email_changed",
            to=old_email,
            full_name=user.full_name,
            new_email=masked_address(new_email),
            # "This wasn't me" (#60, D-147): only to an address the owner proved, and
            # not to one set by a change less than 7 days ago (security review H1).
            undo_path=(
                "/undo-email-change?token=" + make_undo_email_token(user, old_email)
                if old_can_undo
                else ""
            ),
        )
        background_tasks.add_task(email_service.send_email, notice)
    if same_user:
        request.session[SESSION_VERSION] = user.session_version
        set_flash(request, f"Your email is now {new_email}.")
        return redirect("/settings")
    return redirect("/login?email_changed=1")


# --- Undo an email change from the old address (#60, D-147) -------------------------------


def invalid_undo_link(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "This link is no longer valid",
            "text": "Undo links work for 7 days after an email change, and only while the "
            "account doesn't use that address again. If you still can't get into your "
            "account, contact us at the address on the Imprint page.",
            "action_url": "/imprint",
            "action_label": "Open Imprint",
        },
        status_code=400,
        headers=TOKEN_PAGE_HEADERS,
    )


def old_email_taken_page(request: Request, old_email: str) -> HTMLResponse:
    """Someone signed up with the old address after the change. Its owner can still
    get it back: they own the inbox (D-147)."""
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "This email is used by another account",
            "text": f"Another Unfurl account uses {old_email} now. Because you own that "
            "address, you can free it: use Forgot password with it, sign in, delete that "
            "account in Settings › Account, then open this link again (it works for 7 "
            "days after the change).",
            "action_url": "/forgot-password",
            "action_label": "Forgot password",
        },
        status_code=409,
        headers=TOKEN_PAGE_HEADERS,
    )


@router.get("/undo-email-change", response_class=HTMLResponse)
@limiter.limit("30/minute")
def undo_email_change_page(request: Request, session: DbSession, token: str = ""):
    """The "This wasn't me" link in the email to the old address. Opening it changes
    nothing (email programs open links by themselves, D-112); the button does."""
    found = undo_email_from_token(session, token)
    if found is None:
        return invalid_undo_link(request)
    user, old_email = found
    return templates.TemplateResponse(
        request,
        "auth/undo_email_change.html",
        # The old address in full (the link went there), the current one masked: it may
        # be the address of whoever took the account (D-134).
        {"token": token, "old_email": old_email, "current_email": masked_address(user.email)},
        headers=TOKEN_PAGE_HEADERS,
    )


@router.post("/undo-email-change", response_class=HTMLResponse)
@limiter.limit("10/minute")
def undo_email_change_submit(
    request: Request,
    session: DbSession,
    background_tasks: BackgroundTasks,
    token: FormText = "",
):
    found = undo_email_from_token(session, token)  # checked again: the form can be sent alone
    if found is None:
        return invalid_undo_link(request)
    user, old_email = found
    replaced = user.email
    result = undo_email_change(session, user, old_email)
    if result == "taken":
        return old_email_taken_page(request, old_email)
    if result != "ok":
        # The address changed between the check and the update. The link may still
        # be valid: say so instead of "no longer valid" (security review L2).
        return templates.TemplateResponse(
            request,
            "auth/message.html",
            {
                "heading": "Something changed",
                "text": "The account changed while you were undoing. Open the link from "
                "the email again.",
            },
            status_code=409,
            headers=TOKEN_PAGE_HEADERS,
        )

    # The owner proved the address: old login failures for it don't count any more.
    login_lockout.clear_email(old_email)
    # Counted towards the address's limit, but always sent: the undo is single use
    # and its own limit is 10 per minute.
    email_limiter.allow(old_email)
    reset_email = email_service.render_email(
        "reset_password",
        to=old_email,
        full_name=user.full_name,
        path="/reset-password?token=" + make_reset_token(user),  # from the new hash
    )
    background_tasks.add_task(email_service.send_email, reset_email)
    # The replaced address hears about it too: if a former employer's mail server
    # undid a real change, the owner can contact us (security review M1).
    changed_back = email_service.render_email(
        "email_changed_back", to=replaced, full_name=user.full_name
    )
    background_tasks.add_task(email_service.send_email, changed_back)
    request.session.clear()  # signed out here too, like everywhere else
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "Your email is back",
            "text": f"Your account uses {old_email} again. Every device is signed out and "
            "the old password no longer works. Use the link we sent to "
            f"{old_email} to set a new password (it works for 1 hour; after that use "
            "Forgot password).",
            "action_url": "/forgot-password",
            "action_label": "Forgot password",
        },
        headers=TOKEN_PAGE_HEADERS,
    )
