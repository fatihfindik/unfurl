"""Interview prep tab (SPEC §6.7, M14a, D-176…D-180): the requests behind it.

The tab itself is drawn by the detail page (`/applications/{id}?tab=interview`, in
app/routers/applications.py). The paths here are `/applications/{id}/prep/…`:
`/applications/{id}/interview` is already the interview date modal (D-177).
The rules are in app/services/interview_prep_service.py.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import User
from app.security import limiter
from app.services import application_service
from app.services import interview_prep_service as prep
from app.services.auth_service import require_user

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

# The forms here are small; anything bigger is refused before it is read (D-171).
MAX_FIELDS = 10


async def read_form_data(request: Request) -> FormData:
    """The submitted form (async, so the routes can stay sync, like applications.py)."""
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


def check_field_count(data: FormData) -> None:
    if len(data.multi_items()) > MAX_FIELDS:
        raise HTTPException(status_code=400)


@router.post("/applications/{application_id}/prep/start")
@limiter.limit("10/minute")
def start_prep(
    request: Request,
    session: DbSession,
    user: RequiredUser,
    application_id: str,
    data: SubmittedForm,
):
    """ "Start interview prep" (with the starter set) or "Start with an empty list"
    (D-178). Nothing is typed yet, so the page may reload itself (application-changed);
    the address keeps the tab. A second start (another tab) changes nothing."""
    check_field_count(data)
    application = application_service.get_owned_application(session, user, application_id)
    try:
        prep.start(session, user, application, with_starters=data.get("starters") == "1")
    except prep.Archived:
        raise HTTPException(status_code=409) from None
    return Response(status_code=204, headers={"HX-Trigger": "application-changed"})
