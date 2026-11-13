from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.models.base import as_utc, utcnow
from app.models.user import normalize_email


def make_user(**overrides) -> User:
    values = {
        "email": "jana@example.com",
        "password_hash": "not-a-real-hash",
        "full_name": "Jana Novak",
        "privacy_accepted_at": utcnow(),
    }
    return User(**(values | overrides))


def test_new_user_gets_defaults_and_timestamps(session):
    user = make_user()
    session.add(user)
    session.commit()
    session.refresh(user)

    assert user.id is not None
    assert user.plan == "free"
    assert user.plan_expires_at is None
    assert user.is_verified is False
    assert user.session_version == 0
    assert user.created_at is not None
    assert user.updated_at is not None


def test_updated_at_moves_on_update_and_created_at_stays(session):
    user = make_user()
    session.add(user)
    session.commit()

    # Pretend the row was written a day ago.
    a_day_ago = utcnow() - timedelta(days=1)
    user.created_at = a_day_ago
    user.updated_at = a_day_ago
    session.commit()

    user.full_name = "Jana Nováková"
    session.commit()
    session.refresh(user)

    assert as_utc(user.updated_at) > a_day_ago + timedelta(hours=23)
    assert as_utc(user.created_at) == a_day_ago


def test_email_must_be_unique(session):
    session.add(make_user())
    session.commit()
    session.add(make_user(full_name="Someone else"))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Jana@Example.com", "jana@example.com"),
        ("  jana@example.com \n", "jana@example.com"),
        ("jana@example.com", "jana@example.com"),
    ],
)
def test_normalize_email(raw, normalized):
    assert normalize_email(raw) == normalized


def test_as_utc_treats_naive_datetimes_as_utc():
    naive = datetime(2026, 11, 14, 12, 0)
    assert as_utc(naive) == datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    aware = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
    assert as_utc(aware) is aware
