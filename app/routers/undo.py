"""Undo of a delete (D-086): the row comes back from its short-lived copy.

The page reloads with the row back (HX-Refresh) and says "Undone." (flash).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session
from starlette.datastructures import FormData

from app.db import get_session
from app.models import User
from app.routers.profile import SECTIONS
from app.security import limiter
from app.services import (
    document_service,
    learning_service,
    profile_service,
    trash_service,
    undo_service,
)
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
    # Purge first, in its own commit: a failed restore rolls back, the purge must stay.
    trash_service.purge_old(session)
    session.commit()
    item = trash_service.take(session, user, item_id)
    message = "Nothing to undo."
    if item is not None:
        payload, kind = item.payload, item.kind
        try:
            # Used once: the copy is deleted first, in the same commit as the restore.
            if not trash_service.claim(session, item):
                raise trash_service.CannotRestore
            if kind == "cv":
                document_service.restore_cv(session, user, payload)
            elif kind in SECTIONS:
                profile_service.restore_item(session, user, SECTIONS[kind].model, payload)
            elif kind == "learning_task":  # D-095
                learning_service.restore_task(session, user, payload)
            elif kind == "learning_goal":
                learning_service.restore_goal(session, user, payload)
            else:
                raise trash_service.CannotRestore
            session.commit()
            message = "Undone."
        # A broken copy is "Nothing to undo.", never an error page. SQLAlchemyError
        # covers IntegrityError (a place taken) and wrong values for a column.
        except (
            trash_service.CannotRestore,
            SQLAlchemyError,
            AttributeError,
            KeyError,
            OverflowError,
            TypeError,
            ValueError,
        ):
            session.rollback()
    set_flash(request, message)
    return Response(status_code=204, headers={"HX-Refresh": "true"})
