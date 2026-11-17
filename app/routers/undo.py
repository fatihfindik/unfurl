"""Undo of a delete (D-086): the row comes back from its short-lived copy.

The page reloads with the row back (HX-Refresh) and says "Undone." (flash).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import User
from app.routers.profile import SECTIONS
from app.security import limiter
from app.services import document_service, profile_service, trash_service, undo_service
from app.services.auth_service import require_user
from app.templating import set_flash

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]


async def read_form_data(request: Request) -> FormData:
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]


@router.post("/undo/delete")
@limiter.limit("60/minute")
def undo_delete(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    item_id = undo_service.read_delete_token(user, str(data.get("token", "")))
    item = trash_service.take(session, user, item_id)
    message = "Nothing to undo."
    if item is not None:
        try:
            if item.kind == "cv":
                document_service.restore_cv(session, user, item.payload)
            elif item.kind in SECTIONS:
                profile_service.restore_item(session, user, SECTIONS[item.kind].model, item.payload)
            else:
                raise trash_service.CannotRestore
            session.delete(item)  # used once
            session.commit()
            message = "Undone."
        except (trash_service.CannotRestore, IntegrityError, TypeError, ValueError):
            session.rollback()
    set_flash(request, message)
    return Response(status_code=204, headers={"HX-Refresh": "true"})
