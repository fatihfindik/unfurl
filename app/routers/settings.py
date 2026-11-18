"""Settings (`/settings`, SPEC §6.9). Notifications came with M11b (D-114), Documents
with M13a (D-129); the other sections come with their milestones (D-107, D-124).
The theme is saved here too, with a click and without a page.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import NotificationPreference, User
from app.models.cv import CV_LANGUAGES
from app.models.learning import WEEKDAYS
from app.schemas.cv import LANGUAGE_NAMES
from app.schemas.settings import (
    REMINDER_TIMES,
    SWITCHES,
    parse_notification_settings,
    time_value,
)
from app.security import limiter
from app.services import clock, settings_service
from app.services import learning_service as learning
from app.services.auth_service import SESSION_TIMEZONE, require_user
from app.templating import THEMES, set_flash, set_theme_cookie, templates

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

DAY_CHOICES = list(zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))
TIME_CHOICES = [(time_value(value), time_value(value)) for value in REMINDER_TIMES]
SAVED = "Settings saved."


async def read_form_data(request: Request) -> FormData:
    """The submitted form (an async dependency, so the route can stay a sync function)."""
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def form_context(
    user: User,
    prefs: NotificationPreference,
    values: dict | None = None,
    errors: dict | None = None,
) -> dict:
    """What settings/_notifications.html needs. `values` are the sent ones after an
    error, so nothing the user changed is lost."""
    zone = settings_service.user_zone(user)
    form = values or {
        "switches": {name: getattr(prefs, name) for name in SWITCHES},
        "study_reminder_time": time_value(prefs.study_reminder_time),
        "rest_days": prefs.rest_days,
        "timezone": zone,
    }
    return {
        "form": form,
        "errors": errors or {},
        "time_choices": TIME_CHOICES,
        "day_choices": DAY_CHOICES,
        "zone_choices": settings_service.zone_choices(zone),
    }


LANGUAGE_CHOICES = [(code, LANGUAGE_NAMES[code]) for code in CV_LANGUAGES]


def documents_context(user: User, value: str | None = None, error: str | None = None) -> dict:
    """What settings/_documents.html needs."""
    return {
        "doc_language": value or user.default_doc_language,
        "doc_language_error": error,
        "language_choices": LANGUAGE_CHOICES,
    }


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: DbSession, user: RequiredUser):
    prefs = learning.preferences(session, user)
    context = form_context(user, prefs) | documents_context(user)
    return templates.TemplateResponse(request, "settings/index.html", context)


@router.post("/settings/documents", response_class=HTMLResponse)
@limiter.limit("30/minute")
def save_documents(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    default_doc_language: Annotated[str, Form()] = "",
):
    """Settings › Documents (D-129): the language a new CV starts in."""
    if default_doc_language not in CV_LANGUAGES:
        context = documents_context(user, error="Choose one of the languages.")
        return templates.TemplateResponse(request, "settings/_documents.html", context)
    user.default_doc_language = default_doc_language
    session.add(user)
    session.commit()
    response = templates.TemplateResponse(
        request, "settings/_documents.html", documents_context(user)
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"toast": {"message": SAVED, "undo_url": "", "undo_token": ""}}
    )
    return response


@router.post("/settings/notifications", response_class=HTMLResponse)
@limiter.limit("30/minute")
def save_notifications(
    request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm
):
    parsed, errors = parse_notification_settings(data)
    if parsed is None:
        sent = {
            "switches": {name: data.get(name) == "yes" for name in SWITCHES},
            "study_reminder_time": str(data.get("study_reminder_time", "")),
            "rest_days": [day for day in data.getlist("rest_days") if day in WEEKDAYS],
            "timezone": str(data.get("timezone", "")),
        }
        return templates.TemplateResponse(
            request, "settings/_notifications.html", form_context(user, None, sent, errors)
        )

    old_zone = settings_service.user_zone(user)
    prefs = settings_service.save_notifications(session, user, parsed)
    request.session[SESSION_TIMEZONE] = user.timezone
    if parsed.timezone != old_zone:
        # Times on the page were shown in the old zone: load the page again, and show
        # the toast there (D-114).
        set_flash(request, SAVED)
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    clock.set_timezone(user.timezone)
    response = templates.TemplateResponse(
        request, "settings/_notifications.html", form_context(user, prefs)
    )
    response.headers["HX-Trigger"] = json.dumps(
        {"toast": {"message": SAVED, "undo_url": "", "undo_token": ""}}
    )
    return response


@router.post("/settings/theme")
@limiter.limit("30/minute")
def save_theme(
    request: Request, session: DbSession, user: RequiredUser, theme: Annotated[str, Form()] = ""
):
    """The theme picker (D-068): save the choice with the account, so it is the same on
    every device. The cookie is written too, for the pages without a login."""
    if theme not in THEMES:
        raise HTTPException(status_code=422)
    user.theme = theme
    session.add(user)
    session.commit()
    response = Response(status_code=204)
    set_theme_cookie(response, theme)
    return response
