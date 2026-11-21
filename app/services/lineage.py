"""The master / copy / version rules shared by CVs and cover letters (D-153).

Both document types work the same way (SPEC §5.3, §5.4): at most one master per
user and language, copies point to it with `parent_id` and get the next free
`version` (the master is 0), locked copies never change (D-045…D-048, D-050).

Every function takes the model class (CVDocument or CoverLetter) as its first
argument, so the rules are written once. The models need these columns: id, user_id,
language, is_master, parent_id, version, status, created_at, updated_at.
This module imports no other service, so both document services can use it.
"""

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, or_, select

from app.models import User
from app.models.base import as_utc, utcnow


class DocumentLocked(Exception):
    """The document is locked (sent with an application) and can never change."""


class MasterNotUnlocked(Exception):
    """A master is changed only after the user unlocks it in the editor (D-048)."""


class MasterHasCopies(Exception):
    """A master with copies can't be deleted (D-047)."""

    def __init__(self, copies: int):
        super().__init__(f"{copies} copies")
        self.copies = copies


class TooManyDocuments(Exception):
    """The user already has the most documents of this type allowed."""


class NothingChanged(Exception):
    """A new version would be the same as the draft it comes from."""


# --- Reading ------------------------------------------------------------------------


def current_master(session: Session, model, user: User, language: str):
    return session.exec(
        select(model).where(model.user_id == user.id, model.language == language, model.is_master)
    ).first()


def versions_in_lineage(session: Session, model, user: User, document) -> list:
    """The master and all its copies, for the version picker (master first)."""
    master_id = document.id if document.is_master else document.parent_id
    if master_id is None:
        return [document]
    return list(
        session.exec(
            select(model)
            .where(
                model.user_id == user.id,
                or_(model.id == master_id, model.parent_id == master_id),
            )
            .order_by(model.version)
        )
    )


def copy_count(session: Session, model, master) -> int:
    return session.exec(
        select(func.count()).where(model.parent_id == master.id, model.user_id == master.user_id)
    ).one()


def never_edited(document) -> bool:
    """True while a document is exactly as it was created. New documents get the same
    created_at and updated_at (stamped); any save moves updated_at."""
    return as_utc(document.updated_at) == as_utc(document.created_at)


# --- Creating -----------------------------------------------------------------------


def check_room(session: Session, model, user: User, limit: int) -> None:
    """`limit` is passed on every call (not a default), so tests can lower it."""
    count = session.exec(select(func.count()).where(model.user_id == user.id)).one()
    if count >= limit:
        raise TooManyDocuments


def next_version(session: Session, model, user_id: int, master_id: int | None) -> int:
    """The next free number under a master: the highest one + 1 (D-045)."""
    highest = session.exec(
        select(func.max(model.version)).where(
            model.parent_id == master_id, model.user_id == user_id
        )
    ).one()
    return (highest or 0) + 1


def stamped(document):
    """Give a new document one moment for created_at and updated_at (see never_edited)."""
    now = utcnow()
    document.created_at = now
    document.updated_at = now
    return document


def save_copy(session: Session, model, copy):
    """Save a new copy with the next version number. If another request took the same
    number at the same moment, the unique index refuses it; try once more."""
    for attempt in range(2):
        copy.version = next_version(session, model, copy.user_id, copy.parent_id)
        session.add(copy)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if attempt == 1:
                raise
            continue
        session.refresh(copy)
        return copy
    raise AssertionError("unreachable")


def create_master_or_copy(session: Session, model, user: User, fields: dict):
    """A new document from `fields`. The first one in a language becomes the master;
    later ones are copies under that master (D-045)."""
    master = current_master(session, model, user, fields["language"])
    if master is None:
        document = stamped(model(**fields, is_master=True, version=0))
        session.add(document)
        try:
            session.commit()
            session.refresh(document)
            return document
        except IntegrityError:
            # Two "New" clicks at the same moment: the other one became the master.
            session.rollback()
            master = current_master(session, model, user, fields["language"])
            if master is None:
                raise  # some other database error, not the race: don't hide it

    return save_copy(session, model, stamped(model(**fields, parent_id=master.id)))


def check_new_version_allowed(source) -> None:
    """ "Save as new version" of a draft that was never edited would only give an
    identical copy (Fatih's test: v8, v9, v10 all the same). A locked copy is
    different: its new draft is where the changes go, so it is always allowed."""
    if not source.is_locked and not source.is_master and never_edited(source):
        raise NothingChanged


# --- Changing -----------------------------------------------------------------------


def ensure_changeable(document, master_unlocked: bool) -> None:
    """The rules for changing a document in place (CLAUDE.md, D-048):
    a locked document never changes; a master only after the user unlocked it.

    `master_unlocked` means: the user clicked "Edit master" and confirmed in the
    editor. Only the editor routes may pass True. The assistant (M12) and any other
    automatic caller must never pass it: they create a new draft instead (SPEC §9.8)."""
    if document.is_locked:
        raise DocumentLocked
    if document.is_master and not master_unlocked:
        raise MasterNotUnlocked


def ensure_deletable(session: Session, model, document) -> None:
    """Locked documents can't be deleted; a master only when it has no copies (D-047)."""
    if document.is_locked:
        raise DocumentLocked
    if document.is_master:
        copies = copy_count(session, model, document)
        if copies:
            raise MasterHasCopies(copies)


def fits_for_restore(session: Session, model, user: User, document) -> bool:
    """Undo of a delete (D-086): a master only while its language has no master again;
    a copy only while its master exists. A copy whose version number was taken
    meanwhile gets the next one (changed on `document`). False if it doesn't fit."""
    if document.is_master:
        return current_master(session, model, user, document.language) is None
    parent = session.get(model, document.parent_id) if document.parent_id else None
    if parent is None or parent.user_id != user.id:
        return False
    taken = session.exec(
        select(model.id).where(
            model.user_id == user.id,
            model.parent_id == document.parent_id,
            model.version == document.version,
        )
    ).first()
    if taken is not None:
        document.version = next_version(session, model, user.id, document.parent_id)
    return True
