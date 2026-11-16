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
from app.schemas.application import ADD_JOB_STATUSES, AddJobForm, BoardQuery, InterviewForm
from app.schemas.profile import form_errors
from app.security import limiter
from app.services import application_service, clock, next_step_service, undo_service
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
# The "All sources" filter offers these ("other" is never set by hand).
FILTER_SOURCES = (
    "linkedin",
    "stepstone",
    "karriere_at",
    "indeed",
    "xing",
    "company_site",
    "manual",
)


UNDO_URL = "/applications/undo"

# A column shows this many cards, then "Show more" (SPEC §6.7).
COLUMN_LIMIT = 20
PAGE_SIZE = 25  # rows per page in the list view (SPEC §6.7)

TOO_MANY_APPLICATIONS = (
    f"You have reached the limit of {application_service.MAX_APPLICATIONS_PER_USER} jobs."
)


def board_context(session: Session, user: User, query: BoardQuery) -> dict:
    """What the page needs for the board or the list. Both views share the counts
    per status (column headings, summary cards above the list)."""
    cards = application_service.board(session, user, query.filters)
    context = {
        "query": query,
        "counts": application_service.counts(session, user),  # summary cards: unfiltered
        "status_titles": STATUS_TITLES,
        "status_hints": STATUS_HINTS,
        "has_applications": application_service.has_any(session, user),
        "source_names": SOURCE_NAMES,
        "remote_names": REMOTE_NAMES,
        "region_url": query.url,  # the region reloads itself with the same choices
        "clear_url": query.without_filters().url,
        "archived_count": application_service.archived_count(session, user),
        "source_choices": [(key, SOURCE_NAMES[key]) for key in FILTER_SOURCES],
        "mode_choices": list(REMOTE_NAMES.items()),
    }
    if query.view == "list":
        return context | list_context(session, user, query)

    columns = []
    for status in BOARD_STATUSES:
        show_all = status in query.more
        columns.append(
            {
                "key": status,
                "title": STATUS_TITLES[status],
                "count": len(cards[status]),  # with the filters: what the column holds
                "cards": cards[status] if show_all else cards[status][:COLUMN_LIMIT],
                # Where "Show more" leads; empty when every card is shown already.
                "more_url": "" if show_all else query.with_more(status).url,
            }
        )
    return context | {"columns": columns}


