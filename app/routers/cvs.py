"""Documents page and CV documents (SPEC §6.5, M3a). Plan: docs/plans/M3a.md.

The rules for masters, versions and locks are in app/services/document_service.py;
the routes here only read forms, call the service and pick the template.
"""

import base64
import binascii
import json
import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, col, select
from starlette.datastructures import FormData

from app import master_unlock
from app.billing.entitlements import allowed_templates
from app.db import get_session
from app.models import Application, CVDocument, User
from app.models.base import as_utc
from app.models.cv import CV_LANGUAGES, CV_TEMPLATES
from app.schemas.cv import CV_SECTIONS, LANGUAGE_NAMES, CVContent, CVEditForm, NewCVForm
from app.schemas.profile import form_errors
from app.security import MAX_ANY_FORM_FIELDS, limiter, read_limited_form
from app.services import (
    application_service,
    cover_letter_service,
    document_service,
    pdf_service,
    profile_service,
    undo_service,
)
from app.services.auth_service import require_user
from app.templating import is_htmx, set_flash, templates

router = APIRouter(dependencies=[Depends(require_user)])
logger = logging.getLogger("unfurl.cvs")

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

# Templates that are built (all three since M3b); the plan decides which a user
# may pick (entitlements, CLAUDE.md).
BUILT_TEMPLATES = CV_TEMPLATES
TEMPLATE_NAMES = {"classic": "Classic", "modern": "Modern", "compact": "Compact"}
TOO_MANY_CVS = (
    f"You can have up to {document_service.MAX_CVS_PER_USER} CVs. Delete one to add another."
)


async def read_form_data(request: Request) -> FormData:
    # The editor's form can pass Starlette's default of 1000 fields (1000 bullet
    # checkboxes + the other fields), so the app-wide limit is given (D-171).
    return await read_limited_form(request, MAX_ANY_FORM_FIELDS)


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def template_choices(user: User) -> list[tuple[str, str]]:
    """(value, label) for the template dropdown: what the plan allows (entitlements,
    CLAUDE.md) and what is built yet."""
    return [
        (name, TEMPLATE_NAMES[name]) for name in allowed_templates(user) if name in BUILT_TEMPLATES
    ]


def new_cv_context(user: User, values=None, errors=None) -> dict:
    return {
        # A new CV starts in the language picked in Settings › Documents (D-129).
        "values": values or {"language": user.default_doc_language, "template": "classic"},
        "errors": errors or {},
        "language_choices": [(code, LANGUAGE_NAMES[code]) for code in CV_LANGUAGES],
        "template_choices": template_choices(user),
    }


# --- Documents page ---------------------------------------------------------------


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request, session: DbSession, user: RequiredUser, tab: str = "cvs"):
    profile = profile_service.get_or_create_profile(session, user)
    cvs = document_service.list_cvs(session, user)
    letters = cover_letter_service.list_letters(session, user)
    context = {
        "tab": "letters" if tab == "letters" else "cvs",
        "masters": [cv for cv in cvs if cv.is_master],
        "copies": [cv for cv in cvs if not cv.is_master],
        "by_id": {cv.id: cv for cv in cvs},  # to name the master a copy is based on
        "copy_groups": copy_groups(session, user, [cv for cv in cvs if not cv.is_master]),
        "language_names": LANGUAGE_NAMES,
        "template_names": TEMPLATE_NAMES,
        "strength": profile_service.profile_strength(session, profile),
        # The Cover letters tab: the same masters and copies as CVs, copies grouped by
        # the application they were made for (D-163).
        "letter_masters": [letter for letter in letters if letter.is_master],
        "letter_copies": [letter for letter in letters if not letter.is_master],
        "letter_groups": copy_groups(
            session, user, [letter for letter in letters if not letter.is_master]
        ),
        "letters_by_id": {letter.id: letter for letter in letters},
    } | new_cv_context(user)
    return templates.TemplateResponse(request, "cvs/documents.html", context)


# --- New CV -----------------------------------------------------------------------


