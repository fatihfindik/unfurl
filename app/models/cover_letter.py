"""Cover letters (SPEC §5.4, D-152): written by the user, kept like CVs.

Each user has at most one master letter per language; copies point to it with
`parent_id` and are numbered (`version` 1, 2, …; the master is 0). A locked copy
(sent with an application, M8b) never changes again. The rules are shared with CVs
in app/services/lineage.py (D-153); the letter's own rules are in
app/services/cover_letter_service.py.

Unlike a CV, a letter is plain columns, not JSON: it has no nested sections, and
each form field is one column.
"""

import datetime as dt

from sqlalchemy import Column, Index, Text, column, true
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field
from app.models.profile import SHORT_TEXT

# Multi-line address fields: street, postcode and city, maybe a country.
ADDRESS_TEXT = 500
# About three pages. The column is TEXT; the form checks this limit (D-152).
LETTER_BODY_TEXT = 10_000


class CoverLetter(SQLModel, table=True):
    __tablename__ = "coverletter"
    __table_args__ = (
        # At most one master per user and language (a partial index, like CVDocument).
        Index(
            "uq_coverletter_master_per_language",
            "user_id",
            "language",
            unique=True,
            sqlite_where=column("is_master").is_(true()),
            postgresql_where=column("is_master").is_(true()),
        ),
        # A version number is used once per master (D-045).
        Index("uq_coverletter_parent_version", "parent_id", "version", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)

    title: str = Field(max_length=SHORT_TEXT)
    language: str = Field(max_length=2)  # one of CV_LANGUAGES

    # The sender: copied from the profile, then the user's own (D-154).
    sender_name: str | None = Field(default=None, max_length=SHORT_TEXT)
    sender_email: str | None = Field(default=None, max_length=320)
    sender_phone: str | None = Field(default=None, max_length=SHORT_TEXT)
    sender_address: str | None = Field(default=None, max_length=ADDRESS_TEXT)

    recipient_company: str | None = Field(default=None, max_length=SHORT_TEXT)
    recipient_name: str | None = Field(default=None, max_length=SHORT_TEXT)
    recipient_address: str | None = Field(default=None, max_length=ADDRESS_TEXT)

    # The city in the date line ("Wien, 21.11.2026"). The date itself is not stored:
    # it is today until the letter is locked (D-154, M8b).
    place: str | None = Field(default=None, max_length=SHORT_TEXT)
    subject: str = Field(default="", max_length=SHORT_TEXT)
    salutation: str = Field(default="", max_length=SHORT_TEXT)
    body: str = Field(default="", sa_column=Column(Text, nullable=False, default=""))
    closing: str = Field(default="", max_length=SHORT_TEXT)

    source_profile_version: int = Field(default=0)  # Profile.version when the sender was copied

    is_master: bool = Field(default=False)
    # SET NULL is only a safety net: a master with copies can't be deleted (D-047).
    parent_id: int | None = Field(default=None, foreign_key="coverletter.id", ondelete="SET NULL")
    version: int = Field(default=0)  # 0 for the master, then 1, 2, … per master
    status: str = Field(default="draft", max_length=10)  # "draft" or "locked"
    locked_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()

    @property
    def is_locked(self) -> bool:
        return self.status == "locked"
