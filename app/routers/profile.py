"""My profile (/profile, SPEC §6.4). Plan: docs/plans/M2a.md.

Every section is edited in place with htmx: "Edit" loads the section's form, "Save"
sends it and gets the section back. The responses are pieces of HTML (fragments),
not whole pages. A normal browser request for a fragment URL goes to /profile.

Form errors come back with status 200: htmx does not put 4xx responses into the
page, so a 400 would leave the user with no message at all.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import Profile, User
from app.schemas.profile import PersonalInfoForm, SummaryForm, form_errors
from app.services import profile_service
from app.services.auth_service import require_user
from app.templating import is_htmx, templates

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]


async def read_form_data(request: Request) -> FormData:
    """The submitted form. An async dependency, so the routes themselves can stay normal
    (sync) functions that FastAPI runs in a worker thread, next to the database calls."""
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def fragment(request: Request, template: str, context: dict) -> HTMLResponse:
    """A piece of the profile page for htmx; a normal page load goes to /profile instead."""
    if not is_htmx(request):
        return RedirectResponse("/profile", status_code=303)
    return templates.TemplateResponse(request, template, context)


# --- The page ---------------------------------------------------------------------


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    return templates.TemplateResponse(
        request, "profile/index.html", {"profile": profile, "user": user}
    )


# --- Personal info ----------------------------------------------------------------


def personal_values(user: User, profile: Profile) -> dict:
    """What the Personal info form shows: the saved values as text."""
    fields = [name for name in PersonalInfoForm.model_fields if name != "full_name"]
    values = {name: getattr(profile, name) or "" for name in fields}
    values["full_name"] = user.full_name  # the name is stored on the account
    values["date_of_birth"] = profile.date_of_birth.isoformat() if profile.date_of_birth else ""
    return values


@router.get("/profile/personal", response_class=HTMLResponse)
def personal_view(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    return fragment(request, "profile/_personal.html", {"profile": profile, "user": user})


@router.get("/profile/personal/edit", response_class=HTMLResponse)
def personal_edit(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    context = {"values": personal_values(user, profile), "errors": {}}
    return fragment(request, "profile/_personal_form.html", context)


@router.post("/profile/personal", response_class=HTMLResponse)
def personal_save(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    profile = profile_service.get_or_create_profile(session, user)
    try:
        form = PersonalInfoForm.model_validate(dict(data))
    except ValidationError as exc:
        # Show the form again with what the user typed and a message per field.
        context = {"values": dict(data), "errors": form_errors(exc)}
        return fragment(request, "profile/_personal_form.html", context)

    profile_service.update_personal_info(session, user, profile, form)
    return fragment(request, "profile/_personal.html", {"profile": profile, "user": user})


# --- Summary ----------------------------------------------------------------------


@router.get("/profile/summary", response_class=HTMLResponse)
def summary_view(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    return fragment(request, "profile/_summary.html", {"profile": profile})


@router.get("/profile/summary/edit", response_class=HTMLResponse)
def summary_edit(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    context = {"values": {"summary": profile.summary or ""}, "errors": {}}
    return fragment(request, "profile/_summary_form.html", context)


@router.post("/profile/summary", response_class=HTMLResponse)
def summary_save(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    profile = profile_service.get_or_create_profile(session, user)
    try:
        form = SummaryForm.model_validate(dict(data))
    except ValidationError as exc:
        context = {"values": dict(data), "errors": form_errors(exc)}
        return fragment(request, "profile/_summary_form.html", context)

    profile_service.update_summary(session, profile, form)
    return fragment(request, "profile/_summary.html", {"profile": profile})
