"""Rules for applications (SPEC §5.5, §5.6, §6.7; D-061…D-066).

Routes only call this module. Every query filters by user_id (CLAUDE.md).
"""

import datetime as dt
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlmodel import Session, col, delete, func, select

from app.models import Application, ApplicationEvent, User
from app.models.application import BOARD_STATUSES
from app.models.base import as_utc, utcnow
from app.services import clock, profile_service

MAX_APPLICATIONS_PER_USER = 2000  # archived ones count too (D-061)
MAX_ID = profile_service.MAX_ID

# Job sites we know, by a name in the host (D-062). "stepstone" and "indeed" use many
# country domains (stepstone.at, at.indeed.com), so any label of the host may match.
_SOURCE_BY_DOMAIN = {"linkedin.com": "linkedin", "karriere.at": "karriere_at", "xing.com": "xing"}
_SOURCE_BY_LABEL = {"stepstone": "stepstone", "indeed": "indeed"}


class TooManyApplications(Exception):
    """The user already has MAX_APPLICATIONS_PER_USER applications."""


class NotOnBoard(Exception):
    """The status is not a column of the board, or the card is archived."""


class NothingToUndo(Exception):
    """The action was already undone, or something changed the application since."""


def today() -> dt.date:
    """Today's date where the user is (D-071; was UTC in M4a)."""
    return clock.today()


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


@dataclass(frozen=True)
class Filters:
    """What the user narrowed the jobs down to (SPEC §6.7). Empty means "all".
    The router only passes values it checked against the known lists."""

    q: str = ""  # search text: company, job title or city
    source: str = ""
    mode: str = ""  # remote_type
    status: str = ""  # list view only (the summary cards, D-070)
    archived: bool = False  # "Show archived": archived jobs are shown too, faded


NO_FILTERS = Filters()


def _jobs(session: Session, user: User, filters: Filters) -> list[Application]:
    """The user's jobs that match `filters`; archived ones only with "Show archived".

    Source, work mode and status are filtered in SQL. The search text is compared in
    Python with search_key (D-067): SQLite's lower() only knows A-Z, and "istanbul"
    must find "İstanbul" in both databases. A user has at most 2,000 jobs."""
    query = select(Application).where(
        Application.user_id == user.id,
        col(Application.status).in_(BOARD_STATUSES),
    )
    if not filters.archived:
        query = query.where(col(Application.archived).is_(False))
    if filters.source:
        query = query.where(Application.source == filters.source)
    if filters.mode:
        query = query.where(Application.remote_type == filters.mode)
    if filters.status:
        query = query.where(Application.status == filters.status)
    # Archived cards come last in their column (D-063): they have no place in the order.
    order = (col(Application.archived), col(Application.position), col(Application.id))
    jobs = list(session.exec(query.order_by(*order)))
    words = search_key(filters.q).split()
    if words:
        jobs = [job for job in jobs if _matches(job, words)]
    return jobs


def _matches(job: Application, words: list[str]) -> bool:
    """True if every word of the search is in the company, the job title or the place.
    ("acme vienna" finds the Acme job in Vienna.)"""
    text = " ".join(search_key(value) for value in (job.company, job.job_title, job.location))
    return all(word in text for word in words)


def board(
    session: Session, user: User, filters: Filters = NO_FILTERS
) -> dict[str, list[Application]]:
    """Every column of the board with its cards: {"saved": [...], "applied": [...], …},
    top to bottom; archived cards (with "Show archived") at the bottom."""
    columns: dict[str, list[Application]] = {status: [] for status in BOARD_STATUSES}
    for card in _jobs(session, user, filters):
        columns[card.status].append(card)
    return columns


def counts(session: Session, user: User) -> dict[str, int]:
    """How many jobs each status has, archived ones not counted (the summary cards:
    they always show the whole picture, whatever the filters)."""
    rows = session.exec(
        select(Application.status, func.count())
        .where(
            Application.user_id == user.id,
            col(Application.status).in_(BOARD_STATUSES),
            col(Application.archived).is_(False),
        )
        .group_by(Application.status)
    )
    found = dict(rows.all())
    return {status: found.get(status, 0) for status in BOARD_STATUSES}


# Letters that NFKD can't turn into a plain letter (the same list as the PDF file
# names, D-057): "ı" has no "i + accent" form.
_PLAIN_LETTERS = str.maketrans(
    {"ı": "i", "İ": "I", "ß": "ss", "ğ": "g", "Ğ": "G", "ø": "o", "Ø": "O"}
)


