"""Login lockout and requests per IP (SPEC §6.1, D-020, M1b acceptance criteria)."""

from datetime import timedelta

from freezegun import freeze_time

from app.models.base import utcnow
from app.security import MAX_TRACKED_KEYS, LoginLockout
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

LOCKED = "Too many attempts. Try again in 15 minutes."
IP = "203.0.113.7"


def fail(client, email=USER_EMAIL, times=1):
    for _ in range(times):
        response = log_in(client, email=email, password="wrong password!")
    return response


# --- Through the login form -------------------------------------------------------------


def test_sixth_attempt_after_five_failures_is_locked(client, user):
    assert fail(client, times=5).status_code == 400  # the 5th still says "invalid"

    response = log_in(client)  # the right password
    assert response.status_code == 429
    assert LOCKED in response.text
    assert log_in(client).status_code == 429


def test_lock_ends_after_15_minutes(client, user):
    with freeze_time(utcnow()) as frozen:
        fail(client, times=5)
        frozen.tick(timedelta(minutes=14, seconds=59))
        assert log_in(client).status_code == 429
        frozen.tick(timedelta(seconds=2))
        assert log_in(client).status_code == 303


def test_lock_lasts_15_minutes_even_when_failures_are_spread_out(client, user):
    # Failures at minute 0, 1, 2 and 3, the 5th at minute 14: locked until minute 29,
    # although the first failure leaves the 15-minute window at minute 15.
    with freeze_time(utcnow()) as frozen:
        for minute_gap in (0, 1, 1, 1, 11):
            frozen.tick(timedelta(minutes=minute_gap))
            fail(client)
        frozen.tick(timedelta(minutes=2))  # minute 16
        assert log_in(client).status_code == 429
        frozen.tick(timedelta(minutes=12, seconds=59))  # just before minute 29
        assert log_in(client).status_code == 429
        frozen.tick(timedelta(seconds=2))
        assert log_in(client).status_code == 303


def test_failures_older_than_15_minutes_do_not_count(client, user):
    with freeze_time(utcnow()) as frozen:
        fail(client, times=4)
        frozen.tick(timedelta(minutes=15))
        fail(client)  # only 1 in the window now
        assert log_in(client).status_code == 303


def test_other_emails_are_not_affected(client, user, session):
    from app.services.auth_service import create_user

    create_user(session, email="other@example.com", password=USER_PASSWORD, full_name="Other")
    fail(client, times=5)

    assert log_in(client, email="other@example.com").status_code == 303


def test_email_case_does_not_get_around_the_lock(client, user):
    fail(client, email=USER_EMAIL.upper(), times=5)
    assert log_in(client).status_code == 429


def test_successful_login_resets_the_count(client, user):
    fail(client, times=4)
    assert log_in(client).status_code == 303
    client.post("/logout", data={"csrf_token": csrf_token(client)})

    fail(client, times=4)
    assert log_in(client).status_code == 303


# --- LoginLockout itself ----------------------------------------------------------------


def test_too_long_email_is_not_tracked():
    lockout = LoginLockout()
    long_email = "a" * 321
    for _ in range(5):
        lockout.record_failure(IP, long_email)
    assert not lockout.is_locked(IP, long_email)
    assert len(lockout._entries) == 0


def test_store_keeps_at_most_max_tracked_keys():
    lockout = LoginLockout()
    for i in range(MAX_TRACKED_KEYS + 5):
        lockout.record_failure(IP, f"user{i}@example.com")

    assert len(lockout._entries) == MAX_TRACKED_KEYS
    # The oldest keys were dropped, the newest are kept.
    assert (IP, "user0@example.com") not in lockout._entries
    assert (IP, f"user{MAX_TRACKED_KEYS + 4}@example.com") in lockout._entries


def test_expired_entries_are_removed():
    lockout = LoginLockout()
    with freeze_time(utcnow()) as frozen:
        lockout.record_failure(IP, "a@example.com")
        frozen.tick(timedelta(minutes=16))
        lockout.record_failure(IP, "b@example.com")
    assert list(lockout._entries) == [(IP, "b@example.com")]


def test_clear_email_unlocks_every_ip():
    lockout = LoginLockout()
    for ip in ("198.51.100.1", "198.51.100.2"):
        for _ in range(5):
            lockout.record_failure(ip, USER_EMAIL)
    lockout.clear_email(USER_EMAIL.upper())

    assert not lockout.is_locked("198.51.100.1", USER_EMAIL)
    assert not lockout.is_locked("198.51.100.2", USER_EMAIL)


# --- Requests per IP (slowapi) ----------------------------------------------------------


def test_login_allows_10_requests_per_minute_per_ip(client):
    # Different emails, so the lockout above doesn't kick in first.
    for i in range(10):
        assert fail(client, email=f"user{i}@example.com").status_code == 400
    response = fail(client, email="user10@example.com")
    assert response.status_code == 429
    assert "Too many requests" in response.text


def test_register_allows_5_requests_per_hour_per_ip(client):
    def register(i):
        form = {"full_name": "", "email": f"r{i}@example.com", "csrf_token": csrf_token(client)}
        return client.post("/register", data=form, follow_redirects=False)

    for i in range(5):
        assert register(i).status_code == 400  # invalid form, but it counts
    assert register(5).status_code == 429
