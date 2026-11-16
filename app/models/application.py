"""Applications (SPEC §5.5, §5.6): a saved job and what happened to it.

An application sits in one column of the board (`status`) at a place in that column
(`position`). Every change that matters writes an ApplicationEvent; the events are the
timeline, and Undo removes the event it takes back (D-064).

Only the columns M4a uses are here (D-061); next step, interview, notes and
documents come with M4b and M4c. The rules live in app/services/application_service.py.
"""

import datetime as dt

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field, utcnow
from app.models.profile import SHORT_TEXT

# Stored as text, not as database enums (like User.plan, D-025).
STATUSES = ("saved", "applied", "interview", "offer", "rejected", "withdrawn")
# The columns of the board, in order. "withdrawn" has no column yet.
BOARD_STATUSES = ("saved", "applied", "interview", "offer", "rejected")
REMOTE_TYPES = ("onsite", "hybrid", "remote", "unknown")
SOURCES = (
    "linkedin",
    "stepstone",
    "karriere_at",
    "indeed",
    "xing",
    "company_site",
    "other",
    "manual",
)
EVENT_TYPES = ("created", "status_changed", "archived", "restored")

# Job links are often long (tracking parameters), longer than profile links.
JOB_URL_TEXT = 2000
# The page text the extension will send later is cut at this length (SPEC §5.5).
MAX_DESCRIPTION = 50_000


class Application(SQLModel, table=True):
    __tablename__ = "application"
    __table_args__ = (
        # The board reads one user's cards column by column, in order.
        Index("ix_application_board", "user_id", "archived", "status", "position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)

    company: str = Field(max_length=SHORT_TEXT)
    job_title: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    remote_type: str = Field(default="unknown", max_length=10)  # one of REMOTE_TYPES
    job_url: str | None = Field(default=None, max_length=JOB_URL_TEXT)
    source: str = Field(default="manual", max_length=20)  # one of SOURCES
    description_raw: str = Field(default="", sa_column=Column(Text, nullable=False))

    status: str = Field(default="saved", max_length=10)  # one of STATUSES
    # The place in the board column, 0 at the top. Archived cards keep their last
    # number but aren't counted (D-063).
    position: int = Field(default=0)
    applied_at: dt.date | None = Field(default=None)  # UTC date for now (D-061)
    archived: bool = Field(default=False)
    # "Updated" on cards and in the list: changes on add, status change, archive and
    # restore only. Not updated_at: moving one card renumbers its neighbours, and
    # that must not make them look changed (D-061).
    last_activity_at: dt.datetime = Field(
        default_factory=utcnow, sa_column=datetime_column(nullable=False)
    )

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class ApplicationEvent(SQLModel, table=True):
    """One line of an application's timeline. Read only through an application that
    was already checked to belong to the user, never by id alone."""

    __tablename__ = "applicationevent"

    id: int | None = Field(default=None, primary_key=True)
    application_id: int = Field(foreign_key="application.id", ondelete="CASCADE", index=True)
    type: str = Field(max_length=30)  # one of EVENT_TYPES
    from_status: str | None = Field(default=None, max_length=10)
    to_status: str | None = Field(default=None, max_length=10)
    message: str | None = Field(default=None, max_length=SHORT_TEXT)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