def list_context(session: Session, user: User, query: BoardQuery) -> dict:
    """One page of the list view. A page number past the end shows the last page."""
    jobs = application_service.listing(session, user, query.sort, query.filters)
    pages = max(1, -(-len(jobs) // PAGE_SIZE))  # rounded up
    page = min(query.page, pages)
    first = (page - 1) * PAGE_SIZE
    rows = jobs[first : first + PAGE_SIZE]
    return {
        "rows": rows,
        "total": len(jobs),
        "first_row": first + 1 if rows else 0,
        "last_row": first + len(rows),
        "page_size": PAGE_SIZE,
        "previous_url": query.with_page(page - 1).url if page > 1 else "",
        "next_url": query.with_page(page + 1).url if page < pages else "",
        # Clicking "Company" sorts A to Z, then Z to A, then back to "last updated".
        "company_sort_url": query.with_sort(
            {"": "company", "company": "-company", "-company": ""}[query.sort]
        ).url,
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
        "applications-changed": True,  # the board and list reload themselves
        "application-changed": True,  # so does an open detail page (D-076)
    } | more
    return {"HX-Trigger": json.dumps(events)}


def undo_events(
    session: Session, user: User, application, events: list, before: dict, toast: str, **more
) -> dict:
    """The HX-Trigger for an undoable change (D-079): the toast with a token that
    holds what changed, from the note taken before the action."""
    after = application_service.note(session, user, application)
    token = undo_service.make_change_token(
        user,
        application.id,
        [event.id for event in events],
        application_service.newest_event_id(session, application),
        application_service.changes(before, after),
    )
    return page_events(toast=toast, undo_url=UNDO_URL, undo_token=token, **more)


@router.get("/", response_class=HTMLResponse)
def applications_home(request: Request, session: DbSession, user: RequiredUser):
    query = BoardQuery.from_params(request.query_params)
    context = board_context(session, user, query)
    if is_htmx(request):
        # The page asks again after a change: only the part that shows the jobs.
        return templates.TemplateResponse(
            request, "applications/_region.html", context | {"oob": True}
        )
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
    created = application_service.events_of(session, application)[-1]
    token = undo_service.make_token(user, application.id, [created.id], before=None)
    response.headers.update(
        page_events(toast=toast, undo_url=UNDO_URL, undo_token=token, **{"close-dialog": "add-job"})
    )
    return response


def add_job_form(request: Request, values: dict | None, errors: dict | None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "applications/_add_job_form.html", add_job_context(values, errors)
    )


@router.patch("/applications/{application_id}/move")
@limiter.limit("120/minute")  # each move renumbers up to two columns
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
    before = application_service.note(session, user, application)
    try:
        event = application_service.move(
            session, user, application, str(data.get("status", "")), data.get("after_id") or None
        )
    except application_service.NotOnBoard:
        raise HTTPException(status_code=422) from None

    if event is None:  # only the order changed: no toast (D-065)
        return Response(status_code=204, headers={"HX-Trigger": "applications-changed"})
    toast = f"{application.company} moved to {STATUS_TITLES[application.status]}."
    more = {"open-interview": application.id} if application.status == "interview" else {}
    return Response(
        status_code=204,
        headers=undo_events(session, user, application, [event], before, toast, **more),
    )


@router.post("/applications/undo")
@limiter.limit("60/minute")
def application_undo(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    """Undo in the toast (D-064). The token says what to take back; if that is no
    longer possible (already undone, too old, changed since), nothing changes."""
    note = undo_service.read_token(user, str(data.get("token", "")))
    try:
        if note is None:
            raise application_service.NothingToUndo
        if note["version"] == 2:
            application_service.undo_change(
                session,
                user,
                note["application_id"],
                note["event_ids"],
                note["newest"],
                note["fields"],
            )
        else:  # "add", and M4a tokens still within their minute
            application_service.undo(
                session, user, note["application_id"], note["event_ids"], note["before"]
            )
    except application_service.NothingToUndo:
        return Response(status_code=204, headers=page_events(toast="Nothing to undo."))
    return Response(status_code=204, headers=page_events(toast="Undone."))


@router.post("/applications/{application_id}/archive")
@limiter.limit("60/minute")
def application_archive(
    request: Request, session: DbSession, user: RequiredUser, application_id: str
):
    """ "Archive" in the list (D-066): off the board, not deleted. Toast with Undo."""
    application = application_service.get_owned_application(session, user, application_id)
    before = application_service.note(session, user, application)
    try:
        event = application_service.archive(session, user, application)
    except application_service.NotOnBoard:  # archived already (another tab)
        return Response(status_code=204, headers={"HX-Trigger": "applications-changed"})
    toast = f"{application.company} archived."
    return Response(
        status_code=204, headers=undo_events(session, user, application, [event], before, toast)
    )


@router.post("/applications/{application_id}/restore")
@limiter.limit("60/minute")
def application_restore(
    request: Request, session: DbSession, user: RequiredUser, application_id: str
):
    """ "Restore" on an archived card or row: back to the top of its column. Toast with Undo."""
    application = application_service.get_owned_application(session, user, application_id)
    before = application_service.note(session, user, application)
    try:
        event = application_service.restore(session, user, application)
    except application_service.NotOnBoard:  # not archived (restored in another tab)
        return Response(status_code=204, headers={"HX-Trigger": "applications-changed"})
    toast = f"{application.company} restored."
    return Response(
        status_code=204, headers=undo_events(session, user, application, [event], before, toast)
    )


# --- Detail page (SPEC §6.7, M4b) --------------------------------------------------------


def timeline_text(event, source_name: str) -> str:
    """One line of the timeline, newest first on the page."""
    if event.type == "created":
        where = "Added by hand" if source_name == "Added by hand" else f"Saved from {source_name}"
        if event.to_status and event.to_status != "saved":
            return f"{where} as {STATUS_TITLES.get(event.to_status, event.to_status)}"
        return where
    if event.type == "status_changed":
        before = STATUS_TITLES.get(event.from_status or "", event.from_status)
        after = STATUS_TITLES.get(event.to_status or "", event.to_status)
        return f"Status: {before} → {after}"
    if event.type == "note_added":
        return f"Note: {event.message or ''}"
    return {"archived": "Archived", "restored": "Restored"}.get(event.type, event.message or "")


def when_text(value) -> str:
    """When an event happened: "Today, 09:12", "Yesterday, 18:40", "Mon, 9 Nov, 10:05"."""
    local = clock.local(value)
    days = (clock.today() - local.date()).days
    day = {0: "Today", 1: "Yesterday"}.get(days, local.strftime("%a, %-d %b"))
    return f"{day}, {local.strftime('%H:%M')}"


def initials(company: str) -> str:
    """ "Kranich Health GmbH" -> "KH": the company avatar."""
    words = [word for word in company.split() if word[:1].isalnum()]
    return "".join(word[0] for word in words[:2]).upper() or "?"


def detail_context(session: Session, user: User, application) -> dict:
    now = clock.now()
    step = next_step_service.next_step(application, now)
    source_name = SOURCE_NAMES.get(application.source, "Other")
    events = application_service.events_of(session, application)
    position, total, before_id, after_id = application_service.neighbours(
        session, user, application
    )
    return {
        "application": application,
        "initials": initials(application.company),
        "status_title": STATUS_TITLES.get(application.status, application.status),
        "source_name": source_name,
        "remote_name": REMOTE_NAMES.get(application.remote_type),
        "saved_when": application.created_at,
        "position": position,
        "total": total,
        "before_url": f"/applications/{before_id}" if before_id else "",
        "after_url": f"/applications/{after_id}" if after_id else "",
        "step": step,
        "step_title": next_step_service.title(step) if step else "No next step",
        "step_reason": (
            next_step_service.reason(step, application)
            if step
            else next_step_service.no_step_reason(application)
        ),
        "timeline": [
            {
                "text": timeline_text(event, source_name),
                "when": when_text(event.created_at),
                "type": event.type,
            }
            for event in reversed(events)
        ],
        "interview_type_names": next_step_service.INTERVIEW_TYPE_NAMES,
        "status_choices": [*STATUS_TITLES.items(), ("archived", "Archived")],
        "closes_text": (
            next_step_service.day_text(clock.to_utc(application.posting_closes_at))
            if application.posting_closes_at
            else ""
        ),
    }


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def application_detail(
    request: Request, session: DbSession, user: RequiredUser, application_id: str
):
    """One application: header, details, saved posting, next step and timeline."""
    application = application_service.get_owned_application(session, user, application_id)
    return templates.TemplateResponse(
        request, "applications/detail.html", detail_context(session, user, application)
    )


PICKER_STATUSES = (*BOARD_STATUSES, "archived")


@router.post("/applications/{application_id}/status")
@limiter.limit("60/minute")
def application_status(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """The status picker on the detail page (D-076): works with the keyboard, unlike
    dragging (D-069). Toast with Undo; the page reloads its parts (application-changed)."""
    application = application_service.get_owned_application(session, user, application_id)
    status = str(data.get("status", ""))
    if status not in PICKER_STATUSES:
        raise HTTPException(status_code=422)
    before = application_service.note(session, user, application)
    events = application_service.set_status(session, user, application, status)
    if not events:  # it already had that status (another tab)
        return Response(status_code=204, headers={"HX-Trigger": "application-changed"})
    if status == "archived":
        toast = f"{application.company} archived."
    else:
        toast = f"{application.company} moved to {STATUS_TITLES[status]}."
    # Interview: the modal asks for the date next; the status is already changed, so
    # "Skip" leaves it as it is (SPEC M4b acceptance, D-076).
    more = {"open-interview": application.id} if status == "interview" else {}
    return Response(
        status_code=204,
        headers=undo_events(session, user, application, events, before, toast, **more),
    )


# --- Interview modal (D-076) -------------------------------------------------------------


def interview_form(request: Request, application, values: dict, errors: dict) -> HTMLResponse:
    context = {
        "application": application,
        "values": values,
        "errors": errors,
        "type_choices": list(next_step_service.INTERVIEW_TYPE_NAMES.items()),
    }
    return templates.TemplateResponse(request, "applications/_interview_form.html", context)


@router.get("/applications/{application_id}/interview", response_class=HTMLResponse)
def interview_form_page(
    request: Request, session: DbSession, user: RequiredUser, application_id: str
):
    """The modal's form, filled with what is known (an earlier interview date)."""
    application = application_service.get_owned_application(session, user, application_id)
    values = {
        "interview_type": application.interview_type or "",
        "interview_with": application.interview_with or "",
    }
    if application.interview_at:
        local = clock.local(application.interview_at)
        values["date"] = local.date().isoformat()
        if not clock.is_start_of_day(application.interview_at):
            values["time"] = local.strftime("%H:%M")
    return interview_form(request, application, values, {})


@router.post("/applications/{application_id}/interview", response_class=HTMLResponse)
@limiter.limit("60/minute")
def interview_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """ "Save interview": errors come back in the form (200); saved, the modal closes
    with a toast and Undo, and the page reloads its parts."""
    application = application_service.get_owned_application(session, user, application_id)
    values = dict(data)
    try:
        form = InterviewForm.model_validate(values)
    except ValidationError as exc:
        return interview_form(request, application, values, form_errors(exc))

    before = application_service.note(session, user, application)
    at = clock.to_utc(form.date, form.time)
    events = application_service.set_interview(
        session,
        user,
        application,
        at=at,
        interview_type=form.interview_type,
        interview_with=form.interview_with,
    )
    toast = f"Interview saved for {next_step_service.when_text(at)}."
    response = interview_form(request, application, {}, {})
    response.headers.update(
        undo_events(
            session, user, application, events, before, toast, **{"close-dialog": "interview"}
        )
    )
    return response
