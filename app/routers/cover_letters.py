"""Cover letters: new letter, editor with live preview, versions, delete (SPEC §6.6,
M8a). Plan: docs/plans/M8a.md.

The rules for masters, versions and locks are in app/services/cover_letter_service.py
and lineage.py; the routes here only read forms, call the service and pick the
template. The list is on the Documents page (app/routers/cvs.py, ?tab=letters).
"""

import json
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import CoverLetter, User
from app.models.cv import CV_LANGUAGES

# go_to and action_message are the same for CVs and letters.
from app.routers.cvs import MASTER_UNLOCKED, action_message, go_to
from app.schemas.cover_letter import LETTER_FIELDS, CoverLetterForm, NewCoverLetterForm
from app.schemas.cv import LANGUAGE_NAMES
from app.schemas.profile import form_errors
from app.security import limiter
from app.services import cover_letter_service as letters
from app.services import undo_service
from app.services.auth_service import require_user
from app.templating import set_flash, templates

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]
TOO_MANY_LETTERS = (
    f"You can have up to {letters.MAX_LETTERS_PER_USER} cover letters. Delete one to add another."
)
# A form has 14 fields (13 + edit_master); anything far beyond is not from our page.
MAX_FORM_FIELDS = 50


async def read_form_data(request: Request) -> FormData:
    return await request.form(max_fields=MAX_FORM_FIELDS)


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def new_letter_context(user: User, values=None, errors=None) -> dict:
    return {
        # A new letter starts in the language picked in Settings › Documents (D-129).
        "values": values or {"language": user.default_doc_language},
        "errors": errors or {},
        "language_choices": [(code, LANGUAGE_NAMES[code]) for code in CV_LANGUAGES],
    }


def paragraphs(text: str) -> list[str]:
    """The body as paragraphs: split at blank lines. Single line breaks stay inside
    a paragraph (shown with white-space: pre-line, never as HTML)."""
    return [part.strip("\n") for part in re.split(r"\n\s*\n", text) if part.strip()]


def preview_context(session: Session, letter: CoverLetter) -> dict:
    """What pdf/cover_letter.html needs (the preview and the PDF)."""
    return {
        "letter": letter,
        # Today while a draft, the day it was sent once locked (D-154, D-160).
        "letter_date": letters.letter_date(session, letter),
        "paragraphs": paragraphs(letter.body),
    }


def editor_context(
    session: Session,
    letter: CoverLetter,
    *,
    master_unlocked: bool,
    errors=None,
    master=None,
) -> dict:
    return preview_context(session, letter) | {
        "master": master,  # for the band "Copy based on …"
        "errors": errors or {},
        "language_names": LANGUAGE_NAMES,
        "master_unlocked": master_unlocked,
        # Never when locked; a master only after "Edit master" (D-048).
        "editable": not letter.is_locked and (not letter.is_master or master_unlocked),
    }


# --- New letter ---------------------------------------------------------------------


