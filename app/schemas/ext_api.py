"""JSON bodies of the extension API (SPEC §8, D-137, D-141).

Unknown fields are ignored (Pydantic's default): the extension also sends `site`,
but the source always comes from the link (D-141).
"""

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.models.application import JOB_URL_TEXT, MAX_DESCRIPTION, REMOTE_TYPES
from app.models.profile import SHORT_TEXT
from app.models.user import EMAIL_MAX_LENGTH
from app.schemas.application import ADD_JOB_STATUSES, check_date_range, is_link
from app.schemas.profile import ProfileForm, check_url
from app.services.auth_service import PASSWORD_MAX_LENGTH


class LoginIn(BaseModel):
    # A little room above the real limits: authenticate() answers "invalid" for
    # longer values, like the login form, instead of a different error.
    email: str = Field(max_length=EMAIL_MAX_LENGTH + 100)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH + 100)


class CheckIn(BaseModel):
    url: str = Field(max_length=JOB_URL_TEXT)


class JobIn(ProfileForm):
    """The preview form in the popup. ProfileForm strips spaces and turns empty
    fields into None."""

    company: str = Field(max_length=SHORT_TEXT)
    job_title: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    remote_type: str | None = None
    salary_text: str | None = Field(default=None, max_length=SHORT_TEXT)
    posting_closes_at: dt.date | None = None
    status: str | None = None
    url: str = Field(max_length=JOB_URL_TEXT)
    job_text: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @field_validator("remote_type")
    @classmethod
    def check_remote_type(cls, value):
        value = value or "unknown"
        if value not in REMOTE_TYPES:
            raise ValueError("Choose a work mode from the list.")
        return value

    @field_validator("status")
    @classmethod
    def check_status(cls, value):
        # Like "Add job" on the web: the other columns have their own steps
        # (interview modal, locking the CV), so a new job starts in one of these two.
        value = value or "saved"
        if value not in ADD_JOB_STATUSES:
            raise ValueError("Choose Saved or Applied.")
        return value

    @field_validator("posting_closes_at")
    @classmethod
    def check_closing_date(cls, value):
        return check_date_range(value)

    @field_validator("url")
    @classmethod
    def check_job_url(cls, value):
        if not is_link(value):
            raise ValueError("Enter a full link starting with https://.")
        return check_url(value)
