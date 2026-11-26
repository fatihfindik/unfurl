"""The user's profile and its sections (SPEC §5.2). CVs, cover letters and fit
analysis are built from it.

One Profile per user. Every section row (Experience, Education, ...) belongs to a
profile through `profile_id`; there is no `user_id` on them, so queries check the
owner through the profile (app/services/profile_service.py, D-032).

ON DELETE CASCADE: deleting a user deletes their profile, and deleting a profile
deletes its rows. The database does this itself (SQLite too, see app/db.py, D-034).
"""

# `import datetime as dt`: Certification has a column called `date`, which would hide
# the `date` type if it were imported by that name.
import datetime as dt

from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, updated_at_field

# Upper limits for text fields, also checked by the forms (app/schemas/profile.py).
SHORT_TEXT = 200
URL_TEXT = 500
LONG_TEXT = 5000


class Profile(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", unique=True, index=True)

    headline: str | None = Field(default=None, max_length=SHORT_TEXT)
    summary: str | None = Field(default=None, max_length=LONG_TEXT)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    website: str | None = Field(default=None, max_length=URL_TEXT)
    linkedin_url: str | None = Field(default=None, max_length=URL_TEXT)
    github_url: str | None = Field(default=None, max_length=URL_TEXT)
    # Optional for DACH CVs; never sent to the AI (SPEC §8.4, §10.2).
    date_of_birth: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=SHORT_TEXT)
    # Optional photo (DACH CVs). Path inside UPLOAD_DIR, e.g. "photos/3f9c...e1.jpg";
    # the file name is random, never the user's (app/services/photo_service.py, D-038).
    photo_path: str | None = Field(default=None, max_length=SHORT_TEXT)

    # Goes up by 1 on every change, so cached AI results (fit analysis) know the
    # profile changed (SPEC §5.2, §7).
    version: int = Field(default=0)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


# --- Sections ---------------------------------------------------------------------
# Each section row has `profile_id` and `sort_order` (shown in that order; M2b lets the
# user change it). The columns are written out per table on purpose: a shared base
# class can't share created_at/updated_at columns between tables.


class Experience(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    company: str = Field(max_length=SHORT_TEXT)
    title: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    start_date: dt.date
    end_date: dt.date | None = None  # None = still working here ("present")
    description: str | None = Field(default=None, max_length=LONG_TEXT)
    # A list of short achievement lines. SQLAlchemy does not notice changes made inside
    # this list (bullets.append(...)), so code always assigns a new list.
    bullets: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class Education(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    institution: str = Field(max_length=SHORT_TEXT)
    degree: str | None = Field(default=None, max_length=SHORT_TEXT)
    field: str | None = Field(default=None, max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    start_date: dt.date
    end_date: dt.date | None = None  # None = still studying
    description: str | None = Field(default=None, max_length=LONG_TEXT)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class Skill(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    name: str = Field(max_length=SHORT_TEXT)
    category: str | None = Field(default=None, max_length=SHORT_TEXT)  # e.g. "Tools"
    level: int | None = None  # 1-5

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


# CEFR levels plus "native". Stored as text, not a database enum (like User.plan).
LANGUAGE_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2", "native")


class LanguageSkill(SQLModel, table=True):
    __tablename__ = "language_skill"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    language: str = Field(max_length=SHORT_TEXT)
    level: str = Field(max_length=10)  # one of LANGUAGE_LEVELS

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class Certification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    name: str = Field(max_length=SHORT_TEXT)
    issuer: str | None = Field(default=None, max_length=SHORT_TEXT)
    date: dt.date | None = None
    url: str | None = Field(default=None, max_length=URL_TEXT)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


class Project(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    name: str = Field(max_length=SHORT_TEXT)
    description: str | None = Field(default=None, max_length=LONG_TEXT)
    url: str | None = Field(default=None, max_length=URL_TEXT)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()


# A STAR answer spoken in an interview takes about two minutes, so each part stays short
# (D-182). Tags are one comma-separated line ("leadership, conflict").
MAX_STORY_TEXT = 2000


class Story(SQLModel, table=True):
    """An interview story in the STAR format (M14b, D-181, D-182). Written once on My
    profile and picked for interviews (InterviewStory). Never part of a CV."""

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profile.id", ondelete="CASCADE", index=True)
    sort_order: int = Field(default=0)

    title: str = Field(max_length=SHORT_TEXT)
    situation: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    task: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    action: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    result: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tags: str | None = Field(default=None, max_length=SHORT_TEXT)
    # The job it happened in. Only an experience of the same profile (checked by the form
    # and by Undo); the database clears it when that experience is deleted.
    experience_id: int | None = Field(
        default=None, foreign_key="experience.id", ondelete="SET NULL", index=True
    )

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