def search_key(text: str | None) -> str:
    """Text in the form used to compare and sort it (D-067): no accents, no capitals.
    "İstanbul", "istanbul" and "ISTANBUL" all become "istanbul"; "Österreich" becomes
    "osterreich". casefold() alone is not enough: "İ".casefold() is "i" plus a dot."""
    text = unicodedata.normalize("NFKD", (text or "").translate(_PLAIN_LETTERS))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def listing(
    session: Session, user: User, sort: str = "", filters: Filters = NO_FILTERS
) -> list[Application]:
    """The jobs for the list view. `sort` is "" (last updated first), "company"
    (A to Z) or "-company" (Z to A)."""
    jobs = _jobs(session, user, filters)
    # Sorted in Python: SQLite's lower() only knows A-Z, and both databases must agree.
    if sort in ("company", "-company"):
        jobs.sort(key=lambda job: (search_key(job.company), job.id), reverse=sort == "-company")
    else:
        jobs.sort(key=lambda job: (as_utc(job.last_activity_at), job.id), reverse=True)
    return jobs


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


def move(
    session: Session, user: User, application: Application, status: str, after_id=None
) -> ApplicationEvent | None:
    """Put a card into the column `status`, right below the card `after_id` (or at the
    top when after_id is empty). Dragging a card on the board ends here (D-063).

    A new status writes a "status_changed" event, which is returned; moving a card
    inside its column only changes the order and returns None. The browser names
    the card above instead of a number, because with filters or "Show more" the
    cards on screen are not the whole column.
    """
    if status not in BOARD_STATUSES or application.archived:
        raise NotOnBoard
    above = get_owned_application(session, user, after_id) if after_id else None
    if above is not None and above.id == application.id:
        return None  # dropped where it was

    old_status = application.status
    cards = [card for card in column_of(session, user, status) if card.id != application.id]
    # If the card above is no longer in this column (an old screen), go to the top.
    index = cards.index(above) + 1 if above in cards else 0

    event = None
    if status != old_status:
        application.last_activity_at = utcnow()
        if status == "applied" and application.applied_at is None:
            application.applied_at = today()  # SPEC §5.6; a date already there stays
        event = _add_event(
            session, application, "status_changed", from_status=old_status, to_status=status
        )
        clear_next_step(application)  # a new status starts with its automatic next step
    _place(session, user, application, status, index)
    session.add(application)
    session.commit()
    session.refresh(application)
    return event


def _place(session: Session, user: User, application: Application, status: str, index) -> None:
    """Put the card at place `index` of column `status` (None: off the board, archived)
    and close the gap in the column it leaves. Both columns are numbered again, so
    positions never repeat (D-063)."""
    old_status = application.status
    application.status = status
    # Cards are filtered out by id: whether a query already sees the card's new
    # values depends on autoflush.
    if old_status != status or index is None:
        _renumber([c for c in column_of(session, user, old_status) if c.id != application.id])
    if index is not None:
        cards = [c for c in column_of(session, user, status) if c.id != application.id]
        cards.insert(min(index, len(cards)), application)
        _renumber(cards)


# --- Undo, version 2 (D-079) ------------------------------------------------------------
# Before an undoable action the router takes a note() of the application, and another
# one after it. changes() keeps the fields that differ, as [before, after]. Undo writes
# the "before" values back, but only while those fields still have the "after" values
# and no newer event exists: a newer change is never overwritten, and a token can't be
# used twice (the second time the fields are no longer at "after").

NOTE_FIELDS = (
    "status",
    "archived",
    "applied_at",
    "last_activity_at",
    "next_action_type",
    "next_action_at",
    "next_action_note",
    "next_action_manual",
    "interview_at",
    "interview_type",
    "interview_with",
    "snoozed_until",
)
_DATETIME_FIELDS = ("last_activity_at", "next_action_at", "interview_at", "snoozed_until")
_BOOL_FIELDS = ("archived", "next_action_manual")
_TEXT_FIELDS = (
    "status",
    "next_action_type",
    "next_action_note",
    "interview_type",
    "interview_with",
)


def _to_text(value):
    """A field value as JSON-friendly text for the token (UTC for times)."""
    if isinstance(value, dt.datetime):
        return as_utc(value).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _from_text(field: str, value):
    """The other way round. Raises ValueError for a value of the wrong kind."""
    if value is None:
        if field in _BOOL_FIELDS or field in ("status", "last_activity_at"):
            raise ValueError(field)
        return None
    if field in _DATETIME_FIELDS:
        return dt.datetime.fromisoformat(value) if isinstance(value, str) else _bad(field)
    if field == "applied_at":
        return dt.date.fromisoformat(value) if isinstance(value, str) else _bad(field)
    if field in _BOOL_FIELDS:
        return value if isinstance(value, bool) else _bad(field)
    if field in _TEXT_FIELDS:
        return value if isinstance(value, str) and len(value) <= 200 else _bad(field)
    return _bad(field)


