"""Rules for applications (SPEC §5.5, §5.6, §6.7; D-061…D-066).

Routes only call this module. Every query filters by user_id (CLAUDE.md).
"""

import datetime as dt
import hashlib
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlmodel import Session, col, delete, func, select

from app.models import Application, ApplicationEvent, User
from app.models.application import BOARD_STATUSES, INTERVIEW_TYPES, NEXT_ACTION_TYPES
from app.models.base import as_utc, utcnow
from app.services import clock, profile_service
from app.services import next_step_service as next_step_text

MAX_APPLICATIONS_PER_USER = 2000  # archived ones count too (D-061)
MAX_ID = profile_service.MAX_ID

# Job sites we know, by a name in the host (D-062). "stepstone" and "indeed" use many
# country domains (stepstone.at, at.indeed.com), so any label of the host may match.
_SOURCE_BY_DOMAIN = {"linkedin.com": "linkedin", "karriere.at": "karriere_at", "xing.com": "xing"}
_SOURCE_BY_LABEL = {"stepstone": "stepstone", "indeed": "indeed"}


# Sources that only come from a link: without a link they become "manual" again.
SOURCES_FROM_LINKS = ("linkedin", "stepstone", "karriere_at", "indeed", "xing", "company_site")


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


def get_owned_application(
    session: Session, user: User, application_id, *, lock: bool = False
) -> Application:
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
    query = select(Application).where(Application.id == number, Application.user_id == user.id)
    if lock:
        # Undo reads, compares and writes: on Postgres the row stays locked until the
        # commit, so a change at the same moment waits (security review M4b, 2).
        # SQLite has no row locks and runs one write at a time anyway.
        query = query.with_for_update()
    application = session.exec(query).first()
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
    if field == "next_action_type":  # only known kinds: the page looks up their label
        return value if value in NEXT_ACTION_TYPES else _bad(field)
    if field == "interview_type":
        return value if value in INTERVIEW_TYPES else _bad(field)
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


def fingerprint(value) -> str | None:
    """A short stand-in for a text: the token only compares the "after" text, it never
    writes it back, so a hash is enough and keeps the token small (security review
    M4b, 1)."""
    if value is None:
        return None
    return "#" + hashlib.sha256(str(value).encode()).hexdigest()[:16]


def _after(field: str, value):
    return fingerprint(value) if field in _TEXT_FIELDS and field != "status" else value


def changes(before: dict, after: dict) -> dict:
    """{field: [before, after]} for the fields an action changed (texts after the
    change only as a fingerprint). The place in the column ("index") only matters when
    the card changed column or was archived."""
    changed = {f: [before[f], _after(f, after[f])] for f in NOTE_FIELDS if before[f] != after[f]}
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
        application = get_owned_application(session, user, application_id, lock=True)
    except HTTPException:
        raise NothingToUndo from None
    if not fields or newest_event_id(session, application) != newest:
        raise NothingToUndo
    current = note(session, user, application)
    if any(_after(f, current[f]) != values[1] for f, values in fields.items() if f != "index"):
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


def creation_fingerprint(application: Application) -> str:
    """What "add" left behind, for its Undo: if details or a snooze changed since (they
    write no event), Undo of "add" must not delete them (security review M4b, 3)."""
    values = [getattr(application, field) for field in (*DETAIL_FIELDS, "snoozed_until")]
    return fingerprint("|".join(str(_to_text(value)) for value in values))


