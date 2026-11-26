"""Interview prep tab (SPEC §6.7, M14a, D-176…D-180): the requests behind it.

The tab itself is drawn by the detail page (`/applications/{id}?tab=interview`, in
app/routers/applications.py). The paths here are `/applications/{id}/prep/…`:
`/applications/{id}/interview` is already the interview date modal (D-177).
The rules are in app/services/interview_prep_service.py.

No request here sends "application-changed" (D-179) except Start: that event reloads
the whole page, and text typed in the last half second would be lost. Each answer
swaps only its own part of the tab instead.
"""

import json
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import Application, User
from app.models.interview import MAX_QUESTIONS
from app.security import UserFacingHTTPException, limiter, read_limited_form
from app.services import application_service, undo_service
from app.services import interview_prep_service as prep
from app.services.auth_service import require_user
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

# The forms here are small; anything bigger is refused (D-171).
MAX_FIELDS = 10
DELETE_UNDO_URL = "/undo/delete"  # app/routers/undo.py, shared with CVs and learning

CARDS = {"likely": "applications/_prep_likely.html", "to_ask": "applications/_prep_to_ask.html"}


async def read_form_data(request: Request) -> FormData:
    """The submitted form, at most MAX_FIELDS fields (400 otherwise, D-171). Async, so
    the routes can stay sync, like applications.py."""
    return await read_limited_form(request, MAX_FIELDS)


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


@contextmanager
def service_errors():
    """The service's refusals as HTTP answers; htmx shows the text as a toast (D-125)."""
    try:
        yield
    except prep.Archived:
        raise UserFacingHTTPException(
            status_code=409, detail="This job is archived. Restore it to edit your prep."
        ) from None
    except prep.TooManyQuestions:
        raise UserFacingHTTPException(
            status_code=409, detail=f"An application can have up to {MAX_QUESTIONS} questions."
        ) from None
    except prep.InvalidInput:
        raise UserFacingHTTPException(
            status_code=422, detail="This can't be saved. Check the text and try again."
        ) from None


def render(request: Request, template: str, context: dict, headers=None) -> HTMLResponse:
    return templates.TemplateResponse(request, template, context, headers=headers)


def card(request, session, user, application: Application, kind: str, headers=None):
    """One card of the tab (likely questions or questions to ask), drawn again."""
    context = {"application": application} | prep.tab_context(session, user, application)
    return render(request, CARDS[kind], context, headers)


def owned(session, user, application_id: str, question_id: str | None = None, *, lock=False):
    """The user's application (404 otherwise) and, if asked for, its question. With
    lock, the application row stays locked until the commit (Postgres), so two adds at
    the same moment can't both pass the 100-question check (security review M14a, W2)."""
    application = application_service.get_owned_application(
        session, user, application_id, lock=lock
    )
    if question_id is None:
        return application, None
    return application, prep.get_owned_question(session, user, application, question_id)


# --- Start (D-178) ------------------------------------------------------------------


