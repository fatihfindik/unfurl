"""Form for adding a job by hand (SPEC §6.7 "Add job", D-062)."""

from pydantic import Field, field_validator

from app.models.application import JOB_URL_TEXT, MAX_DESCRIPTION, REMOTE_TYPES
from app.models.profile import SHORT_TEXT
from app.schemas.profile import ProfileForm, check_url

# A new job starts in one of these two columns (the prototype's Status field).
ADD_JOB_STATUSES = ("saved", "applied")


class AddJobForm(ProfileForm):
    """ProfileForm strips spaces and turns empty fields into None."""

    company: str = Field(max_length=SHORT_TEXT)
    job_title: str = Field(max_length=SHORT_TEXT)
    location: str | None = Field(default=None, max_length=SHORT_TEXT)
    remote_type: str = "unknown"
    status: str = "saved"
    # One field for "Job link or posting text": a link or the text, see is_link.
    posting: str | None = Field(default=None, max_length=MAX_DESCRIPTION)

    @field_validator("remote_type", mode="before")
    @classmethod
    def check_remote_type(cls, value):
        value = (value or "").strip() or "unknown"  # "Not sure" is sent as ""
        if value not in REMOTE_TYPES:
            raise ValueError("Choose a work mode from the list.")
        return value

    @field_validator("status", mode="before")
    @classmethod
    def check_status(cls, value):
        if value not in ADD_JOB_STATUSES:
            raise ValueError("Choose Saved or Applied.")
        return value

    @field_validator("posting")
    @classmethod
    def check_link(cls, value):
        if value and is_link(value):
            if len(value) > JOB_URL_TEXT:
                raise ValueError(f"Use a link of at most {JOB_URL_TEXT} characters.")
            check_url(value)
        return value

    # The two values the service stores. They are worked out here from `posting`,
    # never read from the request.
    @property
    def job_url(self) -> str | None:
        return self.posting if self.posting and is_link(self.posting) else None

    @property
    def description_raw(self) -> str:
        return "" if self.job_url else (self.posting or "")


def is_link(text: str) -> bool:
    """True for a single link: starts with http:// or https:// and has no spaces.
    Anything else in the field is the posting text."""
    return text.startswith(("http://", "https://")) and not any(c.isspace() for c in text)
