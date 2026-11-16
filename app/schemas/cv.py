"""What a CV contains (D-043). `CVDocument.content` is this model as JSON.

A CV is a copy of the profile at one moment (a snapshot), so the fields mirror the
profile sections. Editing the CV only changes this copy, never the profile.

Old CVs stay readable when fields are added later: new fields get a default value,
and `schema_version` tells which shape a stored CV was written in.
"""

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.cv import CV_LANGUAGES
from app.models.profile import LONG_TEXT, SHORT_TEXT
from app.schemas.profile import ProfileForm

# A CV photo is a ~400 px JPEG, about 20–50 KB; as base64 text a bit more. Anything
# bigger than this was not made by us (D-052).
MAX_PHOTO_TEXT = 200_000

# The sections a user can hide on a CV (the name and contact lines are always shown).
CV_SECTIONS = (
    "summary",
    "experience",
    "education",
    "skills",
    "languages",
    "certifications",
    "projects",
)


class CVPersonal(BaseModel):
    full_name: str = ""
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    # The CV's own copy of the profile photo (D-052): a small JPEG as base64 text,
    # only while "Show photo" is on. A sent (locked) CV keeps the photo it was sent
    # with, whatever happens to the profile photo later. The limits stop odd content
    # (the type goes into a Content-Type header and a data: URI).
    photo: str | None = Field(
        default=None, max_length=MAX_PHOTO_TEXT, pattern=r"^[A-Za-z0-9+/]+={0,2}$"
    )
    photo_type: Literal["image/jpeg"] | None = None
    # Copied from the profile only while their box is on (D-053), like the photo.
    # Never sent to the AI: see CVContent.without_private().
    date_of_birth: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=SHORT_TEXT)


class CVBullet(BaseModel):
    text: str
    selected: bool = True  # unticked bullets stay in the CV but are not shown


class CVExperience(BaseModel):
    title: str
    company: str
    location: str | None = None
    start_date: dt.date
    end_date: dt.date | None = None  # None = "present"
    description: str | None = None
    bullets: list[CVBullet] = Field(default_factory=list)


class CVEducation(BaseModel):
    institution: str
    degree: str | None = None
    field: str | None = None
    location: str | None = None
    start_date: dt.date
    end_date: dt.date | None = None
    description: str | None = None


class CVSkill(BaseModel):
    name: str
    category: str | None = None
    level: int | None = None


class CVLanguage(BaseModel):
    language: str
    level: str


class CVCertification(BaseModel):
    name: str
    issuer: str | None = None
    date: dt.date | None = None
    url: str | None = None


class CVProject(BaseModel):
    name: str
    description: str | None = None
    url: str | None = None


class CVContent(BaseModel):
    schema_version: int = 2  # 2: photo, date of birth, nationality (M3b)
    personal: CVPersonal = Field(default_factory=CVPersonal)
    summary: str | None = None
    experience: list[CVExperience] = Field(default_factory=list)
    education: list[CVEducation] = Field(default_factory=list)
    skills: list[CVSkill] = Field(default_factory=list)
    languages: list[CVLanguage] = Field(default_factory=list)
    certifications: list[CVCertification] = Field(default_factory=list)
    projects: list[CVProject] = Field(default_factory=list)
    hidden_sections: list[str] = Field(default_factory=list)  # names from CV_SECTIONS

    def without_private(self) -> "CVContent":
        """A copy without the photo, date of birth and nationality, for anything that
        goes to the AI (M7+; SPEC §9.1, §10.2, D-053)."""
        copy = self.model_copy(deep=True)
        copy.personal.photo = None
        copy.personal.photo_type = None
        copy.personal.date_of_birth = None
        copy.personal.nationality = None
        return copy

    def to_json(self) -> dict:
        """For CVDocument.content (dates become "2021-03-01" text)."""
        return self.model_dump(mode="json")


# --- Forms --------------------------------------------------------------------------
# Checked like the profile forms: text is stripped, empty counts as missing, and
# form_errors() (app/schemas/profile.py) turns problems into short messages.

LANGUAGE_NAMES = {"en": "English", "de": "German", "tr": "Turkish"}


class NewCVForm(ProfileForm):
    title: str = Field(max_length=SHORT_TEXT)
    language: str
    template: str  # checked against the user's plan in the router (entitlements)

    @field_validator("language")
    @classmethod
    def known_language(cls, value: str) -> str:
        if value not in CV_LANGUAGES:
            raise ValueError("Choose a language.")
        return value


# Bullet checkboxes send "<experience position>-<bullet position>", e.g. "0-2".
_BULLET_KEY = re.compile(r"\d{1,2}-\d{1,2}")
MAX_BULLET_KEYS = 50 * 20  # 50 experiences × 20 bullets (the profile limits)


class CVEditForm(ProfileForm):
    """The editor form (D-049): title, headline, summary, which sections and which
    experience bullets are shown. Everything else comes from the profile."""

    title: str = Field(max_length=SHORT_TEXT)
    headline: str | None = Field(default=None, max_length=SHORT_TEXT)
    summary: str | None = Field(default=None, max_length=LONG_TEXT)
    visible_sections: list[str] = Field(default_factory=list)  # ticked "Sections" boxes
    bullets: list[str] = Field(default_factory=list, max_length=MAX_BULLET_KEYS)

    @field_validator("visible_sections")
    @classmethod
    def known_sections(cls, value: list[str]) -> list[str]:
        if any(name not in CV_SECTIONS for name in value):
            raise ValueError("Choose sections from the list.")
        return value

    @field_validator("bullets")
    @classmethod
    def bullet_keys(cls, value: list[str]) -> list[str]:
        if any(not _BULLET_KEY.fullmatch(key) for key in value):
            raise ValueError("Choose bullets from the list.")
        return value

    def apply_to(self, content: CVContent) -> CVContent:
        """A copy of `content` with this form's changes."""
        changed = content.model_copy(deep=True)
        changed.personal.headline = self.headline
        changed.summary = self.summary
        changed.hidden_sections = [
            name for name in CV_SECTIONS if name not in self.visible_sections
        ]
        ticked = set(self.bullets)
        for i, job in enumerate(changed.experience):
            for j, bullet in enumerate(job.bullets):
                bullet.selected = f"{i}-{j}" in ticked
        return changed
