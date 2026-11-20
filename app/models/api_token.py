"""Tokens for the Chrome extension (SPEC §5.12, D-136).

The extension signs in once and then sends `Authorization: Bearer <token>`. Only the
SHA-256 hash of the token is stored; the token itself is shown once, at sign-in.
A token belongs to one session version of its user: when the password or the email
changes, or the user signs out, the version goes up and every token stops working.
The rules live in app/services/token_service.py.
"""

import datetime as dt

from sqlmodel import Field, SQLModel

from app.models.base import created_at_field, datetime_column, updated_at_field


class ApiToken(SQLModel, table=True):
    __tablename__ = "apitoken"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=100)  # e.g. "Chrome extension"
    token_hash: str = Field(max_length=64, unique=True)  # hex SHA-256
    # User.session_version when the token was made; a different one means signed out.
    session_version: int
    last_used_at: dt.datetime | None = Field(default=None, sa_column=datetime_column(nullable=True))
    expires_at: dt.datetime = Field(sa_column=datetime_column(nullable=False))
    revoked: bool = Field(default=False)

    created_at: dt.datetime = created_at_field()
    updated_at: dt.datetime = updated_at_field()
