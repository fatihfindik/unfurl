"""The cover letter forms (SPEC §6.6, D-152): new letter and the editor.

Every field is cleaned like the profile forms (spaces around text removed, an empty
field is "not filled in"). The lengths match the columns in app/models/cover_letter.py.
"""

from pydantic import Field, field_validator

from app.models.cover_letter import ADDRESS_TEXT, LETTER_BODY_TEXT
from app.models.cv import CV_LANGUAGES
from app.models.profile import SHORT_TEXT
from app.schemas.profile import ProfileForm, check_email_address


class NewCoverLetterForm(ProfileForm):
    title: str = Field(max_length=SHORT_TEXT)
    language: str

    @field_validator("language")
    @classmethod
    def known_language(cls, value: str) -> str:
        if value not in CV_LANGUAGES:
            raise ValueError("Choose a language.")
        return value


# The editor's fields, in form order. All of them are saved together.
LETTER_FIELDS = (
    "title",
    "sender_name",
    "sender_email",
    "sender_phone",
    "sender_address",
    "recipient_company",
    "recipient_name",
    "recipient_address",
    "place",
    "subject",
    "salutation",
    "body",
    "closing",
)
# Columns that are never NULL: "not filled in" is saved as "".
TEXT_NOT_NULL = ("subject", "salutation", "body", "closing")


class CoverLetterForm(ProfileForm):
    title: str = Field(max_length=SHORT_TEXT)
    sender_name: str | None = Field(default=None, max_length=SHORT_TEXT)
    sender_email: str | None = Field(default=None, max_length=320)
    sender_phone: str | None = Field(default=None, max_length=SHORT_TEXT)
    sender_address: str | None = Field(default=None, max_length=ADDRESS_TEXT)
    recipient_company: str | None = Field(default=None, max_length=SHORT_TEXT)
    recipient_name: str | None = Field(default=None, max_length=SHORT_TEXT)
    recipient_address: str | None = Field(default=None, max_length=ADDRESS_TEXT)
    place: str | None = Field(default=None, max_length=SHORT_TEXT)
    subject: str | None = Field(default=None, max_length=SHORT_TEXT)
    salutation: str | None = Field(default=None, max_length=SHORT_TEXT)
    body: str | None = Field(default=None, max_length=LETTER_BODY_TEXT)
    closing: str | None = Field(default=None, max_length=SHORT_TEXT)

    @field_validator("sender_address", "recipient_address", "body", mode="before")
    @classmethod
    def one_kind_of_line_break(cls, value):
        # Browsers send line breaks in a textarea as \r\n; stored as \n, so the
        # length check and the preview count each break once.
        if isinstance(value, str):
            return value.replace("\r\n", "\n").replace("\r", "\n")
        return value

    @field_validator("sender_email")
    @classmethod
    def check_email(cls, value):
        return check_email_address(value)

    def columns(self) -> dict:
        """The values as the model stores them ("" instead of None where required)."""
        values = self.model_dump()
        for name in TEXT_NOT_NULL:
            values[name] = values[name] or ""
        return values
