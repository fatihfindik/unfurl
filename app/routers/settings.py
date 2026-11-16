"""Settings that are saved with a click, without a page (SPEC §6.9).

The Settings page itself comes in M11 (app/routers/placeholders.py until then).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.services.auth_service import require_user
from app.templating import THEMES, set_theme_cookie

router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]


@router.post("/settings/theme")
def save_theme(session: DbSession, user: RequiredUser, theme: Annotated[str, Form()] = ""):
    """The theme picker (D-068): save the choice with the account, so it is the same on
    every device. The cookie is written too, for the pages without a login."""
    if theme not in THEMES:
        raise HTTPException(status_code=422)
    user.theme = theme
    session.add(user)
    session.commit()
    response = Response(status_code=204)
    set_theme_cookie(response, theme)
    return response
