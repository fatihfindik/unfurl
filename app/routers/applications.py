"""Applications home page (`/`): summary cards and the board (SPEC §6.3, §6.7, M4a).

The rules are in app/services/application_service.py; this module only reads the
request, calls the service and picks a template.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.models.application import BOARD_STATUSES
from app.services import application_service
from app.services.auth_service import require_user
from app.templating import templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

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


def board_context(session: Session, user: User) -> dict:
    """What the page needs: one entry per column, with its title, hint and cards."""
    cards = application_service.board(session, user)
    columns = [
        {
            "key": status,
            "title": STATUS_TITLES[status],
            "hint": STATUS_HINTS[status],
            "cards": cards[status],
        }
        for status in BOARD_STATUSES
    ]
    return {
        "columns": columns,
        "has_applications": application_service.has_any(session, user),
        "source_names": SOURCE_NAMES,
        "remote_names": REMOTE_NAMES,
    }


@router.get("/", response_class=HTMLResponse)
def applications_home(request: Request, session: DbSession, user: RequiredUser):
    return templates.TemplateResponse(
        request, "applications/index.html", board_context(session, user)
    )
