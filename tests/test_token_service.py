"""Extension tokens (SPEC §5.12, D-136): made once, stored as a hash, and stopped by
revoking, expiry or a new session version."""

import datetime as dt

from freezegun import freeze_time
from sqlmodel import select

from app.models import ApiToken, User
from app.models.base import as_utc
from app.services import auth_service, token_service
from app.services.auth_service import create_user


def test_a_new_token_is_shown_once_and_stored_as_a_hash(session, user):
    raw, token = token_service.issue(session, user)

    assert len(raw) >= 40
    assert token.token_hash == token_service.hash_token(raw) != raw
    assert (token.user_id, token.name, token.revoked) == (user.id, "Chrome extension", False)
    assert token.session_version == user.session_version
    assert as_utc(token.expires_at) - as_utc(token.created_at) >= dt.timedelta(days=89)
    # The raw token is nowhere in the database.
    stored = session.exec(select(ApiToken)).all()
    assert all(raw not in (row.token_hash, row.name) for row in stored)


def test_a_token_gives_its_user(session, user):
    raw, token = token_service.issue(session, user)

    assert token_service.check(session, raw) == (user, token)


def test_unknown_empty_and_overlong_tokens_give_nothing(session, user):
    token_service.issue(session, user)

    for raw in (None, "", "not-a-token", "x" * 5000):
        assert token_service.check(session, raw) is None


def test_a_revoked_token_stops_working(session, user):
    raw, token = token_service.issue(session, user)

    token_service.revoke(session, token)

    assert token_service.check(session, raw) is None


def test_a_token_expires_after_90_days(session, user):
    with freeze_time("2026-11-18 12:00:00"):
        raw, _ = token_service.issue(session, user)
    with freeze_time("2027-02-15 11:59:00"):  # 89 days later
        assert token_service.check(session, raw) is not None
    with freeze_time("2027-02-16 12:00:01"):  # 90 days later
        assert token_service.check(session, raw) is None


def test_a_new_session_version_stops_every_token(session, user):
    # Sign out, password change and email change all raise the version (D-136).
    raw, _ = token_service.issue(session, user)

    user.session_version += 1
    session.add(user)
    session.commit()

    assert token_service.check(session, raw) is None


def test_a_password_reset_stops_the_token(session, user):
    raw, _ = token_service.issue(session, user)

    assert auth_service.reset_password(session, user, "another-long-password")

    assert token_service.check(session, raw) is None


def test_an_email_change_stops_the_token(session, user):
    raw, _ = token_service.issue(session, user)

    assert auth_service.change_email(session, user, "new@example.com") == "ok"

    assert token_service.check(session, raw) is None


def test_last_used_is_written_at_most_every_five_minutes(session, user):
    with freeze_time("2026-11-18 12:00:00"):
        raw, token = token_service.issue(session, user)
        assert token.last_used_at is None
        token_service.check(session, raw)
    assert as_utc(token.last_used_at) == dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)

    with freeze_time("2026-11-18 12:04:00"):
        token_service.check(session, raw)
    assert as_utc(token.last_used_at) == dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)

    with freeze_time("2026-11-18 12:05:00"):
        token_service.check(session, raw)
    assert as_utc(token.last_used_at) == dt.datetime(2026, 11, 18, 12, 5, tzinfo=dt.UTC)


def test_signing_in_deletes_dead_tokens_of_that_user_only(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    # Ids are kept right away: a later issue() already deletes the dead ones.
    with freeze_time("2026-01-01"):
        expired_id = token_service.issue(session, user)[1].id
    _, revoked = token_service.issue(session, user)
    revoked_id = revoked.id
    token_service.revoke(session, revoked)
    old_id = token_service.issue(session, user)[1].id
    _, others = token_service.issue(session, other)
    others_id = others.id
    token_service.revoke(session, others)
    user.session_version += 1
    session.add(user)
    session.commit()

    _, new = token_service.issue(session, user)

    ids = set(session.exec(select(ApiToken.id)).all())
    assert {expired_id, revoked_id, old_id}.isdisjoint(ids)
    assert {others_id, new.id} <= ids


def test_at_most_ten_tokens_work_and_the_oldest_is_revoked(session, user):
    raws = []
    for minute in range(11):
        with freeze_time(f"2026-11-18 12:{minute:02d}:00"):
            raws.append(token_service.issue(session, user)[0])

    assert token_service.check(session, raws[0]) is None
    assert all(token_service.check(session, raw) is not None for raw in raws[1:])
    assert len(token_service.active_tokens(session, user)) == 10


def test_deleting_the_user_deletes_the_tokens(session, user):
    token_service.issue(session, user)

    session.delete(session.get(User, user.id))
    session.commit()

    assert session.exec(select(ApiToken)).all() == []
