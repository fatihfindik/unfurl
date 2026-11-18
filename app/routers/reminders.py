"""The "Turn off these emails" link in study reminder emails (D-098, D-112).

Works without a login: the signed token in the link says whose emails these are.
GET only shows a page with a button, because email programs and virus scanners open
links in emails by themselves; the POST (with the session's CSRF token) turns the
emails off. Only the emails: in-app reminders stay.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.security import limiter
from app.services import learning_service, reminder_email
from app.templating import templates

router = APIRouter()

DbSession = Annotated[Session, Depends(get_session)]

# The page carries a token: no Referer header to other sites (like the verify link),
# and no copy in a cache (security review M11b, L2).
TOKEN_PAGE_HEADERS = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def invalid_link(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/message.html",
        {
            "heading": "This link is no longer valid",
            "text": "You can turn reminders off in Settings.",
            "action_url": "/settings",
            "action_label": "Open Settings",
        },
        status_code=400,
        headers=TOKEN_PAGE_HEADERS,
    )


@router.get("/reminders/off", response_class=HTMLResponse)
@limiter.limit("30/minute")
def reminders_off_page(request: Request, session: DbSession, token: str = ""):
    user = reminder_email.user_from_off_token(session, token)
    if user is None:
        return invalid_link(request)
    return templates.TemplateResponse(
        request,
        "auth/reminders_off.html",
        {"token": token, "email": reminder_email.masked(user.email), "done": False},
        headers=TOKEN_PAGE_HEADERS,
    )


@router.post("/reminders/off", response_class=HTMLResponse)
@limiter.limit("10/minute")
def reminders_off(request: Request, session: DbSession, token: Annotated[str, Form()] = ""):
    # The token is checked again: the form could be sent without opening the page.
    user = reminder_email.user_from_off_token(session, token)
    if user is None:
        return invalid_link(request)
    prefs = learning_service.preferences(session, user)
    prefs.study_reminder_email = False
    session.add(prefs)
    session.commit()
    return templates.TemplateResponse(
        request,
        "auth/reminders_off.html",
        {"email": reminder_email.masked(user.email), "done": True},
        headers=TOKEN_PAGE_HEADERS,
    )
