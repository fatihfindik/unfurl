"""Checking the profile forms (SPEC §6.4, plan M2a §2).

Each form is a Pydantic model. The router turns the submitted form into a dict,
validates it here, and on errors shows `form_errors(...)` next to the fields.
Pydantic's own error texts ("String should have at most 200 characters") are not
shown to users; `form_errors` turns them into short messages.
"""

import datetime as dt
import re

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError, field_validator

from app.models.profile import LANGUAGE_LEVELS, LONG_TEXT, SHORT_TEXT, URL_TEXT

MAX_BULLETS = 20
BULLET_TEXT = 300
OLDEST_BIRTH_YEAR = 1900
YOUNGEST_AGE = 14

_email_adapter = TypeAdapter(EmailStr)
_URL = re.compile(r"https?://\S+")


class ProfileForm(BaseModel):
    """Shared cleaning for every profile form."""

    @field_validator("*", mode="before")
    @classmethod
    def clean_text(cls, value):
        # Spaces around text are removed, and an empty field counts as "not filled in".
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


def parse_month(value) -> dt.date | None:
    """ "2021-03" (from <input type="month">) -> 1 March 2021 (D-033)."""
    if isinstance(value, dt.date):
        return value
    value = str(value or "").strip()
    if not value:
        return None  # not filled in; a required field then says "This field is required."
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ValueError("Use the format YYYY-MM.")
    return dt.date(int(match.group(1)), int(match.group(2)), 1)


def check_url(value: str | None) -> str | None:
    if value is not None and not _URL.fullmatch(value):
        raise ValueError("Enter a full link starting with https://.")
    return value


def check_dates_in_order(start: dt.date | None, end: dt.date | None) -> None:
    if start and end and end < start:
        raise ValueError("The end date must be after the start date.")


# --- Personal info and summary ----------------------------------------------------


class PersonalInfoForm(ProfileForm):
    full_name: str = Field(max_length=SHORT_TEXT)  # stored on User, not Profile
    headline: str | None = Field(default=None, max_length=SHORT_TEXT)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    website: str | None = Field(default=None, max_length=URL_TEXT)
    linkedin_url: str | None = Field(default=None, max_length=URL_TEXT)
    github_url: str | None = Field(default=None, max_length=URL_TEXT)
    date_of_birth: dt.date | None = None
    nationality: str | None = Field(default=None, max_length=SHORT_TEXT)

    @field_validator("email")
    @classmethod
    def check_email(cls, value):
        if value is not None:
            try:
                _email_adapter.validate_python(value)
            except ValidationError:
                raise ValueError("Enter a valid email address.") from None
        return value

    @field_validator("website", "linkedin_url", "github_url")
    @classmethod
    def check_urls(cls, value):
        return check_url(value)

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def parse_birth_date(cls, value):
        if value is None or isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            raise ValueError("Use the format YYYY-MM-DD.") from None

    @field_validator("date_of_birth")
    @classmethod
    def check_birth_date(cls, value):
        if value is None:
            return value
        today = dt.date.today()
        if value.year < OLDEST_BIRTH_YEAR or value.year > today.year - YOUNGEST_AGE:
            raise ValueError("Enter a real date of birth.")
        return value


class SummaryForm(ProfileForm):
    summary: str | None = Field(default=None, max_length=LONG_TEXT)


# --- List sections ----------------------------------------------------------------


class ExperienceForm(ProfileForm):
    title: str = Field(max_length=SHORT_TEXT)
    company: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    start_date: dt.date
    # Before end_date on purpose: validators only see the fields defined above them.
    ongoing: bool = False  # "I currently work here": the end date is ignored
    end_date: dt.date | None = None
    description: str | None = Field(default=None, max_length=LONG_TEXT)
    bullets: list[str] = []

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return parse_month(value)

    @field_validator("end_date")
    @classmethod
    def check_end_date(cls, value, info):
        if info.data.get("ongoing"):
            return None
        check_dates_in_order(info.data.get("start_date"), value)
        return value

    @field_validator("bullets", mode="before")
    @classmethod
    def drop_empty_bullets(cls, value):
        # One form field per bullet; empty boxes are left out.
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]  # a single text is one bullet, not one bullet per character
        return [line.strip() for line in value if isinstance(line, str) and line.strip()]

    @field_validator("bullets")
    @classmethod
    def check_bullets(cls, value):
        if len(value) > MAX_BULLETS:
            raise ValueError(f"Use at most {MAX_BULLETS} bullets.")
        if any(len(line) > BULLET_TEXT for line in value):
            raise ValueError(f"Each bullet can have at most {BULLET_TEXT} characters.")
        return value


class EducationForm(ProfileForm):
    institution: str = Field(max_length=SHORT_TEXT)
    degree: str | None = Field(default=None, max_length=SHORT_TEXT)
    field: str | None = Field(default=None, max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    start_date: dt.date
    ongoing: bool = False  # still studying (before end_date, see ExperienceForm)
    end_date: dt.date | None = None
    description: str | None = Field(default=None, max_length=LONG_TEXT)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def parse_dates(cls, value):
        return parse_month(value)

    @field_validator("end_date")
    @classmethod
    def check_end_date(cls, value, info):
        if info.data.get("ongoing"):
            return None
        check_dates_in_order(info.data.get("start_date"), value)
        return value


class SkillForm(ProfileForm):
    name: str = Field(max_length=SHORT_TEXT)
    category: str | None = Field(default=None, max_length=SHORT_TEXT)
    level: int | None = Field(default=None, ge=1, le=5)


class LanguageForm(ProfileForm):
    language: str = Field(max_length=SHORT_TEXT)
    level: str

    @field_validator("level")
    @classmethod
    def check_level(cls, value):
        if value not in LANGUAGE_LEVELS:
            raise ValueError("Choose a level from the list.")
        return value


class CertificationForm(ProfileForm):
    name: str = Field(max_length=SHORT_TEXT)
    issuer: str | None = Field(default=None, max_length=SHORT_TEXT)
    date: dt.date | None = None
    url: str | None = Field(default=None, max_length=URL_TEXT)

    @field_validator("date", mode="before")
    @classmethod
    def parse_date(cls, value):
        return parse_month(value)

    @field_validator("url")
    @classmethod
    def check_link(cls, value):
        return check_url(value)


class ProjectForm(ProfileForm):
    name: str = Field(max_length=SHORT_TEXT)
    description: str | None = Field(default=None, max_length=LONG_TEXT)
    url: str | None = Field(default=None, max_length=URL_TEXT)

    @field_validator("url")
    @classmethod
    def check_link(cls, value):
        return check_url(value)


# --- Error messages ---------------------------------------------------------------


def form_errors(exc: ValidationError) -> dict[str, str]:
    """{field name: short message} for showing next to the form fields.

    Only the first problem per field is kept.
    """
    errors: dict[str, str] = {}
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else "form"
        errors.setdefault(field, _message(error))
    return errors


def _message(error: dict) -> str:
    kind = error["type"]
    if kind == "missing":
        return "This field is required."
    if kind in ("string_type", "date_type") and error.get("input") is None:
        return "This field is required."  # the field was sent empty
    if kind == "string_too_long":
        return f"Use at most {error['ctx']['max_length']} characters."
    if kind == "value_error":
        # Our own messages, raised as ValueError in the validators above.
        return str(error["ctx"]["error"])
    if kind in ("int_parsing", "int_type", "greater_than_equal", "less_than_equal"):
        return "Choose a level from 1 to 5."
    return "Check this field."
