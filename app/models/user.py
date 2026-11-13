"""User account (SPEC §5.1). Only the columns M1 uses are here (D-025)."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field

# Emails can be up to 320 characters long (64 for the name + @ + 255 for the domain).
EMAIL_MAX_LENGTH = 320


def normalize_email(email: str) -> str:
    """The form an email is stored and looked up in: no spaces around it, lowercase.
    So "Jana@Example.com " and "jana@example.com" are the same account."""
    return email.strip().lower()


class User(SQLModel, table=True):
    # The table is named "user". That is a reserved word in Postgres;
    # SQLAlchemy quotes it automatically, so it works like any other name.
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(max_length=EMAIL_MAX_LENGTH, unique=True, index=True)
    password_hash: str = Field(max_length=255)
    full_name: str = Field(max_length=200)
    is_verified: bool = Field(default=False)
    privacy_accepted_at: datetime = Field(sa_column=datetime_column(nullable=False))
    last_login_at: datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    # Goes up by 1 when the password changes; older sessions stop working (D-021).
    session_version: int = Field(default=0)

    # Stored as plain text, not a database enum (enums are hard to change later).
    # Valid values are the keys of app.billing.plans.PLANS. Code never reads this
    # directly: use app.billing.entitlements (effective_plan, can, limit).
    plan: str = Field(default="free", max_length=20)
    plan_expires_at: datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))

    created_at: datetime = created_at_field()
    updated_at: datetime = updated_at_field()
