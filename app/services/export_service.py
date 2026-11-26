"""Export my data (D-130, SPEC §10.2): everything of a user as one JSON file.

The file is written piece by piece (a generator), never built in memory: with the
app's limits one account could reach gigabytes (2000 jobs with long descriptions
and notes). Rows are read in pages of BATCH, each in a short session of its own.

Every table that belongs to a user, directly or through another table, is either
exported (SECTIONS) or in EXCLUDED_TABLES with a reason. tests/test_export.py walks
the foreign keys from `user` and fails when a new table is in neither list, so a
table added later (cover letters, chats, …) can't be forgotten.
"""

import base64
import datetime as dt
import json
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, col, select

from app.models import (
    Application,
    ApplicationDocument,
    ApplicationEvent,
    Certification,
    CoverLetter,
    CVDocument,
    Education,
    Experience,
    InterviewQuestion,
    LanguageSkill,
    LearningGoal,
    LearningResource,
    LearningTask,
    Notification,
    NotificationPreference,
    Profile,
    Project,
    Skill,
    StudyLog,
    User,
)
from app.models.base import utcnow
from app.services import photo_service

FORMAT = "unfurl-export"
VERSION = 1
BATCH = 200

# Never in the file: secrets, internal state, and the owner (it's always the same user).
# photo_path is where the file is stored; the photo itself is in "profile_photo".
DROPPED_FIELDS = frozenset({"user_id", "password_hash", "session_version", "photo_path"})

# Tables of a user that are not exported, and why.
EXCLUDED_TABLES = {
    "deleteditem": "Copies of deleted rows, kept for a few minutes for Undo (D-086).",
    "apitoken": "Extension sign-ins; the token hash is a secret (D-136).",
}


@dataclass(frozen=True)
class Section:
    key: str  # the name in the file
    model: type[SQLModel]
    owner: str  # how a row belongs to the user: "user", "profile" or "application"


# In file order. Rows of one section are sorted by id.
SECTIONS = (
    Section("experience", Experience, "profile"),
    Section("education", Education, "profile"),
    Section("skills", Skill, "profile"),
    Section("languages", LanguageSkill, "profile"),
    Section("certifications", Certification, "profile"),
    Section("projects", Project, "profile"),
    Section("cvs", CVDocument, "user"),
    Section("cover_letters", CoverLetter, "user"),
    Section("applications", Application, "user"),
    Section("application_events", ApplicationEvent, "application"),
    Section("application_documents", ApplicationDocument, "user"),
    Section("interview_questions", InterviewQuestion, "user"),
    Section("learning_goals", LearningGoal, "user"),
    Section("learning_resources", LearningResource, "user"),
    Section("learning_tasks", LearningTask, "user"),
    Section("study_log", StudyLog, "user"),
    Section("notifications", Notification, "user"),
)
# The single rows: the account, the profile and the settings.
SINGLE_TABLES = ("user", "profile", "notificationpreference")
EXPORTED_TABLES = frozenset(SINGLE_TABLES) | {section.model.__tablename__ for section in SECTIONS}


def file_name(now: dt.datetime | None = None) -> str:
    return f"unfurl-export-{(now or utcnow()).date().isoformat()}.json"


def as_dict(row: SQLModel) -> dict:
    """A row as JSON-ready values (dates as ISO text), without DROPPED_FIELDS."""
    return {
        key: value
        for key, value in row.model_dump(mode="json").items()
        if key not in DROPPED_FIELDS
    }


def _query(section: Section, user_id: int, after_id: int):
    """One page of a section: the next BATCH rows after `after_id` (keyset paging)."""
    model = section.model
    if section.owner == "profile":
        query = select(model).join(Profile, Profile.id == model.profile_id)
        query = query.where(Profile.user_id == user_id)
    elif section.owner == "application":
        query = select(model).join(Application, Application.id == model.application_id)
        query = query.where(Application.user_id == user_id)
    else:
        query = select(model).where(model.user_id == user_id)
    return query.where(col(model.id) > after_id).order_by(col(model.id)).limit(BATCH)


def _array(engine: Engine, section: Section, user_id: int) -> Iterator[str]:
    """A section as a JSON array. Each page is read in its own short session, which is
    closed before the text is sent: a slow download never holds a database
    connection (security review M13b, M1)."""
    yield "["
    after_id, first = 0, True
    while True:
        with Session(engine) as session:
            page = [as_dict(row) for row in session.exec(_query(section, user_id, after_id))]
        for row in page:
            yield ("" if first else ",") + json.dumps(row, ensure_ascii=False)
            first = False
        if len(page) < BATCH:
            break
        after_id = page[-1]["id"]
    yield "]"


def _photo(profile: Profile | None) -> dict | None:
    """The profile photo as base64 (at most 2 MB, SPEC §10.1), or None."""
    if profile is None or not profile.photo_path:
        return None
    path = photo_service.photo_file(profile.photo_path)
    if path is None:
        return None
    return {
        "media_type": photo_service.media_type(profile.photo_path),
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def stream(engine: Engine, user_id: int, now: dt.datetime | None = None) -> Iterator[str]:
    """The export file of `user_id`, as pieces of text. Reads with short sessions of
    its own (`engine`), so it can run after the route's session is closed."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user is None:  # deleted after the request came in: still a valid file
            yield "{}"
            return
        profile = session.exec(select(Profile).where(Profile.user_id == user_id)).first()
        prefs = session.exec(
            select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        ).first()
        head = {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": (now or utcnow()).isoformat(),
            "account": as_dict(user),
            "profile": as_dict(profile) if profile else None,
            "profile_photo": _photo(profile),
            "notification_preferences": as_dict(prefs) if prefs else None,
        }
    # The head without its closing "}", then one array per section.
    yield json.dumps(head, ensure_ascii=False)[:-1]
    for section in SECTIONS:
        yield f', "{section.key}": '
        yield from _array(engine, section, user_id)
    yield "}"
