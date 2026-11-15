"""Documents page and CV documents (SPEC §6.5, M3a). Plan: docs/plans/M3a.md.

The rules for masters, versions and locks are in app/services/document_service.py;
the routes here only read forms, call the service and pick the template.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.billing.entitlements import allowed_templates
from app.db import get_session
from app.models import User
from app.models.cv import CV_LANGUAGES
from app.schemas.cv import LANGUAGE_NAMES, NewCVForm
from app.schemas.profile import form_errors
from app.services import document_service, profile_service
from app.services.auth_service import require_user
from app.templating import is_htmx, templates

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

# Templates that can be shown so far. M3b adds "modern" and "compact" (#28).
BUILT_TEMPLATES = ("classic",)
TEMPLATE_NAMES = {"classic": "Classic", "modern": "Modern", "compact": "Compact"}
TOO_MANY_CVS = (
    f"You can have up to {document_service.MAX_CVS_PER_USER} CVs. Delete one to add another."
)


async def read_form_data(request: Request) -> FormData:
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def template_choices(user: User) -> list[tuple[str, str]]:
    """(value, label) for the template dropdown: what the plan allows (entitlements,
    CLAUDE.md) and what is built yet."""
    return [
        (name, TEMPLATE_NAMES[name]) for name in allowed_templates(user) if name in BUILT_TEMPLATES
    ]


def new_cv_context(user: User, values=None, errors=None) -> dict:
    return {
        "values": values or {"language": "en", "template": "classic"},
        "errors": errors or {},
        "language_choices": [(code, LANGUAGE_NAMES[code]) for code in CV_LANGUAGES],
        "template_choices": template_choices(user),
    }


# --- Documents page ---------------------------------------------------------------


@router.get("/documents", response_class=HTMLResponse)
def documents_page(request: Request, session: DbSession, user: RequiredUser, tab: str = "cvs"):
    profile = profile_service.get_or_create_profile(session, user)
    cvs = document_service.list_cvs(session, user)
    context = {
        "tab": "letters" if tab == "letters" else "cvs",
        "masters": [cv for cv in cvs if cv.is_master],
        "copies": [cv for cv in cvs if not cv.is_master],
        "by_id": {cv.id: cv for cv in cvs},  # to name the master a copy is based on
        "language_names": LANGUAGE_NAMES,
        "template_names": TEMPLATE_NAMES,
        "strength": profile_service.profile_strength(session, profile),
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
    # Step 4 of the plan opens the editor (/cvs/{id}); until then, back to the list.
    del cv
    return go_to(request, "/documents")


def new_cv_form(request: Request, user: User, values: dict, errors: dict) -> HTMLResponse:
    context = new_cv_context(user, values, errors)
    return templates.TemplateResponse(request, "cvs/_new_cv_form.html", context)


def go_to(request: Request, url: str) -> Response:
    """Open another page: htmx follows HX-Redirect; a normal form follows the 303."""
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)