@router.post("/applications/{application_id}/prep/start")
@limiter.limit("10/minute")
def start_prep(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """ "Start interview prep" (with the starter set) or "Start with an empty list".
    Nothing is typed yet, so the page may reload itself (application-changed); the
    address keeps the tab. A second start (another tab) changes nothing."""
    application, _ = owned(session, user, application_id)
    with service_errors():
        prep.start(session, user, application, with_starters=data.get("starters") == "1")
    return Response(status_code=204, headers={"HX-Trigger": "application-changed"})


# --- Questions ----------------------------------------------------------------------


@router.post("/applications/{application_id}/prep/questions", response_class=HTMLResponse)
@limiter.limit("30/minute")
def add_question(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """ "Add a question" under either card. The answer is that card, drawn again."""
    application, _ = owned(session, user, application_id, lock=True)
    kind = data.get("kind")
    with service_errors():
        prep.add(session, user, application, kind, data.get("category"), data.get("text"))
    return card(request, session, user, application, kind)


@router.get(
    "/applications/{application_id}/prep/questions/{question_id}", response_class=HTMLResponse
)
@limiter.limit("60/minute")
def question_row(
    request: Request, session: DbSession, user: RequiredUser, application_id: str, question_id: str
):
    """One question as it is shown (the edit form's "Cancel")."""
    application, question = owned(session, user, application_id, question_id)
    context = {"application": application, "question": question} | prep.tab_context(
        session, user, application
    )
    return render(request, "applications/_prep_question.html", context)


@router.get(
    "/applications/{application_id}/prep/questions/{question_id}/edit",
    response_class=HTMLResponse,
)
@limiter.limit("60/minute")
def question_edit_form(
    request: Request, session: DbSession, user: RequiredUser, application_id: str, question_id: str
):
    """The question's text (and category) as a small form, in place of the row."""
    application, question = owned(session, user, application_id, question_id)
    if application.archived:
        raise HTTPException(status_code=409)
    context = {"application": application, "question": question} | prep.tab_context(
        session, user, application
    )
    return render(request, "applications/_prep_question_edit.html", context)


@router.post(
    "/applications/{application_id}/prep/questions/{question_id}", response_class=HTMLResponse
)
@limiter.limit("60/minute")
def question_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    question_id: str,
    data: SubmittedForm,
):
    """Save the edit form. The card is drawn again: a new category moves the question."""
    application, question = owned(session, user, application_id, question_id)
    with service_errors():
        prep.update_question(session, application, question, data.get("text"), data.get("category"))
    return card(request, session, user, application, question.kind)


@router.post(
    "/applications/{application_id}/prep/questions/{question_id}/answer",
    response_class=HTMLResponse,
)
@limiter.limit("120/minute")
def answer_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    question_id: str,
    data: SubmittedForm,
):
    """The answer draft (or their answer), saved while typing (D-179). The answer is
    only "Saved" and the "{n} of {m} answered" line, never the textarea itself."""
    application, question = owned(session, user, application_id, question_id)
    with service_errors():
        prep.save_answer(session, application, question, data.get("answer"))
    context = {"application": application} | prep.tab_context(session, user, application)
    context["count_oob"] = question.kind == "likely"
    return render(request, "applications/_prep_saved.html", context)


@router.post("/applications/{application_id}/prep/questions/{question_id}/asked")
@limiter.limit("60/minute")
def asked_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    question_id: str,
    data: SubmittedForm,
):
    """The "asked" tick of a question to ask. The form sends a hidden "0" and, when
    ticked, the checkbox's "1"."""
    application, question = owned(session, user, application_id, question_id)
    with service_errors():
        prep.set_asked(session, application, question, "1" in data.getlist("asked"))
    return Response(status_code=204)


@router.post(
    "/applications/{application_id}/prep/questions/{question_id}/delete",
    response_class=HTMLResponse,
)
@limiter.limit("30/minute")
def question_delete(
    request: Request, session: DbSession, user: RequiredUser, application_id: str, question_id: str
):
    """Delete a question: the card without it, and a toast with Undo (D-086, D-179)."""
    application, question = owned(session, user, application_id, question_id)
    kind = question.kind
    with service_errors():
        copy_id = prep.delete(session, user, application, question)
    token = undo_service.make_delete_token(user, copy_id)
    toast = {"message": "Question deleted.", "undo_url": DELETE_UNDO_URL, "undo_token": token}
    headers = {"HX-Trigger": json.dumps({"toast": toast})}
    return card(request, session, user, application, kind, headers)


# --- Notes --------------------------------------------------------------------------


@router.post("/applications/{application_id}/prep/notes", response_class=HTMLResponse)
@limiter.limit("120/minute")
def notes_save(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """The company and role notes, saved while typing (D-179)."""
    application, _ = owned(session, user, application_id)
    with service_errors():
        prep.save_notes(session, application, data.get("interview_notes"))
    return render(request, "applications/_prep_saved.html", {"count_oob": False})
