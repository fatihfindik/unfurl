"""Shared helpers for model fields.

Every table has `created_at` and `updated_at` (SPEC §5). Both are filled by
SQLAlchemy on the application side (D-025), so code never sets them by hand.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field


def utcnow() -> datetime:
    """The current time in UTC, timezone-aware."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Make a datetime from the database timezone-aware.

    SQLite does not store the timezone, so it gives back "naive" datetimes.
    Everything we store is UTC, so a naive value is read as UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def datetime_column(*, nullable: bool, onupdate=None) -> Column:
    """A timezone-aware datetime column. Each call builds a new Column,
    because SQLAlchemy can't share one Column object between tables."""
    return Column(DateTime(timezone=True), nullable=nullable, onupdate=onupdate)


def created_at_field():
    # Set once, when the row is created.
    return Field(default_factory=utcnow, sa_column=datetime_column(nullable=False))


def updated_at_field():
    # Set when the row is created, and again by SQLAlchemy on every UPDATE
    # (onupdate). Raw SQL run outside SQLAlchemy does not change it.
    return Field(default_factory=utcnow, sa_column=datetime_column(nullable=False, onupdate=utcnow))
