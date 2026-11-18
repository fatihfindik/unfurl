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
import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ValidationError
from sqlmodel import Session, SQLModel
from starlette.datastructures import FormData, UploadFile

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
    MAX_BULLETS,
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
from app.services import photo_service, profile_service, undo_service
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


def changed(response):
    """Mark a response that changed the profile. htmx fires the "profile-changed" event
    on the page, and the Profile strength card reloads itself (D-040)."""
    response.headers["HX-Trigger"] = "profile-changed"
    return response


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
        {
            "profile": profile,
            "user": user,
            "strength": profile_service.profile_strength(session, profile),
            "main_sections": main,
            "side_sections": side,
        },
    )


# --- Profile strength (SPEC §6.4) -------------------------------------------------


@router.get("/profile/strength", response_class=HTMLResponse)
def strength_view(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    strength = profile_service.profile_strength(session, profile)
    return fragment(request, "profile/_strength.html", {"strength": strength})


# --- Photo (D-038, D-039) ---------------------------------------------------------


@dataclass
class PhotoUpload:
    """What came in with a photo upload: the file, or a message saying why not."""

    filename: str = ""
    data: bytes = b""
    error: str | None = None


async def read_photo_upload(request: Request) -> PhotoUpload:
    """The uploaded photo, with its size checked BEFORE the body is read.

    The usual SubmittedForm dependency would read the whole request first, so a huge
    upload would already be on the disk. Here the Content-Length header is checked
    first; a request that is too big is answered without reading it. (The CSRF check
    takes the token from the htmx header, so it doesn't read the body either.)
    """
    length = request.headers.get("content-length", "")
    if not (length.isascii() and length.isdigit()):
        return PhotoUpload(error=photo_service.UNREADABLE)
    if int(length) > photo_service.MAX_REQUEST_BYTES:
        return PhotoUpload(error=photo_service.TOO_BIG)

    form = await request.form(max_files=1, max_fields=5)
    upload = form.get("photo")
    if not isinstance(upload, UploadFile):
        return PhotoUpload(error=photo_service.UNREADABLE)
    # One byte more than allowed is enough to know the file is too big.
    data = await upload.read(photo_service.MAX_BYTES + 1)
    return PhotoUpload(filename=upload.filename or "", data=data)


def photo_context(user: User, profile: Profile, error: str | None = None) -> dict:
    return {"user": user, "profile": profile, "photo_error": error}


@router.get("/profile/photo")
def photo_file(session: DbSession, user: RequiredUser):
    """The signed-in user's own photo, for <img src>. There is no id in the URL, so
    nobody can ask for another user's photo (SPEC §10.1)."""
    profile = profile_service.get_or_create_profile(session, user)
    path = photo_service.photo_file(profile.photo_path) if profile.photo_path else None
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=photo_service.media_type(profile.photo_path),
        headers={
            # No copy stays in the browser: on a shared computer the photo is gone
            # after signing out (security review #10, L4).
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/profile/photo", response_class=HTMLResponse)
def photo_upload(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    upload: Annotated[PhotoUpload, Depends(read_photo_upload)],
):
    profile = profile_service.get_or_create_profile(session, user)
    error = upload.error
    if error is None:
        try:
            new_path = photo_service.save_photo(upload.data, upload.filename)
        except photo_service.PhotoError as exc:
            error = str(exc)
    if error is not None:
        # 200 with the message: htmx does not show 4xx answers.
        return fragment(request, "profile/_photo.html", photo_context(user, profile, error))

    try:
        old_path = profile_service.set_photo(session, profile, new_path)
    except Exception:
        # The new file was written but the database didn't take it: remove the file,
        # so nothing is left on the disk that no profile points to.
        photo_service.delete_photo_file(new_path)
        raise
    # The old file goes only after the new path is saved, so a failed save never
    # leaves the profile pointing at a deleted file.
    photo_service.delete_photo_file(old_path)
    return changed(fragment(request, "profile/_photo.html", photo_context(user, profile)))


@router.delete("/profile/photo", response_class=HTMLResponse)
def photo_delete(request: Request, session: DbSession, user: RequiredUser):
    profile = profile_service.get_or_create_profile(session, user)
    old_path = profile_service.set_photo(session, profile, None)
    photo_service.delete_photo_file(old_path)
    return changed(fragment(request, "profile/_photo.html", photo_context(user, profile)))


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
    return changed(fragment(request, "profile/_personal.html", {"profile": profile, "user": user}))


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
    return changed(fragment(request, "profile/_summary.html", {"profile": profile}))


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
    sortable: bool = False  # the user can change the order (↑ / ↓, drag and drop)

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
            sortable=True,
        ),
        Section(
            "education",
            "Education",
            "education",
            Education,
            EducationForm,
            "No education yet.",
            sortable=True,
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
            sortable=True,
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
        # One more than allowed is enough for the form to say "too many". A request
        # with thousands of bullets is not sent back as thousands of inputs.
        values[name] = data.getlist(name)[: MAX_BULLETS + 1]
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


def row_context(session: Session, user: User, section: Section, item) -> dict:
    """item_context for a row shown on its own (after Save or Cancel): it also says
    whether the row is first or last, so the right ↑ / ↓ button is turned off."""
    context = item_context(section, item)
    if section.sortable:
        profile = profile_service.get_or_create_profile(session, user)
        ids = [row.id for row in profile_service.list_items(session, profile, section.model)]
        context["is_first"] = ids[0] == item.id
        context["is_last"] = ids[-1] == item.id
    return context


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
# The fixed paths (/profile/personal, /profile/summary, /profile/strength,
# /profile/photo) are defined above, so "personal" is never read as a section;
# "/new" comes before "/{item_id}", so "new" is never read as an id.


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
    context = section_context(session, profile, section)
    return changed(fragment(request, "profile/_section.html", context))


# Before the /{item_id} routes, so "order" is never read as a row id.
@router.post("/profile/{section_key}/order", response_class=HTMLResponse)
def section_order(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, data: SubmittedForm
):
    """Drag and drop: save the new order of a whole section (app.js, initSortable)."""
    section = get_section(section_key)
    if not section.sortable:
        raise HTTPException(status_code=404)
    profile = profile_service.get_or_create_profile(session, user)

    raw_ids = data.getlist("ids")
    moved = False
    # More ids than a section can have, or something that isn't an id: the request
    # didn't come from our page, so it changes nothing (the section comes back as it is).
    if len(raw_ids) <= profile_service.MAX_ITEMS_PER_SECTION and all(
        # At most 10 digits: ids fit in 32 bits, and int() refuses very long numbers.
        value.isascii() and value.isdigit() and len(value) <= 10
        for value in raw_ids
    ):
        ids = [int(value) for value in raw_ids]
        moved = profile_service.reorder_items(session, profile, section.model, ids)

    response = fragment(
        request, "profile/_section.html", section_context(session, profile, section)
    )
    return changed(response) if moved else response


@router.get("/profile/{section_key}/{item_id}", response_class=HTMLResponse)
def item_view(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, item_id: str
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    return fragment(request, section.item_template, row_context(session, user, section, item))


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
    context = row_context(session, user, section, item)
    return changed(fragment(request, section.item_template, context))


@router.delete("/profile/{section_key}/{item_id}", response_class=HTMLResponse)
def item_delete(
    request: Request, session: DbSession, user: RequiredUser, section_key: str, item_id: str
):
    section = get_section(section_key)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    copy_id = profile_service.delete_item(session, item, user, section.key)
    profile = profile_service.get_or_create_profile(session, user)
    # The whole section (status 200, not 204: htmx does not swap on 204), so the
    # "No ... yet" text comes back when the last row is gone. The toast offers Undo
    # (D-086): it reloads the page with the row back.
    context = section_context(session, profile, section)
    response = fragment(request, "profile/_section.html", context)
    response.headers["HX-Trigger"] = json.dumps(
        {
            "profile-changed": True,
            "toast": {
                "message": f"{section.noun.capitalize()} deleted.",
                "undo_url": "/undo/delete",
                "undo_token": undo_service.make_delete_token(user, copy_id),
            },
        }
    )
    return response


@router.post("/profile/{section_key}/{item_id}/move", response_class=HTMLResponse)
def item_move(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    section_key: str,
    item_id: str,
    data: SubmittedForm,
):
    """↑ / ↓: move a row one place (experience, education, projects only)."""
    section = get_section(section_key)
    if not section.sortable:
        raise HTTPException(status_code=404)
    item = profile_service.get_owned_item(session, user, section.model, item_id)
    profile = profile_service.get_or_create_profile(session, user)
    direction = data.get("direction")
    moved = profile_service.move_item(session, profile, section.model, item, direction)

    # The whole section comes back in the new order. `focus` puts the keyboard focus
    # back on the button that was pressed (htmx focuses the autofocus element).
    context = section_context(session, profile, section)
    context["focus"] = (item.id, direction)
    response = fragment(request, "profile/_section.html", context)
    return changed(response) if moved else response
