"""Deleted rows, kept for a short time so a delete can be undone (D-086).

A delete copies the row into DeletedItem as JSON and then really deletes it. Undo puts
it back as a new row. Kept for UNDO_SECONDS plus a minute, then purged; deleting the
account deletes these too (cascade).
"""

import datetime as dt

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, updated_at_field

# What can be restored: "cv", or the key of a profile section ("experience",
# "skills", ...; app/routers/profile.py SECTIONS).


class DeletedItem(SQLModel, table=True):
    __tablename__ = "deleteditem"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    kind: str = Field(max_length=30)  # "cv" or a profile section key
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))

    created_at: dt.datetime = created_at_field()  # when it was deleted
    updated_at: dt.datetime = updated_at_field()