def _bad(field: str):
    raise ValueError(field)


def note(session: Session, user: User, application: Application) -> dict:
    """The values an undoable action may change, plus "index": the card's place in
    its column (None while archived)."""
    values = {field: _to_text(getattr(application, field)) for field in NOTE_FIELDS}
    index = None
    if not application.archived:
        column = [card.id for card in column_of(session, user, application.status)]
        index = column.index(application.id) if application.id in column else 0
    return values | {"index": index}


def changes(before: dict, after: dict) -> dict:
    """{field: [before, after]} for the fields an action changed. The place in the
    column ("index") only matters when the card changed column or was archived."""
    changed = {f: [before[f], after[f]] for f in NOTE_FIELDS if before[f] != after[f]}
    if "status" in changed or "archived" in changed:
        changed["index"] = [before["index"], None]
    return changed


def newest_event_id(session: Session, application: Application) -> int | None:
    return session.exec(
        select(func.max(ApplicationEvent.id)).where(
            ApplicationEvent.application_id == application.id
        )
    ).one()


def undo_change(
    session: Session, user: User, application_id, event_ids: list[int], newest, fields: dict
) -> Application:
    """Take back one action (D-079): delete its events and write back the "before"
    values of `fields`. Raises NothingToUndo if the application changed since."""
    try:
        application = get_owned_application(session, user, application_id)
    except HTTPException:
        raise NothingToUndo from None
    if not fields or newest_event_id(session, application) != newest:
        raise NothingToUndo
    current = note(session, user, application)
    if any(current[f] != values[1] for f, values in fields.items() if f != "index"):
        raise NothingToUndo  # something changed these fields since: keep it
    try:
        restored = {f: _from_text(f, values[0]) for f, values in fields.items() if f != "index"}
        index = fields.get("index", [None])[0]
        if index is not None and (not isinstance(index, int) or isinstance(index, bool)):
            raise ValueError("index")
        if restored.get("status", application.status) not in BOARD_STATUSES:
            raise ValueError("status")
    except (TypeError, ValueError, IndexError):
        raise NothingToUndo from None  # never with our own tokens

    if event_ids:
        deleted = session.exec(
            delete(ApplicationEvent).where(
                col(ApplicationEvent.id).in_(event_ids),
                ApplicationEvent.application_id == application.id,
            )
        )
        if deleted.rowcount != len(set(event_ids)):
            session.rollback()  # another Undo was faster
            raise NothingToUndo

    status = restored.pop("status", application.status)
    for field, value in restored.items():
        setattr(application, field, value)
    if "index" in fields:
        _place(session, user, application, status, None if application.archived else index or 0)
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


# --- Undo (D-064, M4a tokens; still used for "add") -------------------------------------
# An undoable action returns its events and a "before" note: the values it changed.
# The note travels inside a signed token (app/services/undo_service.py) and comes
# back when the user clicks Undo. Only these fields are ever written back.

UNDO_FIELDS = ("status", "index", "applied_at", "archived", "last_activity_at")


def before_note(session: Session, user: User, application: Application) -> dict:
    """The values an action may change, as plain text for the undo token."""
    index = None
    if not application.archived:
        column = [card.id for card in column_of(session, user, application.status)]
        index = column.index(application.id) if application.id in column else 0
    return {
        "status": application.status,
        "index": index,
        "applied_at": application.applied_at.isoformat() if application.applied_at else None,
        "archived": application.archived,
        "last_activity_at": as_utc(application.last_activity_at).isoformat(),
    }


