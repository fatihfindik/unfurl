"""SQLModel tables. Each model module is imported here so Alembic sees every table."""

from sqlmodel import SQLModel

# Give every constraint and index a predictable name, e.g. "fk_application_user_id_user".
# Without names, SQLite and Postgres invent different ones, and a later migration
# that drops or changes a constraint can't find it.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Imported after the naming convention is set, so the tables use it.
from app.models.api_token import ApiToken  # noqa: E402
from app.models.application import (  # noqa: E402
    Application,
    ApplicationDocument,
    ApplicationEvent,
)
from app.models.change_set import PendingChangeSet  # noqa: E402
from app.models.cover_letter import CoverLetter  # noqa: E402
from app.models.cv import CVDocument  # noqa: E402
from app.models.interview import InterviewQuestion, InterviewStory  # noqa: E402
from app.models.learning import (  # noqa: E402
    LearningGoal,
    LearningResource,
    LearningTask,
    NotificationPreference,
    StudyLog,
)
from app.models.notification import Notification  # noqa: E402
from app.models.profile import (  # noqa: E402
    Certification,
    Education,
    Experience,
    LanguageSkill,
    Profile,
    Project,
    Skill,
    Story,
)
from app.models.security_counter import SecurityCounter  # noqa: E402
from app.models.trash import DeletedItem  # noqa: E402
from app.models.user import User  # noqa: E402

__all__ = [
    "PendingChangeSet",
    "SecurityCounter",
    "ApiToken",
    "Application",
    "ApplicationDocument",
    "ApplicationEvent",
    "CoverLetter",
    "CVDocument",
    "DeletedItem",
    "Certification",
    "Education",
    "Experience",
    "InterviewQuestion",
    "InterviewStory",
    "LanguageSkill",
    "LearningGoal",
    "LearningResource",
    "LearningTask",
    "Notification",
    "NotificationPreference",
    "Profile",
    "Project",
    "Skill",
    "StudyLog",
    "Story",
    "User",
]
