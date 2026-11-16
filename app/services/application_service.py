"""Rules for applications (SPEC §5.5, §5.6, §6.7; D-061…D-066).

Routes only call this module. Every query filters by user_id (CLAUDE.md).
"""

import datetime as dt
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from app.models import Application, ApplicationEvent, User
from app.models.application import BOARD_STATUSES
from app.models.base import utcnow
from app.services import profile_service

MAX_APPLICATIONS_PER_USER = 2000  # archived ones count too (D-061)
MAX_ID = profile_service.MAX_ID

# Job sites we know, by a name in the host (D-062). "stepstone" and "indeed" use many
# country domains (stepstone.at, at.indeed.com), so any label of the host may match.
_SOURCE_BY_DOMAIN = {"linkedin.com": "linkedin", "karriere.at": "karriere_at", "xing.com": "xing"}
_SOURCE_BY_LABEL = {"stepstone": "stepstone", "indeed": "indeed"}


class TooManyApplications(Exception):
    """The user already has MAX_APPLICATIONS_PER_USER applications."""


def today() -> dt.date:
    """Today's date in UTC (users get their own time zone in M10/M11, D-061)."""
    return utcnow().date()


def detect_source(job_url: str | None) -> str:
    """Where a job was found, from its link: "linkedin", "stepstone", …; any other
    link is "company_site", no link is "manual".

    The host is compared exactly or as a parent domain, never as a piece of text:
    "linkedin.com.evil.example" is not LinkedIn.
    """
    if not job_url:
        return "manual"
    host = (urlsplit(job_url).hostname or "").lower()
    for domain, source in _SOURCE_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return source
    labels = host.split(".")
    for label, source in _SOURCE_BY_LABEL.items():
        # Not the last label: "jobs.indeed" would be a made-up top-level domain.
        if label in labels[:-1]:
            return source
    return "company_site"


def get_owned_application(session: Session, user: User, application_id) -> Application:
    """The application `application_id` if it belongs to `user`, otherwise 404.

    Another user's application and one that doesn't exist give the same answer
    (SPEC §10.1). The id comes from the URL as text; anything that isn't a valid
    id is a 404 too.
    """
    try:
        number = int(application_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)
    application = session.exec(
        select(Application).where(Application.id == number, Application.user_id == user.id)
    ).first()
    if application is None:
        raise HTTPException(status_code=404)
    return application


def column_of(session: Session, user: User, status: str) -> list[Application]:
    """The cards of one board column, top to bottom. Archived cards are not in it."""
    return list(
        session.exec(
            select(Application)
            .where(
                Application.user_id == user.id,
                Application.status == status,
                col(Application.archived).is_(False),
            )
            .order_by(col(Application.position), col(Application.id))
        )
    )


def board(session: Session, user: User) -> dict[str, list[Application]]:
    """Every column of the board with its cards: {"saved": [...], "applied": [...], …}.
    One query for all columns; archived cards are left out."""
    cards = session.exec(
        select(Application)
        .where(
            Application.user_id == user.id,
            col(Application.status).in_(BOARD_STATUSES),
            col(Application.archived).is_(False),
        )
        .order_by(col(Application.position), col(Application.id))
    )
    columns: dict[str, list[Application]] = {status: [] for status in BOARD_STATUSES}
    for card in cards:
        columns[card.status].append(card)
    return columns


def has_any(session: Session, user: User) -> bool:
    """True if the user has saved at least one job (archived ones count)."""
    first = session.exec(select(Application.id).where(Application.user_id == user.id)).first()
    return first is not None


def _renumber(cards: list[Application]) -> None:
    """Give the cards the positions 0, 1, 2, … in the order of the list."""
    for position, card in enumerate(cards):
        if card.position != position:
            card.position = position


def _add_event(session: Session, application: Application, type: str, **fields) -> ApplicationEvent:
    event = ApplicationEvent(application_id=application.id, type=type, **fields)
    session.add(event)
    return event


def create(
    session: Session,
    user: User,
    *,
    company: str,
    job_title: str,
    location: str | None = None,
    remote_type: str = "unknown",
    job_url: str | None = None,
    description_raw: str = "",
    status: str = "saved",
) -> Application:
    """Save a new job at the top of its column, with a "created" event.
    The values were already checked by the form (app.schemas.application)."""
    count = session.exec(
        select(func.count()).select_from(Application).where(Application.user_id == user.id)
    ).one()
    if count >= MAX_APPLICATIONS_PER_USER:
        raise TooManyApplications

    application = Application(
        user_id=user.id,
        company=company,
        job_title=job_title,
        location=location,
        remote_type=remote_type,
        job_url=job_url,
        source=detect_source(job_url),
        description_raw=description_raw,
        status=status,
        # SPEC §5.6: an application that is "applied" has the date it was sent.
        applied_at=today() if status == "applied" else None,
    )
    _renumber([application, *column_of(session, user, status)])
    session.add(application)
    session.flush()  # gives the application its id, for the event
    _add_event(session, application, "created", to_status=status)
    session.commit()
    session.refresh(application)
    return application


def events_of(session: Session, application: Application) -> list[ApplicationEvent]:
    """The timeline of an application the caller already owns, oldest first."""
    return list(
        session.exec(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(col(ApplicationEvent.id))
        )
    )