@router.post("/cover-letters", response_class=HTMLResponse)
def letter_create(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    """The "New cover letter" form in the modal (htmx). Errors come back as the form;
    a new letter opens right away."""
    values = {"title": data.get("title"), "language": data.get("language")}
    try:
        form = NewCoverLetterForm.model_validate(values)
    except ValidationError as exc:
        return new_letter_form(request, user, values, form_errors(exc))
    try:
        letter = letters.create_letter(session, user, form.title, form.language)
    except letters.TooManyDocuments:
        return new_letter_form(request, user, values, {"form": TOO_MANY_LETTERS})
    return go_to(request, f"/cover-letters/{letter.id}")


def new_letter_form(request: Request, user: User, values: dict, errors: dict) -> HTMLResponse:
    context = new_letter_context(user, values, errors)
    return templates.TemplateResponse(request, "cover_letters/_new_letter_form.html", context)


# --- Editor -------------------------------------------------------------------------


@router.get("/cover-letters/{letter_id}", response_class=HTMLResponse)
def letter_editor(
    request: Request, session: DbSession, user: RequiredUser, letter_id: str, edit_master: str = ""
):
    letter = letters.get_owned_letter(session, user, letter_id)
    unlocked = letter.is_master and edit_master == MASTER_UNLOCKED
    master = None
    if letter.parent_id is not None:
        master = letters.get_owned_letter(session, user, letter.parent_id)
    context = editor_context(session, letter, master_unlocked=unlocked, master=master)
    context["versions"] = letters.versions_in_lineage(session, user, letter)
    return templates.TemplateResponse(request, "cover_letters/editor.html", context)


@router.post("/cover-letters/{letter_id}", response_class=HTMLResponse)
def letter_save(
    request: Request, session: DbSession, user: RequiredUser, letter_id: str, data: SubmittedForm
):
    """Autosave from the editor form (htmx, 500 ms after the last change). Answers with
    the new preview. A field with a problem keeps its saved value and its message is
    shown; the other fields are saved (like the CV editor)."""
    letter = letters.get_owned_letter(session, user, letter_id)
    unlocked = data.get("edit_master") == MASTER_UNLOCKED
    values = {name: data.get(name) for name in LETTER_FIELDS}
    errors = {}
    try:
        form = CoverLetterForm.model_validate(values)
    except ValidationError as exc:
        errors = form_errors(exc)
        for name in errors:
            if name in values:
                values[name] = getattr(letter, name)
        try:
            form = CoverLetterForm.model_validate(values)
        except ValidationError:
            # Still wrong with the saved values: not a request from our page.
            context = editor_context(session, letter, master_unlocked=unlocked, errors=errors)
            context["nothing_saved"] = True
            return templates.TemplateResponse(request, "cover_letters/_preview.html", context)
    try:
        letters.update_letter(session, letter, form, master_unlocked=unlocked)
    except (letters.DocumentLocked, letters.MasterNotUnlocked):
        # The page shows these read-only, so only an old tab or a hand-made request.
        raise HTTPException(status_code=409) from None
    context = editor_context(session, letter, master_unlocked=unlocked, errors=errors)
    return templates.TemplateResponse(request, "cover_letters/_preview.html", context)


# --- Versions, refresh, delete ------------------------------------------------------


@router.post("/cover-letters/{letter_id}/versions", response_class=HTMLResponse)
def letter_new_version(request: Request, session: DbSession, user: RequiredUser, letter_id: str):
    """Save as new version (draft) and Duplicate as new version (locked copy)."""
    letter = letters.get_owned_letter(session, user, letter_id)
    try:
        new = letters.save_as_new_version(session, user, letter)
    except letters.TooManyDocuments:
        return action_message(request, TOO_MANY_LETTERS)
    except letters.NothingChanged:
        return action_message(
            request,
            "Nothing changed since this version. Make a change first, "
            "then save it as a new version.",
        )
    return go_to(request, f"/cover-letters/{new.id}")


@router.post("/cover-letters/{letter_id}/refresh", response_class=HTMLResponse)
def letter_refresh(
    request: Request, session: DbSession, user: RequiredUser, letter_id: str, data: SubmittedForm
):
    """Refresh the sender lines from the profile, after the confirmation modal
    (D-154). A master only when it was unlocked (D-048); a locked letter never."""
    letter = letters.get_owned_letter(session, user, letter_id)
    unlocked = data.get("edit_master") == MASTER_UNLOCKED
    try:
        letters.refresh_sender(session, user, letter, master_unlocked=unlocked)
    except (letters.DocumentLocked, letters.MasterNotUnlocked):
        raise HTTPException(status_code=409) from None
    suffix = f"?edit_master={MASTER_UNLOCKED}" if unlocked else ""
    return go_to(request, f"/cover-letters/{letter.id}{suffix}")


@router.delete("/cover-letters/{letter_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
def letter_delete(request: Request, session: DbSession, user: RequiredUser, letter_id: str):
    """Delete right away, with Undo in the toast (D-086). Locked letters never; a
    master only when it has no copies (D-047). Opens the letters tab with the toast."""
    letter = letters.get_owned_letter(session, user, letter_id)
    try:
        copy_id = letters.delete_letter(session, letter, user)
    except letters.DocumentLocked:
        raise HTTPException(status_code=409) from None
    except letters.MasterHasCopies as exc:
        word = "copy" if exc.copies == 1 else "copies"
        message = f"This master has {exc.copies} {word}. Delete the {word} first."
        toast = {"toast": {"message": message, "undo_url": "", "undo_token": ""}}
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(toast)})
    title = letter.title if len(letter.title) <= 60 else letter.title[:57] + "…"
    set_flash(
        request,
        f'"{title}" deleted.',
        "/undo/delete",
        undo_service.make_delete_token(user, copy_id),
    )
    return go_to(request, "/documents?tab=letters")
