"""The bell: unread count, the panel, opening a notification (SPEC §6.0, D-105).

The notifications themselves are written by the scheduler (app/scheduler.py); here
they are only read and marked read.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.security import limiter, safe_redirect_path
from app.services import clock
from app.services import notification_service as notes
from app.services.auth_service import current_user, require_user
from app.templating import templates

# Every route here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])
# The badge alone answers without a login (see badge()).
badge_router = APIRouter()

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]
OptionalUser = Annotated[User | None, Depends(current_user)]


@badge_router.get("/notifications/badge", response_class=HTMLResponse)
def badge(request: Request, session: DbSession, user: OptionalUser):
    """The unread count on the bell. The page asks for it when it loads, every two
    minutes and after "Mark all as read" (D-105).

    It is asked for without the user doing anything, so it must not act like a click:
    without a login it answers 204 (the badge disappears) instead of sending the page
    to /login while someone types in a form; and its answer never touches the session
    cookie (app/main.py, M11a review L1/L2)."""
    if user is None:
        return Response(status_code=204)
    count = notes.unread_count(session, user)
    return templates.TemplateResponse(request, "notifications/_badge.html", {"count": count})


def panel_response(request: Request, session: Session, user: User) -> HTMLResponse:
    now = clock.now()
    rows = [
        {"note": note, "when": notes.time_text(note.created_at, now)}
        for note in notes.latest(session, user)
    ]
    return templates.TemplateResponse(
        request,
        "notifications/_panel.html",
        {"rows": rows, "unread": any(row["note"].read_at is None for row in rows)},
    )


@router.get("/notifications/panel", response_class=HTMLResponse)
def panel(request: Request, session: DbSession, user: RequiredUser):
    """The newest notifications, loaded when the bell is opened."""
    return panel_response(request, session, user)


@router.post("/notifications/{notification_id}/open")
@limiter.limit("60/minute")
def open_notification(
    request: Request, session: DbSession, user: RequiredUser, notification_id: str
):
    """A click on a notification (a plain form, not htmx): mark it read and go to the
    page it is about. The link is always one of our own paths; safe_redirect_path
    keeps it that way even if a stored value were changed (D-105)."""
    note = notes.get_owned(session, user, notification_id)
    if note is None:
        raise HTTPException(status_code=404)
    notes.mark_read(session, note, clock.now())
    return RedirectResponse(safe_redirect_path(note.link_url), status_code=303)


@router.post("/notifications/read-all", response_class=HTMLResponse)
@limiter.limit("60/minute")
def read_all(request: Request, session: DbSession, user: RequiredUser):
    """ "Mark all as read": the panel comes back redrawn, and the badge reloads itself
    on the "notifications-changed" event."""
    notes.mark_all_read(session, user, clock.now())
    response = panel_response(request, session, user)
    response.headers["HX-Trigger"] = json.dumps({"notifications-changed": True})
    return response
