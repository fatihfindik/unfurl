"""My profile (/profile, SPEC §6.4). Plan: docs/plans/M2a.md.

Personal info and Summary have their own routes. The list sections (Skills,
Languages, ...) share one set of routes, /profile/{section}/..., described by the
SECTIONS table below (D-031).

Every section is edited in place with htmx: "Edit" loads the section's form, "Save"
sends it and gets the section back. The responses are pieces of HTML (fragments),
not whole pages. A normal browser request for a fragment URL goes to /profile.

Form errors come back with status 200: htmx does not put 4xx responses into the
page, so a 400 would leave the user with no message at all.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, SQLModel
from starlette.datastructures import FormData

from app.db import get_session
from app.models import (
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Profile,
    Project,
    Skill,
    User,
)
from app.models.profile import LANGUAGE_LEVELS
from app.schemas.profile import (
    CertificationForm,
    EducationForm,
    ExperienceForm,
    LanguageForm,
    PersonalInfoForm,
    ProjectForm,
    SkillForm,
    SummaryForm,
    form_errors,
)
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
    # One context per list section, in page order (see SECTIONS below).
    main = [section_context(session, profile, SECTIONS[key]) for key in MAIN_COLUMN]
    side = [section_context(session, profile, SECTIONS[key]) for key in SIDE_COLUMN]
    return templates.TemplateResponse(
        request,
        "profile/index.html",
        {"profile": profile, "user": user, "main_sections": main, "side_sections": side},
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


# --- List sections (D-031) --------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """Everything the shared routes need to know about one list section."""

    key: str  # in the URL and in HTML ids: /profile/skills, id="profile-skills"
    title: str  # heading: "Skills"
    noun: str  # for buttons: "Add skill"
    model: type[SQLModel]  # the table
    form: type[BaseModel]  # the form check (app/schemas/profile.py)
    empty_text: str  # shown when the section has no rows yet
    list_fields: tuple[str, ...] = ()  # form fields sent several times (e.g. bullets)

    @property
    def item_template(self) -> str:
        return f"profile/_item_{self.key}.html"  # one row, read view

    @property
    def form_template(self) -> str:
        return f"profile/_form_{self.key}.html"  # one row, add / edit form


SECTIONS = {
    section.key: section
    for section in [
        Section(
            "experience",
            "Experience",
            "experience",
            Experience,
            ExperienceForm,
            "No experience yet. Add your jobs, newest first.",
            list_fields=("bullets",),
        ),
        Section(
            "education",
            "Education",
            "education",
            Education,
            EducationForm,
            "No education yet.",
        ),
        Section(
            "certifications",
            "Certifications",
            "certification",
            Certification,
            CertificationForm,
            "No certifications yet. Add courses or certificates that matter for your target roles.",
        ),
        Section(
            "projects",
            "Projects",
            "project",
            Project,
            ProjectForm,
            "No projects yet. Side projects and open source work show what you can build.",
        ),
        Section("skills", "Skills", "skill", Skill, SkillForm, "No skills yet."),
        Section(
            "languages", "Languages", "language", LanguageSkill, LanguageForm, "No languages yet."
        ),
    ]
}

# Where each section sits on the page (docs/prototype/Profile.dc.html).
MAIN_COLUMN = ["experience", "education", "certifications", "projects"]
SIDE_COLUMN = ["skills", "languages"]

TOO_MANY_ITEMS = "You can add up to 50 entries here. Delete one to add another."

# Choices for the dropdowns in the section forms.
LANGUAGE_LEVEL_CHOICES = [
    (level, "Native" if level == "native" else level) for level in LANGUAGE_LEVELS
]
SKILL_LEVEL_CHOICES = [(str(n), f"{n} of 5") for n in range(1, 6)]


def get_section(key: str) -> Section:
    if key not in SECTIONS:
        raise HTTPException(status_code=404)
    return SECTIONS[key]


def form_to_dict(data: FormData, section: Section) -> dict:
    """The submitted form as a dict. A field sent several times (one input per bullet)
    becomes a list; every other field keeps its single value."""
    values = {name: data.get(name) for name in data.keys()}
    for name in section.list_fields:
        values[name] = data.getlist(name)
    return values


def item_values(item: SQLModel, section: Section) -> dict:
    """A saved row as the text its edit form shows ("2021-03" for months, "" for empty)."""
    values = {}
    for name in section.form.model_fields:
        value = getattr(item, name, None)
        if isinstance(value, dt.date):
            value = value.strftime("%Y-%m")
        values[name] = "" if value is None else value
    if "ongoing" in section.form.model_fields:
        # No end date means "still there" (D-033).
        values["ongoing"] = item.end_date is None
    return values


def section_context(session: Session, profile: Profile, section: Section, new_form=None) -> dict:
    """What _section.html needs. `new_form` = {"values", "errors"} keeps the add form
    open (after an error); None shows the section closed."""
    return {
        "section": section,
        "items": profile_service.list_items(session, profile, section.model),
        "new_form": new_form,
        "language_levels": LANGUAGE_LEVEL_CHOICES,
        "skill_levels": SKILL_LEVEL_CHOICES,
    }


def item_context(section: Section, item, values=None, errors=None) -> dict:
    return {
        "section": section,
        "item": item,
        "values": values if values is not None else item_values(item, section),
        "errors": errors or {},
        "language_levels": LANGUAGE_LEVEL_CHOICES,
        "skill_levels": SKILL_LEVEL_CHOICES,
    }


# The order of the routes matters: FastAPI uses the first route whose path matches.
# The fixed paths (/profile/personal, /profile/summary) are defined above, so
# "personal" is never read as a section; "/new" comes before "/{item_id}", so "new"
# is never read as an id.


@router.get("/profile/{section_key}", response_class=HTMLResponse)
def section_view(request: Request, session: DbSession, user: RequiredUser, section_key: str):
    """The whole section, closed (also the Cancel of the add form)."""
    section = get_section(section_key)
    profile = profile_service.get_or_create_profile(session, user)
    return fragment(request, "profile/_section.html", section_context(session, profile, section))


@router.get("/profile/{section_key}/new", response_class=HTMLResponse)
def section_new(request: Request, session: DbSession, user: RequiredUser, section_key: str):
    section = get_section(section_key)
    profile = profile_service.get_or_create_profile(session, user)
    context = section_context(session, profile, section, new_form={"values": {}, "errors": {}})
    return fragment(request, "profile/_section.html", context)


@router.post("/profile/{section_key}", response_class=HTMLResponse)
def section_add(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, data: SubmittedForm
):
    section = get_section(section_key)
    profile = profile_service.get_or_create_profile(session, user)
    values = form_to_dict(data, section)
    try:
        form = section.form.model_validate(values)
        profile_service.add_item(
            session, profile, section.model, form.model_dump(exclude={"ongoing"})
        )
    except ValidationError as exc:
        new_form = {"values": values, "errors": form_errors(exc)}
        return fragment(
            request, "profile/_section.html", section_context(session, profile, section, new_form)
        )
    except profile_service.TooManyItems:
        new_form = {"values": values, "errors": {"form": TOO_MANY_ITEMS}}
        return fragment(
            request, "profile/_section.html", section_context(session, profile, section, new_form)
        )

    # The whole section comes back: the new row is in the list and the form is closed.
    return fragment(request, "profile/_section.html", section_context(session, profile, section))


@router.get("/profile/{section_key}/{item_id}", response_class=HTMLResponse)
def item_view(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, item_id: str
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    return fragment(request, section.item_template, item_context(section, item))


@router.get("/profile/{section_key}/{item_id}/edit", response_class=HTMLResponse)
def item_edit(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, item_id: str
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    return fragment(request, section.form_template, item_context(section, item))


@router.post("/profile/{section_key}/{item_id}", response_class=HTMLResponse)
def item_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    section_key: str,
    item_id: str,
    data: SubmittedForm,
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    values = form_to_dict(data, section)
    try:
        form = section.form.model_validate(values)
    except ValidationError as exc:
        return fragment(
            request, section.form_template, item_context(section, item, values, form_errors(exc))
        )

    profile_service.update_item(session, item, form.model_dump(exclude={"ongoing"}))
    return fragment(request, section.item_template, item_context(section, item))


@router.delete("/profile/{section_key}/{item_id}", response_class=HTMLResponse)
def item_delete(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, item_id: str
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    profile_service.delete_item(session, item)  # no Undo until M4 (D-035)
    profile = profile_service.get_or_create_profile(session, user)
    # The whole section (status 200, not 204: htmx does not swap on 204), so the
    # "No ... yet" text comes back when the last row is gone.
    return fragment(request, "profile/_section.html", section_context(session, profile, section))
