"""Login lockout and requests per IP (SPEC §6.1, D-020, M1b acceptance criteria)."""

from datetime import timedelta

from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlmodel import Session
from starlette.requests import Request

from app.main import app
from app.models import User
from app.models.base import utcnow
from app.security import MAX_TRACKED_KEYS, LoginLockout, login_lockout
from app.services import auth_service
from app.services.auth_service import create_user, make_reset_token
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in
from tests.test_password_reset import NEW_PASSWORD, reset

LOCKED = "Too many attempts. Try again later, or reset your password."
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
    assert "a few minutes" in response.text


def test_register_allows_5_requests_per_hour_per_ip(client):
    def register(i):
        form = {"full_name": "", "email": f"r{i}@example.com", "csrf_token": csrf_token(client)}
        return client.post("/register", data=form, follow_redirects=False)

    for i in range(5):
        assert register(i).status_code == 400  # invalid form, but it counts
    response = register(5)
    assert response.status_code == 429
    assert "try again in an hour" in response.text


# --- Per email, from every IP (#57, D-149) -----------------------------------------------

OTHER_IPS = [f"198.51.100.{i}" for i in range(1, 30)]


def fail_from_many_ips(lockout, email=USER_EMAIL, times=20):
    """Up to 4 failures per IP, so no IP + email pair is ever locked on its own."""
    for i in range(times):
        lockout.record_failure(OTHER_IPS[i // 4], email)


def test_twenty_failures_from_many_ips_lock_the_email_everywhere():
    lockout = LoginLockout()
    fail_from_many_ips(lockout, times=19)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL)

    fail_from_many_ips(lockout, times=1)  # the 20th

    assert lockout.is_locked("192.0.2.1", USER_EMAIL)  # a brand-new IP too
    assert lockout.is_locked(OTHER_IPS[0], USER_EMAIL.upper())  # any case
    assert not lockout.is_locked("192.0.2.1", "someone.else@example.com")


def test_the_email_lock_lasts_an_hour_and_old_failures_fall_out():
    lockout = LoginLockout()
    with freeze_time(utcnow()) as frozen:
        fail_from_many_ips(lockout, times=19)
        frozen.tick(timedelta(hours=1, seconds=1))  # the 19 are older than the window
        fail_from_many_ips(lockout, times=1)
        assert not lockout.is_locked(IP, USER_EMAIL)

        fail_from_many_ips(lockout, times=19)  # 20 within the hour
        assert lockout.is_locked(IP, USER_EMAIL)
        frozen.tick(timedelta(minutes=59, seconds=59))
        assert lockout.is_locked(IP, USER_EMAIL)
        frozen.tick(timedelta(seconds=2))
        assert not lockout.is_locked(IP, USER_EMAIL)


def test_settings_checks_neither_count_nor_see_the_email_lock():
    lockout = LoginLockout()
    for ip in OTHER_IPS[:20]:
        lockout.record_failure(ip, USER_EMAIL, per_email=False)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL)

    fail_from_many_ips(lockout)
    assert lockout.is_locked("192.0.2.1", USER_EMAIL)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False)


def test_a_successful_login_keeps_the_email_counter_a_reset_clears_it():
    lockout = LoginLockout()
    fail_from_many_ips(lockout, times=19)
    lockout.clear("192.0.2.1", USER_EMAIL)  # the owner signs in elsewhere
    fail_from_many_ips(lockout, times=1)
    assert lockout.is_locked("192.0.2.1", USER_EMAIL)

    lockout.clear_email(USER_EMAIL)  # a password reset
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL)


def test_reset_empties_both_stores():
    lockout = LoginLockout()
    fail_from_many_ips(lockout)
    lockout.reset()
    assert not lockout.is_locked(IP, USER_EMAIL)
    assert not lockout._entries and not lockout._by_email


def test_the_email_store_keeps_at_most_max_tracked_keys():
    lockout = LoginLockout()
    for i in range(MAX_TRACKED_KEYS + 5):
        lockout.record_failure(f"10.0.{i // 250}.{i % 250}", f"user{i}@example.com")

    assert len(lockout._by_email) == MAX_TRACKED_KEYS
    assert "user0@example.com" not in lockout._by_email


def test_values_no_account_can_have_are_not_counted():
    lockout = LoginLockout()
    for ip in OTHER_IPS[:20]:
        lockout.record_failure(ip, "")
    assert not lockout._by_email


# --- The email lock through the routes ------------------------------------------------------


def lock_email():
    fail_from_many_ips(login_lockout)


def test_a_locked_email_refuses_the_right_password_on_the_web(client, user):
    lock_email()

    response = log_in(client)

    assert response.status_code == 429
    assert LOCKED in response.text


def test_a_locked_email_refuses_the_extension_sign_in(client, user):
    lock_email()

    response = client.post(
        "/api/ext/auth/login",
        json={"email": USER_EMAIL, "password": USER_PASSWORD},
        headers={"Origin": "chrome-extension://testextensionid"},
    )

    assert response.status_code == 429
    assert response.json()["error"]["message"] == LOCKED


def test_settings_checks_ignore_the_email_lock(auth_client, engine, user):
    lock_email()
    with Session(engine) as check:
        signed_in = check.get(User, user.id)
        request = Request({"type": "http", "client": (IP, 1234), "headers": []})
        result = auth_service.check_password_with_lockout(request, signed_in, USER_PASSWORD)
        assert result == "ok"


def test_after_a_reset_this_browser_gets_past_the_email_lock_for_15_minutes(client, user):
    other_browser = TestClient(app)
    with freeze_time(utcnow()) as frozen:
        lock_email()
        assert reset(client, make_reset_token(user)).status_code == 303
        lock_email()  # whoever locked it does it again at once

        assert log_in(other_browser, password=NEW_PASSWORD).status_code == 429
        response = log_in(client, password=NEW_PASSWORD)
        assert response.status_code == 303

        # The exemption ends with the sign-in, and after 15 minutes anyway.
        frozen.tick(timedelta(minutes=16))
        assert log_in(TestClient(app), password=NEW_PASSWORD).status_code == 429


def test_the_exemption_is_only_for_the_reset_email(client, user, session):
    create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    reset(client, make_reset_token(user))
    fail_from_many_ips(login_lockout, email="other@example.com")

    response = log_in(client, email="other@example.com", password="x" * 12)

    assert response.status_code == 429


def test_the_exemption_ends_after_15_minutes(client, user):
    with freeze_time(utcnow()) as frozen:
        reset(client, make_reset_token(user))
        frozen.tick(timedelta(minutes=15, seconds=1))
        lock_email()

        assert log_in(client, password=NEW_PASSWORD).status_code == 429
