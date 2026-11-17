"""Undo for deletes (SPEC §6.0/2, D-086).

A delete keeps a JSON copy of the row in DeletedItem for a short time, then deletes the
row for real. Undo builds a new row from the copy. Copies older than KEEP are purged on
every delete and every Undo, so deleted personal data doesn't stay around.
"""

import datetime as dt

from sqlmodel import Session, SQLModel, delete, select

from app.models import DeletedItem, User
from app.models.base import as_utc, utcnow
from app.services.undo_service import UNDO_SECONDS


class CannotRestore(Exception):
    """The deleted row can't come back (its place is taken, a limit is reached, …)."""


KEEP = dt.timedelta(seconds=UNDO_SECONDS + 60)
# Columns that are never copied back: a restored row gets new ones.
_SKIPPED = ("id", "created_at", "updated_at")


def _to_json(value):
    if isinstance(value, dt.datetime):
        return as_utc(value).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def payload_of(row: SQLModel) -> dict:
    """The row's columns as JSON-friendly values (dates as text)."""
    return {
        column.name: _to_json(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in _SKIPPED
    }


def row_from(model: type[SQLModel], payload: dict) -> SQLModel:
    """A new row of `model` from a payload (dates read back from text)."""
    values = {}
    for column in model.__table__.columns:
        if column.name in _SKIPPED or column.name not in payload:
            continue
        value = payload[column.name]
        python_type = getattr(column.type, "python_type", None)
        if isinstance(value, str) and python_type is dt.datetime:
            value = dt.datetime.fromisoformat(value)
        elif isinstance(value, str) and python_type is dt.date:
            value = dt.date.fromisoformat(value)
        values[column.name] = value
    return model(**values)


def purge_old(session: Session) -> None:
    """Delete the copies whose Undo time is over (all users; not committed)."""
    session.exec(delete(DeletedItem).where(DeletedItem.created_at < utcnow() - KEEP))


def remember(session: Session, user: User, kind: str, payload: dict) -> DeletedItem:
    """Keep a copy before a delete (not committed: part of the delete)."""
    purge_old(session)
    item = DeletedItem(user_id=user.id, kind=kind, payload=payload)
    session.add(item)
    session.flush()  # gives it its id, for the Undo token
    return item


def take(session: Session, user: User, item_id) -> DeletedItem | None:
    """The user's copy `item_id` if it is still there and recent, else None. Taken
    copies are deleted by the caller in the same commit as the restore."""
    purge_old(session)
    if type(item_id) is not int:
        return None
    item = session.exec(
        select(DeletedItem).where(DeletedItem.id == item_id, DeletedItem.user_id == user.id)
    ).first()
    if item is None or as_utc(item.created_at) < utcnow() - KEEP:
        return None
    return item