def undo(
    session: Session,
    user: User,
    application_id,
    event_ids: list[int],
    before: dict | None,
    created: str | None = None,
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
        if created is not None and creation_fingerprint(application) != created:
            session.rollback()
            raise NothingToUndo  # edited since it was added: keep it
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


def set_interview(
    session: Session,
    user: User,
    application: Application,
    *,
    at: dt.datetime,
    interview_type: str | None,
    interview_with: str | None,
) -> list[ApplicationEvent]:
    """Save the interview from the modal (D-076). A job that isn't in Interview yet is
    moved there first. A new date also clears a next step set by hand: the interview
    is the next step again (D-073). Returns the events written."""
    events = []
    if application.archived:
        events.append(restore(session, user, application))
    if application.status != "interview":
        events.append(move(session, user, application, "interview"))
    application.interview_at = at
    application.interview_type = interview_type
    application.interview_with = interview_with
    application.last_activity_at = utcnow()
    clear_next_step(application)
    events.append(
        _add_event(
            session,
            application,
            "interview_scheduled",
            message=f"Interview set for {next_step_text.when_text(at)}",
        )
    )
    session.add(application)
    session.commit()
    session.refresh(application)
    return events


class NoNextStep(Exception):
    """There is no next step to finish."""


def change_next_step(
    session: Session,
    user: User,
    application: Application,
    *,
    next_action_type: str,
    at: dt.datetime,
    note: str | None,
) -> ApplicationEvent:
    """ "Change" on the Next step card (D-073): set it by hand. It stays until the
    status changes; a snooze ends."""
    application.next_action_manual = True
    application.next_action_type = next_action_type
    application.next_action_at = at
    application.next_action_note = note
    application.snoozed_until = None
    step = next_step_text.next_step(application, utcnow())
    event = _add_event(
        session,
        application,
        "next_step_changed",
        message=f"Next step: {next_step_text.title(step)}",
    )
    session.add(application)
    session.commit()
    session.refresh(application)
    return event


def finish_next_step(session: Session, user: User, application: Application) -> ApplicationEvent:
    """ "Done" (D-073): after the thank-you note a follow-up 5 days later, after
    anything else no next step until the status changes."""
    now = utcnow()
    step = next_step_text.next_step(application, now)
    if step is None:
        raise NoNextStep
    next_type, next_at = next_step_text.after_done(application, now)
    application.next_action_manual = True
    application.next_action_type = next_type
    application.next_action_at = next_at
    application.next_action_note = None
    application.snoozed_until = None
    event = _add_event(session, application, "next_step_changed", message=f"Done: {step.label}")
    session.add(application)
    session.commit()
    session.refresh(application)
    return event


DETAIL_FIELDS = (
    "company",
    "job_title",
    "location",
    "remote_type",
    "job_url",
    "posting_closes_at",
    "contact_name",
    "contact_email",
    "salary_text",
    "notes",
)


def update_details(
    session: Session, user: User, application: Application, values: dict
) -> Application:
    """The Details form (D-075). `values` were checked by DetailsForm; only the
    DETAIL_FIELDS are taken. A new link also gives a new source. No event: details
    are not part of the timeline, and the form can simply be edited again."""
    for field in DETAIL_FIELDS:
        setattr(application, field, values.get(field))
    application.source = (
        detect_source(application.job_url)
        if application.job_url
        else ("manual" if application.source in SOURCES_FROM_LINKS else application.source)
    )
    session.add(application)
    session.commit()
    session.refresh(application)
    return application


# --- Needs attention (SPEC §6.3, D-077) ---------------------------------------------------

SNOOZE_DAYS = 3


@dataclass(frozen=True)
class Attention:
    """One line of "Needs attention"."""

    application: Application
    step: "next_step_text.NextStep"
    group: int  # 0 deadlines and offers, 1 interview, 2 follow up, 3 waiting
    tag: str  # "Overdue", "Due today", "Offer", "Interview", "Follow up", "Waiting"


def needs_attention(session: Session, user: User, now: dt.datetime) -> list[Attention]:
    """The user's jobs whose next step is due today or overdue, most urgent first
    (SPEC §6.3): deadlines and offers → interviews → follow-ups → waiting saved jobs;
    in each group the oldest date first. Snoozed and archived jobs are left out.
    Worked out in Python: at most 2,000 jobs, the next step is computed (D-073)."""
    jobs = session.exec(
        select(Application).where(
            Application.user_id == user.id,
            col(Application.status).in_(BOARD_STATUSES),
            col(Application.archived).is_(False),
        )
    )
    today = clock.local(now).date()
    items = []
    for job in jobs:
        if job.snoozed_until is not None and as_utc(job.snoozed_until) > as_utc(now):
            continue
        step = next_step_text.next_step(job, now)
        if step is None or not step.is_due(today):
            continue
        items.append(Attention(job, step, *_attention_group(step, today)))
    items.sort(key=lambda item: (item.group, as_utc(item.step.at), item.application.id))
    return items


def _attention_group(step, today: dt.date) -> tuple[int, str]:
    if step.waiting:
        return 3, "Waiting"
    if step.type in ("interview", "thank_you"):
        return 1, "Interview"
    if step.type == "follow_up":
        return 2, "Follow up"
    if step.type == "reply_offer":
        return 0, "Offer"
    overdue = clock.local(step.at).date() < today
    return 0, "Overdue" if overdue else "Due today"


def snooze(session: Session, user: User, application: Application, now: dt.datetime) -> None:
    """ "Snooze" (D-074): hide from Needs attention for 3 days. No event."""
    if application.archived:
        raise NotOnBoard
    application.snoozed_until = as_utc(now) + dt.timedelta(days=SNOOZE_DAYS)
    session.add(application)
    session.commit()
    session.refresh(application)


MAX_NOTES_PER_APPLICATION = 500  # keeps the timeline of one job a reasonable size


class TooManyNotes(Exception):
    """The application has MAX_NOTES_PER_APPLICATION notes already."""


def add_note(session: Session, user: User, application: Application, text: str) -> ApplicationEvent:
    """A note on the timeline (event "note_added"). The text was checked by NoteForm."""
    count = session.exec(
        select(func.count())
        .select_from(ApplicationEvent)
        .where(
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.type == "note_added",
        )
    ).one()
    if count >= MAX_NOTES_PER_APPLICATION:
        raise TooManyNotes
    event = _add_event(session, application, "note_added", message=text)
    session.commit()
    session.refresh(event)
    return event


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
