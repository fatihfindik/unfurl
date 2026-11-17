"""CV documents (SPEC §5.3): a snapshot of the profile that can be edited without
changing the profile.

Each user has at most one master CV per language; copies point to it with
`parent_id` and are numbered (`version` 1, 2, …; the master is 0). A locked copy
(sent with an application, M4) never changes again. The rules live in
app/services/document_service.py (D-045…D-048).
"""

import datetime as dt

from sqlalchemy import JSON, Column, Index, column, true
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field
from app.models.profile import SHORT_TEXT

# Stored as text, not as database enums (like User.plan, D-025).
CV_LANGUAGES = ("de", "en", "tr")
CV_STATUSES = ("draft", "locked")
# The templates that exist (SPEC §6.5). Which ones a user may pick is decided by
# their plan (app.billing.entitlements.allowed_templates, CLAUDE.md).
CV_TEMPLATES = ("classic", "modern", "compact")
DEFAULT_TEMPLATE = "classic"


class CVDocument(SQLModel, table=True):
    __tablename__ = "cvdocument"
    __table_args__ = (
        # At most one master per user and language. A "partial" index: it only looks
        # at rows where is_master is true. The condition is built from a column
        # expression, so SQLite (1/0) and Postgres (true/false) each get their own SQL.
        Index(
            "uq_cvdocument_master_per_language",
            "user_id",
            "language",
            unique=True,
            sqlite_where=column("is_master").is_(true()),
            postgresql_where=column("is_master").is_(true()),
        ),
        # A version number is used once per master (D-045): two "Save as new
        # version" clicks at the same moment can't both become v2.
        Index("uq_cvdocument_parent_version", "parent_id", "version", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)

    title: str = Field(max_length=SHORT_TEXT)
    language: str = Field(max_length=2)  # one of CV_LANGUAGES
    template: str = Field(default=DEFAULT_TEMPLATE, max_length=20)
    # The CV itself: app.schemas.cv.CVContent as JSON (D-043). Code always assigns a
    # new dict, because SQLAlchemy doesn't notice changes inside a JSON value.
    content: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    show_photo: bool = Field(default=False)  # used from M3b (D-052)
    show_birth_date: bool = Field(default=False)  # used from M3b
    show_nationality: bool = Field(default=False)  # its own box (D-053)
    source_profile_version: int = Field(default=0)  # Profile.version at the snapshot

    # The application this copy was made for (D-080): all versions for one application
    # share it; Application.cv_id says which one is used now. Masters have none.
    application_id: int | None = Field(
        default=None, foreign_key="application.id", ondelete="SET NULL", index=True
    )

    is_master: bool = Field(default=False)
    # The master this copy belongs to. SET NULL is only a safety net: a master
    # with copies can't be deleted (D-047).
    parent_id: int | None = Field(default=None, foreign_key="cvdocument.id", ondelete="SET NULL")
    version: int = Field(default=0)  # 0 for the master, then 1, 2, … per master
    status: str = Field(default="draft", max_length=10)  # one of CV_STATUSES
    locked_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"