@router.post("/cvs", response_class=HTMLResponse)
def cv_create(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    """The "New CV" form in the modal (htmx). Errors come back as the form (200);
    a new CV opens right away."""
    values = dict(data)
    try:
        form = NewCVForm.model_validate(values)
        if form.template not in dict(template_choices(user)):
            raise ValueError
    except ValidationError as exc:
        return new_cv_form(request, user, values, form_errors(exc))
    except ValueError:
        return new_cv_form(request, user, values, {"template": "Choose a template."})

    try:
        cv = document_service.create_cv(session, user, form.title, form.language, form.template)
    except document_service.TooManyDocuments:
        return new_cv_form(request, user, values, {"form": TOO_MANY_CVS})
    return go_to(request, f"/cvs/{cv.id}")


def new_cv_form(request: Request, user: User, values: dict, errors: dict) -> HTMLResponse:
    context = new_cv_context(user, values, errors)
    return templates.TemplateResponse(request, "cvs/_new_cv_form.html", context)


def go_to(request: Request, url: str) -> Response:
    """Open another page: htmx follows HX-Redirect; a normal form follows the 303."""
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


# --- Editor -----------------------------------------------------------------------

SECTION_NAMES = {
    "summary": "Summary",
    "experience": "Experience",
    "education": "Education",
    "skills": "Skills",
    "languages": "Languages",
    "certifications": "Certifications",
    "projects": "Projects",
}
# A master opens read-only; "Edit master" (after a confirmation) unlocks it with a
# value kept in the session (app/master_unlock.py; D-048, D-169).


def editor_context(
    cv: CVDocument,
    *,
    master_unlocked: bool,
    errors=None,
    master: CVDocument | None = None,
    template_choices: list | None = None,
) -> dict:
    # cv, content, labels and cv_template for the preview (the same as the PDF).
    return pdf_service.cv_context(cv) | {
        "master": master,  # for the band "Copy based on …"
        "errors": errors or {},
        "language_names": LANGUAGE_NAMES,
        "template_names": TEMPLATE_NAMES,
        "section_names": SECTION_NAMES,
        "sections": CV_SECTIONS,
        "master_unlocked": master_unlocked,
        "template_choices": template_choices or [],
        # The form can be changed: never when locked, a master only after unlocking.
        "editable": not cv.is_locked and (not cv.is_master or master_unlocked),
    }


@router.get("/cvs/{cv_id}", response_class=HTMLResponse)
def cv_editor(
    request: Request, session: DbSession, user: RequiredUser, cv_id: str, unlock: str = ""
):
    cv = document_service.get_owned_cv(session, user, cv_id)
    # Only the value the session gave out unlocks a master; an old
    # ?edit_master=confirmed link opens it read-only (D-169).
    unlocked = cv.is_master and master_unlock.is_unlocked(request, "cv", cv.id, unlock)
    master = None
    if cv.parent_id is not None:
        master = document_service.get_owned_cv(session, user, cv.parent_id)
    context = editor_context(
        cv, master_unlocked=unlocked, master=master, template_choices=template_choices(user)
    )
    # The form and "Refresh from profile" send it back with every save (D-169).
    context["unlock_value"] = unlock if unlocked else ""
    # The application this copy was made for (D-084): from the CV itself, never from
    # the address, so a link can't put another job's name on this page.
    application = linked_application(session, user, cv)
    context["application"] = application
    # The version picker: the master and the copies of the same application (or, for
    # copies of no application, every copy under the master).
    versions = document_service.versions_in_lineage(session, user, cv)
    if not cv.is_master:
        versions = [v for v in versions if v.is_master or v.application_id == cv.application_id]
    context["versions"] = versions
    return templates.TemplateResponse(request, "cvs/editor.html", context)


def copy_groups(session: Session, user: User, copies: list) -> list[tuple]:
    """Copies (CVs or cover letters) grouped by the application they were made for
    (D-084, D-163): [(application, [copies])], the latest active applications first,
    then (None, copies of none)."""
    ids = {copy.application_id for copy in copies if copy.application_id is not None}
    applications = (
        {
            app.id: app
            for app in session.exec(
                select(Application).where(
                    Application.user_id == user.id, col(Application.id).in_(ids)
                )
            )
        }
        if ids
        else {}
    )
    linked = sorted(
        applications.values(), key=lambda app: as_utc(app.last_activity_at), reverse=True
    )
    groups = [(app, [c for c in copies if c.application_id == app.id]) for app in linked]
    unlinked = [c for c in copies if c.application_id not in applications]
    return groups + ([(None, unlinked)] if unlinked else [])


def linked_application(session: Session, user: User, cv: CVDocument):
    """The user's application this CV was made for, or None."""
    if cv.application_id is None:
        return None
    return session.exec(
        select(Application).where(
            Application.id == cv.application_id, Application.user_id == user.id
        )
    ).first()


@router.post("/cvs/{cv_id}", response_class=HTMLResponse)
def cv_save(
    request: Request, session: DbSession, user: RequiredUser, cv_id: str, data: SubmittedForm
):
    """Autosave from the editor form (htmx, 500 ms after the last change). Answers with
    the new preview; form errors come back next to the form and nothing is saved."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    unlocked = master_unlock.is_unlocked(request, "cv", cv.id, data.get("unlock"))
    values = {
        "title": data.get("title"),
        "headline": data.get("headline"),
        "summary": data.get("summary"),
        "visible_sections": data.getlist("visible_sections"),
        "bullets": data.getlist("bullets"),  # at most MAX_BULLET_KEYS (checked by the form)
    }
    saved_content = CVContent.model_validate(cv.content)
    errors = {}
    # The template must be one the user's plan allows (entitlements, CLAUDE.md);
    # otherwise the CV keeps its template and the rest is still saved.
    template = data.get("template") or cv.template
    if template not in dict(template_choices(user)):
        errors["template"] = "Choose a template."
        template = cv.template
    try:
        form = CVEditForm.model_validate(values)
    except ValidationError as exc:
        # One bad text field must not block the other changes (e.g. an empty title
        # while a section is switched off): that field keeps its saved value, the
        # rest is saved, and the message says what wasn't.
        errors |= form_errors(exc)
        saved = {
            "title": cv.title,
            "headline": saved_content.personal.headline,
            "summary": saved_content.summary,
        }
        for name in errors:
            if name in saved:
                values[name] = saved[name]
        try:
            form = CVEditForm.model_validate(values)
        except ValidationError:
            # Sections or bullets that aren't on the page: a hand-made request.
            context = editor_context(cv, master_unlocked=unlocked, errors=errors)
            context["nothing_saved"] = True
            return templates.TemplateResponse(request, "cvs/_preview.html", context)

    content = form.apply_to(saved_content)
    try:
        document_service.update_content(
            session,
            cv,
            title=form.title,
            content=content,
            template=template,
            # An unticked checkbox sends nothing, so "not sent" means off.
            shown={box: data.get(box) == "on" for box in document_service.PRIVATE_BOXES},
            user=user,
            master_unlocked=unlocked,
        )
    except (document_service.DocumentLocked, document_service.MasterNotUnlocked):
        # The page shows a locked document or a closed master read-only, so this only
        # happens with an old tab or a hand-made request: nothing is changed.
        raise HTTPException(status_code=409) from None
    context = editor_context(cv, master_unlocked=unlocked, errors=errors)
    return templates.TemplateResponse(request, "cvs/_preview.html", context)


# --- Versions, refresh, delete ----------------------------------------------------
# The buttons send htmx requests. Success opens the next page (HX-Redirect); a
# message that can't be helped (e.g. the 100-CV limit) goes into #cv-action-message.


def action_message(request: Request, text: str) -> HTMLResponse:
    return templates.TemplateResponse(request, "cvs/_action_message.html", {"text": text})


@router.post("/cvs/{cv_id}/versions", response_class=HTMLResponse)
def cv_new_version(request: Request, session: DbSession, user: RequiredUser, cv_id: str):
    """Save as new version (draft) and Duplicate as new version (locked copy)."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    try:
        new = document_service.save_as_new_version(session, user, cv)
    except document_service.TooManyDocuments:
        return action_message(request, TOO_MANY_CVS)
    except document_service.NothingChanged:
        return action_message(
            request,
            "Nothing changed since this version. Make a change first, "
            "then save it as a new version.",
        )
    # A new draft of the CV an application uses becomes the one it uses (D-080); the
    # sent version stays in "what you sent".
    application = linked_application(session, user, cv)
    # Not for a job that was sent: then the sent CV stays its CV, and a new draft can't
    # be locked later as if it had been sent (security review M4c, E1).
    if (
        application is not None
        and application.cv_id == cv.id
        and application.status not in application_service.SENT_STATUSES
    ):
        application.cv_id = new.id
        session.add(application)
        session.commit()
    return go_to(request, f"/cvs/{new.id}")


@router.post("/cvs/{cv_id}/duplicate", response_class=HTMLResponse)
def cv_duplicate(request: Request, session: DbSession, user: RequiredUser, cv_id: str):
    cv = document_service.get_owned_cv(session, user, cv_id)
    try:
        new = document_service.duplicate(session, user, cv)
    except document_service.TooManyDocuments:
        return action_message(request, TOO_MANY_CVS)
    return go_to(request, f"/cvs/{new.id}")


@router.post("/cvs/{cv_id}/refresh", response_class=HTMLResponse)
def cv_refresh(
    request: Request, session: DbSession, user: RequiredUser, cv_id: str, data: SubmittedForm
):
    """Refresh from profile, after the confirmation modal. A master only when it was
    unlocked (D-048); a locked copy never."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    sent = data.get("unlock")
    unlocked = master_unlock.is_unlocked(request, "cv", cv.id, sent)
    try:
        document_service.refresh_from_profile(session, user, cv, master_unlocked=unlocked)
    except (document_service.DocumentLocked, document_service.MasterNotUnlocked):
        raise HTTPException(status_code=409) from None
    suffix = f"?unlock={quote(sent)}" if unlocked else ""
    return go_to(request, f"/cvs/{cv.id}{suffix}")


@router.post("/cvs/{cv_id}/edit-master", response_class=HTMLResponse)
@limiter.limit("30/minute")
def cv_edit_master(request: Request, session: DbSession, user: RequiredUser, cv_id: str):
    """ "Edit master" in the confirmation dialog (D-048, D-169): the editor opens again
    with the value that unlocks this master. Only a master; a copy is edited anyway."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    if not cv.is_master:
        raise HTTPException(status_code=409)
    value = master_unlock.unlock(request, "cv", cv.id)
    return go_to(request, f"/cvs/{cv.id}?unlock={quote(value)}")


@router.delete("/cvs/{cv_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
def cv_delete(request: Request, session: DbSession, user: RequiredUser, cv_id: str):
    """Delete right away, with Undo in the toast (D-086; the confirmation modal of
    D-046 is gone). Locked copies never; a master only when it has no copies (D-047).
    The Documents page opens and shows the toast (a one-time "flash" in the session)."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    try:
        copy_id = document_service.delete_cv(session, cv, user)
    except document_service.DocumentLocked:
        raise HTTPException(status_code=409) from None
    except document_service.MasterHasCopies as exc:
        word = "copy" if exc.copies == 1 else "copies"
        message = f"This master has {exc.copies} {word}. Delete the {word} first."
        toast = {"toast": {"message": message, "undo_url": "", "undo_token": ""}}
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(toast)})
    title = cv.title if len(cv.title) <= 60 else cv.title[:57] + "…"
    set_flash(
        request,
        f'"{title}" deleted.',
        "/undo/delete",
        undo_service.make_delete_token(user, copy_id),
    )
    return go_to(request, "/documents")


# --- PDF (D-057) ------------------------------------------------------------------


@router.get("/cvs/{cv_id}/pdf")
@limiter.limit("20/minute")  # making a PDF takes CPU time
def cv_pdf(request: Request, session: DbSession, user: RequiredUser, cv_id: str):
    """Download the CV as PDF. Locked copies too: that is the CV that was sent."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    try:
        pdf = pdf_service.render_cv_pdf(cv)
    except pdf_service.PDFError as exc:
        # Only the kind of error goes to the log, never the CV's content.
        logger.warning("CV PDF failed (cv %s): %s", cv.id, exc)
        context = {"editor_url": f"/cvs/{cv.id}", "title": cv.title, "what": "CV"}
        return templates.TemplateResponse(request, "cvs/pdf_error.html", context, status_code=503)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": pdf_service.content_disposition(
                cv, company=getattr(linked_application(session, user, cv), "company", None)
            ),
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/cvs/{cv_id}/photo")
def cv_photo(session: DbSession, user: RequiredUser, cv_id: str):
    """The photo inside a CV, for the editor preview (D-052). Only the owner gets it
    (get_owned_cv); the PDF gets it as a data: URI instead."""
    cv = document_service.get_owned_cv(session, user, cv_id)
    personal = CVContent.model_validate(cv.content).personal
    if not personal.photo:
        raise HTTPException(status_code=404)
    try:
        photo = base64.b64decode(personal.photo, validate=True)
    except binascii.Error:  # only a broken database row: no photo, never a 500
        raise HTTPException(status_code=404) from None
    return Response(
        content=photo,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
