"""Learning (SPEC §5.10, plan M10): goals, where you study (resources), tasks and
time logged. Every table carries user_id, so each query filters by the user directly
(D-094). A goal's progress and color are not stored; they are worked out (D-096).
The rules live in app/services/learning_service.py.
"""

import datetime as dt

from sqlalchemy import JSON, Column, Text, true
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field
from app.models.profile import SHORT_TEXT, URL_TEXT

GOAL_STATUSES = ("active", "paused", "done")
GOAL_SOURCES = ("manual", "fit_gap", "assistant")
RESOURCE_KINDS = ("course", "book", "video", "platform", "class", "other")
UNIT_LABELS = ("Week", "Module", "Lesson", "Chapter")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # date.weekday() order
DEFAULT_REST_DAYS = ["sat", "sun"]
DEFAULT_REMINDER_TIME = dt.time(19, 0)


class LearningGoal(SQLModel, table=True):
    __tablename__ = "learninggoal"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    title: str = Field(max_length=SHORT_TEXT)
    description: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default="active", max_length=10)  # GOAL_STATUSES
    target_date: dt.date | None = None
    weekly_hours_target: float | None = None
    # Where the goal came from; a gap of a fit analysis comes with M7 (D-089).
    source: str = Field(default="manual", max_length=20)  # GOAL_SOURCES
    source_application_id: int | None = Field(
        default=None, foreign_key="application.id", ondelete="SET NULL"
    )
    source_requirement: str | None = Field(default=None, max_length=SHORT_TEXT)
    sort_order: int = Field(default=0)
    # Archived goals are hidden with their tasks but kept, history too (D-201).
    # `status` is left as it was, so Unarchive brings the goal back the same.
    archived_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class LearningResource(SQLModel, table=True):
    """Where the user studies for a goal, e.g. CS50P (Harvard), 10 weeks."""

    __tablename__ = "learningresource"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    goal_id: int = Field(foreign_key="learninggoal.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=SHORT_TEXT)
    provider: str | None = Field(default=None, max_length=SHORT_TEXT)
    url: str | None = Field(default=None, max_length=URL_TEXT)
    kind: str = Field(default="course", max_length=20)  # RESOURCE_KINDS
    unit_label: str = Field(default="Week", max_length=20)  # UNIT_LABELS
    units_total: int | None = None
    units_done: int = Field(default=0)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class LearningTask(SQLModel, table=True):
    __tablename__ = "learningtask"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    goal_id: int = Field(foreign_key="learninggoal.id", ondelete="CASCADE", index=True)
    resource_id: int | None = Field(
        default=None, foreign_key="learningresource.id", ondelete="SET NULL"
    )
    title: str = Field(max_length=SHORT_TEXT)
    due_date: dt.date = Field(index=True)
    estimated_minutes: int = Field(default=30)
    url: str | None = Field(default=None, max_length=URL_TEXT)
    done: bool = Field(default=False)
    done_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))
    sort_order: int = Field(default=0)
    # Soft delete from the MCP server (D-200): the row stays, every live query skips it,
    # and undo_changes clears it again. The web's Delete still removes the row (D-086).
    deleted_at: dt.datetime | None = Field(
        default=None, sa_column=datetime_column(nullable=True, index=True)
    )

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class StudyLog(SQLModel, table=True):
    """Time spent studying on a day. Completing a task logs its estimated time.

    Deleting the goal or task keeps the log (SET NULL): the hours and the streak of
    the past don't change (D-095)."""

    __tablename__ = "studylog"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    goal_id: int | None = Field(default=None, foreign_key="learninggoal.id", ondelete="SET NULL")
    task_id: int | None = Field(default=None, foreign_key="learningtask.id", ondelete="SET NULL")
    date: dt.date = Field(index=True)
    minutes: int

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class NotificationPreference(SQLModel, table=True):
    """One row per user (SPEC §5.11). M10 added the rest days (D-090), M11a the job
    search reminders (D-102), M11b the study reminder and the streak (D-110)."""

    __tablename__ = "notificationpreference"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", unique=True)
    rest_days: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REST_DAYS), sa_column=Column(JSON, nullable=False)
    )
    # server_default: rows saved before M11a get "on" too.
    follow_up_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    interview_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    stale_saved_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    # The daily study reminder and the streak (M11b, D-110).
    study_reminder_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    study_reminder_time: dt.time = Field(
        default=DEFAULT_REMINDER_TIME, sa_column_kwargs={"server_default": "19:00:00"}
    )
    study_reminder_email: bool = Field(default=True, sa_column_kwargs={"server_default": true()})
    streak_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": true()})

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
