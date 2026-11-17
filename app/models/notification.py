"""In-app notifications (SPEC §5.11, plan M11a).

A notification is written once by the scheduler (app/services/notification_service.py)
and then only marked read. `dedupe_key` names the reminder, e.g.
"follow_up:12:2026-11-20": the same key can't be written twice for a user (D-100).
"""

import datetime as dt

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field

NOTIFICATION_TYPES = ("follow_up", "interview_tomorrow", "stale_saved")  # M11a; more in M11b
TITLE_TEXT = 200
BODY_TEXT = 300


class Notification(SQLModel, table=True):
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key"),
        Index("ix_notification_unread", "user_id", "read_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    # The application it is about; deleting the application deletes its notifications.
    application_id: int | None = Field(
        default=None, foreign_key="application.id", ondelete="CASCADE", index=True
    )
    type: str = Field(max_length=30)  # NOTIFICATION_TYPES
    title: str = Field(max_length=TITLE_TEXT)
    body: str = Field(max_length=BODY_TEXT)
    link_url: str = Field(max_length=500)  # always a path of this app, e.g. /applications/12
    dedupe_key: str = Field(max_length=120)
    read_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
