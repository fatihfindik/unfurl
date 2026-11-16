"""Applications home page (`/`): summary cards and the board (SPEC §6.3, §6.7, M4a).

The rules are in app/services/application_service.py; this module only reads the
request, calls the service and picks a template.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import User
from app.models.application import BOARD_STATUSES, MAX_DESCRIPTION
from app.schemas.application import ADD_JOB_STATUSES, AddJobForm, BoardQuery
from app.schemas.profile import form_errors
from app.security import limiter
from app.services import application_service
from app.services.auth_service import require_user
from app.templating import is_htmx, templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]


async def read_form_data(request: Request) -> FormData:
    """The submitted form. An async dependency, so the routes themselves can stay normal
    (sync) functions that FastAPI runs in a worker thread, next to the database calls."""
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]

# Words shown for stored values (docs/prototype/Applications.dc.html).
STATUS_TITLES = {
    "saved": "Saved",
    "applied": "Applied",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
}
STATUS_HINTS = {
    "saved": "Jobs you are considering",
    "applied": "Waiting for a reply",
    "interview": "In the interview process",
    "offer": "Offers on the table",
    "rejected": "Closed, keep the learnings",
}
SOURCE_NAMES = {
    "linkedin": "LinkedIn",
    "stepstone": "StepStone",
    "karriere_at": "karriere.at",
    "indeed": "Indeed",
    "xing": "XING",
    "company_site": "Company site",
    "other": "Other",
    "manual": "Added by hand",
}
REMOTE_NAMES = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite"}  # "unknown": nothing


# A column shows this many cards, then "Show more" (SPEC §6.7).
COLUMN_LIMIT = 20

TOO_MANY_APPLICATIONS = (
    f"You have reached the limit of {application_service.MAX_APPLICATIONS_PER_USER} jobs."
)


def board_context(session: Session, user: User, query: BoardQuery) -> dict:
    """What the page needs: one entry per column, with its title and cards."""
    cards = application_service.board(session, user)
    columns = []
    for status in BOARD_STATUSES:
        show_all = status in query.more
        columns.append(
            {
                "key": status,
                "title": STATUS_TITLES[status],
                "hint": STATUS_HINTS[status],
                "count": len(cards[status]),
                "cards": cards[status] if show_all else cards[status][:COLUMN_LIMIT],
                # Where "Show more" leads; empty when every card is shown already.
                "more_url": "" if show_all else query.with_more(status).url,
            }
        )
    return {
        "columns": columns,
        "has_applications": application_service.has_any(session, user),
        "source_names": SOURCE_NAMES,
        "remote_names": REMOTE_NAMES,
        "region_url": query.url,  # the region reloads itself with the same choices
    }


def add_job_context(values=None, errors=None) -> dict:
    return {
        "values": values or {"status": "saved"},
        "errors": errors or {},
        "remote_choices": list(REMOTE_NAMES.items()),
        "status_choices": [(status, STATUS_TITLES[status]) for status in ADD_JOB_STATUSES],
        "max_description": MAX_DESCRIPTION,
    }


def page_events(*, toast: str, undo_url: str = "", undo_token: str = "", **more) -> dict:
    """The HX-Trigger header that tells the page what just happened (D-065): show a
    toast (with Undo if a token is given) and reload the board with the filters it
    has now. json.dumps writes non-ASCII letters as \\uXXXX, so a company name like
    "Şişecam" is safe in an HTTP header."""
    events = {
        "toast": {"message": toast, "undo_url": undo_url, "undo_token": undo_token},
        "applications-changed": True,
    } | more
    return {"HX-Trigger": json.dumps(events)}


@router.get("/", response_class=HTMLResponse)
def applications_home(request: Request, session: DbSession, user: RequiredUser):
    query = BoardQuery.from_params(request.query_params)
    context = board_context(session, user, query)
    if is_htmx(request):
        # The page asks again after a change: only the part that shows the jobs.
        return templates.TemplateResponse(request, "applications/_region.html", context)
    return templates.TemplateResponse(
        request, "applications/index.html", context | add_job_context()
    )


@router.post("/applications", response_class=HTMLResponse)
@limiter.limit("30/minute")
def application_create(
    request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm
):
    """The "Add job" form in the modal (htmx). Errors come back as the form (200).
    A saved job answers with an empty form, a toast and a reloaded board."""
    values = dict(data)
    try:
        form = AddJobForm.model_validate(values)
    except ValidationError as exc:
        return add_job_form(request, values, form_errors(exc))

    try:
        application = application_service.create(
            session,
            user,
            company=form.company,
            job_title=form.job_title,
            location=form.location,
            remote_type=form.remote_type,
            job_url=form.job_url,
            description_raw=form.description_raw,
            status=form.status,
        )
    except application_service.TooManyApplications:
        return add_job_form(request, values, {"form": TOO_MANY_APPLICATIONS})

    response = add_job_form(request, None, None)
    toast = f"{application.company} saved to {STATUS_TITLES[application.status]}."
    response.headers.update(page_events(toast=toast, **{"close-dialog": "add-job"}))
    return response


def add_job_form(request: Request, values: dict | None, errors: dict | None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "applications/_add_job_form.html", add_job_context(values, errors)
    )


@router.patch("/applications/{application_id}/move")
def application_move(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """A card was dropped on the board (D-063): `status` is the column, `after_id` the
    card above it (empty at the top). The answer has no HTML: the page reloads the
    board itself, so it always shows what is stored."""
    application = application_service.get_owned_application(session, user, application_id)
    try:
        event = application_service.move(
            session, user, application, str(data.get("status", "")), data.get("after_id") or None
        )
    except application_service.NotOnBoard:
        raise HTTPException(status_code=422) from None

    if event is None:  # only the order changed: no toast (D-065)
        return Response(status_code=204, headers={"HX-Trigger": "applications-changed"})
    toast = f"{application.company} moved to {STATUS_TITLES[application.status]}."
    return Response(status_code=204, headers=page_events(toast=toast))