def undo(
    session: Session, user: User, application_id, event_ids: list[int], before: dict | None
) -> Application | None:
    """Take back the action that wrote `event_ids`. `before` is the note from before_note,
    or None when the action created the application (then it is deleted again).
    Returns the application, or None if it was deleted.

    Undo works only while the action's last event is still the newest one of the
    application: anything done afterwards would otherwise be overwritten. The events
    are deleted first, and only if all of them are still there: of two Undo clicks
    at the same moment, one finds them gone and changes nothing.
    """
    try:
        application = get_owned_application(session, user, application_id)
    except HTTPException:
        raise NothingToUndo from None  # already deleted by an earlier Undo
    newest = session.exec(
        select(func.max(ApplicationEvent.id)).where(
            ApplicationEvent.application_id == application.id
        )
    ).one()
    if not event_ids or newest != max(event_ids):
        raise NothingToUndo
    if before is not None and before.get("status") not in BOARD_STATUSES:
        raise NothingToUndo  # never happens with our own tokens; a guard all the same

    deleted = session.exec(
        delete(ApplicationEvent).where(
            col(ApplicationEvent.id).in_(event_ids),
            ApplicationEvent.application_id == application.id,
        )
    )
    if deleted.rowcount != len(set(event_ids)):
        session.rollback()
        raise NothingToUndo

    if before is None:
        session.delete(application)  # undo "add": its events go with it (cascade)
        _renumber(
            [c for c in column_of(session, user, application.status) if c.id != application.id]
        )
        session.commit()
        return None

    try:
        applied_at = before.get("applied_at")
        applied_at = dt.date.fromisoformat(applied_at) if applied_at else None
        last_activity_at = dt.datetime.fromisoformat(before["last_activity_at"])
        archived = bool(before["archived"])
        index = None if archived else int(before.get("index") or 0)
    except (KeyError, TypeError, ValueError):
        # Never with our own tokens; a broken note is "nothing to undo", not a 500
        # (test-writer M4a). The deleted events come back with the rollback.
        session.rollback()
        raise NothingToUndo from None
    application.applied_at = applied_at
    application.last_activity_at = last_activity_at
    application.archived = archived
    _place(session, user, application, before["status"], index)
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


def clear_next_step(application: Application) -> None:
    """Forget a next step set by hand and a snooze (D-073, D-074): the rules for the
    new situation apply again."""
    application.next_action_type = None
    application.next_action_at = None
    application.next_action_note = None
    application.next_action_manual = False
    application.snoozed_until = None


def set_status(
    session: Session, user: User, application: Application, status: str
) -> list[ApplicationEvent]:
    """The status picker on the detail page (D-076): a board status, or "archived".
    From the archive the job is restored first. Returns the events written."""
    if status != "archived" and status not in BOARD_STATUSES:
        raise NotOnBoard
    if status == "archived":
        return [] if application.archived else [archive(session, user, application)]
    events = []
    if application.archived:
        events.append(restore(session, user, application))
    if status != application.status:
        events.append(move(session, user, application, status))
    return events


def archive(session: Session, user: User, application: Application) -> ApplicationEvent:
    """Take a job off the board without deleting it (SPEC §5.5, D-066). It keeps its
    status; "Show archived" shows it, Restore brings it back."""
    if application.archived:
        raise NotOnBoard
    application.archived = True
    application.last_activity_at = utcnow()
    event = _add_event(session, application, "archived")
    _place(session, user, application, application.status, None)  # its column closes the gap
    session.add(application)
    session.commit()
    session.refresh(application)
    return event


def restore(session: Session, user: User, application: Application) -> ApplicationEvent:
    """Put an archived job back on the board, at the top of its column (D-066)."""
    if not application.archived:
        raise NotOnBoard
    application.archived = False
    application.last_activity_at = utcnow()
    event = _add_event(session, application, "restored")
    _place(session, user, application, application.status, 0)
    session.add(application)
    session.commit()
    session.refresh(application)
    return event


def archived_count(session: Session, user: User) -> int:
    """How many jobs are archived ("Show archived (n)")."""
    return session.exec(
        select(func.count())
        .select_from(Application)
        .where(Application.user_id == user.id, col(Application.archived).is_(True))
    ).one()


def neighbours(
    session: Session, user: User, application: Application
) -> tuple[int, int, int | None, int | None]:
    """Where the application sits among its column for "‹ 1 of 10 in Saved ›" (D-078):
    (position from 1, how many, id before, id after). Archived jobs are counted among
    the archived ones of their status, newest first."""
    if application.archived:
        siblings = list(
            session.exec(
                select(Application.id)
                .where(
                    Application.user_id == user.id,
                    Application.status == application.status,
                    col(Application.archived).is_(True),
                )
                .order_by(col(Application.last_activity_at).desc(), col(Application.id).desc())
            )
        )
    else:
        siblings = [card.id for card in column_of(session, user, application.status)]
    if application.id not in siblings:
        return 1, 1, None, None
    index = siblings.index(application.id)
    before = siblings[index - 1] if index > 0 else None
    after = siblings[index + 1] if index + 1 < len(siblings) else None
    return index + 1, len(siblings), before, after


def events_of(session: Session, application: Application) -> list[ApplicationEvent]:
    """The timeline of an application the caller already owns, oldest first."""
    return list(
        session.exec(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application.id)
            .order_by(col(ApplicationEvent.id))
        )
    )
