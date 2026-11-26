"""Login lockout and requests per IP (SPEC §6.1, D-020, M1b acceptance criteria)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlmodel import Session, select
from starlette.requests import Request

from app.main import app
from app.models import User
from app.models.base import utcnow
from app.models.security_counter import SecurityCounter
from app.security import (
    COUNTER_KEEP,
    EmailLimiter,
    LoginLockout,
    login_lockout,
    purge_security_counters,
)
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
# The counters live in the test's database (D-198); every LoginLockout() here uses it.


@pytest.fixture(autouse=True)
def counters_database(engine):
    return engine


def counter_rows(engine, kind=None) -> list[SecurityCounter]:
    with Session(engine) as session:
        query = select(SecurityCounter)
        if kind:
            query = query.where(SecurityCounter.kind == kind)
        return list(session.exec(query).all())


def test_too_long_email_is_not_tracked(engine):
    lockout = LoginLockout()
    long_email = "a" * 321
    for _ in range(5):
        lockout.record_failure(IP, long_email)
    assert not lockout.is_locked(IP, long_email)
    assert counter_rows(engine) == []


def test_old_rows_of_a_key_go_with_its_next_failure(engine):
    lockout = LoginLockout()
    with freeze_time(utcnow()) as frozen:
        lockout.record_failure(IP, "a@example.com", per_email=False)
        frozen.tick(COUNTER_KEEP + timedelta(minutes=1))
        lockout.record_failure(IP, "a@example.com", per_email=False)
        assert len(counter_rows(engine)) == 1  # without the nightly job too


def test_the_nightly_purge_deletes_rows_nobody_needs(engine):
    lockout = LoginLockout()
    with freeze_time(utcnow()) as frozen:
        lockout.record_failure(IP, "a@example.com")
        frozen.tick(timedelta(minutes=30))
        lockout.record_failure(IP, "b@example.com")
        with Session(engine) as session:
            assert purge_security_counters(session, utcnow() + COUNTER_KEEP) == 2  # a's rows
        assert {row.kind for row in counter_rows(engine)} == {"fail_pair", "fail_email"}
        assert len(counter_rows(engine)) == 2  # b's rows stay


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


def test_reset_empties_every_store(engine):
    lockout = LoginLockout()
    fail_from_many_ips(lockout)
    lockout.reset()
    assert not lockout.is_locked(IP, USER_EMAIL)
    lockout.record_failure(IP, USER_EMAIL, user_id=1)
    lockout.reset()
    assert counter_rows(engine) == []


def test_values_no_account_can_have_are_not_counted(engine):
    lockout = LoginLockout()
    for ip in OTHER_IPS[:20]:
        lockout.record_failure(ip, "")
    assert counter_rows(engine) == []
    assert not lockout.is_locked(IP, "")


# --- In the database (D-198) -----------------------------------------------------------


def test_a_restart_keeps_the_lock():
    fail_from_many_ips(LoginLockout())

    # A new object, like after a restart: the lock is in the database, not in memory.
    assert LoginLockout().is_locked("192.0.2.1", USER_EMAIL)


def test_the_email_limit_survives_a_restart():
    for _ in range(3):
        assert EmailLimiter().allow(USER_EMAIL)
    assert not EmailLimiter().allow(USER_EMAIL)


def test_rows_never_hold_the_ip_or_the_email(engine):
    lockout = LoginLockout()
    lockout.record_failure(IP, USER_EMAIL, user_id=7)
    EmailLimiter().allow(USER_EMAIL)

    rows = counter_rows(engine)
    assert {row.kind for row in rows} == {"fail_pair", "fail_email", "fail_user", "email_sent"}
    for row in rows:
        text = f"{row.key} {row.email_key}"
        assert IP not in text and USER_EMAIL not in text and "7" != row.key
        assert len(row.key) == 64


def test_keys_depend_on_the_secret_key(engine, monkeypatch):
    lockout = LoginLockout()
    lockout.record_failure(IP, USER_EMAIL, per_email=False)
    first = counter_rows(engine)[0].key

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "secret_key", type(get_settings().secret_key)("y" * 40))
    lockout.record_failure(IP, USER_EMAIL, per_email=False)

    assert len({row.key for row in counter_rows(engine)}) == 2 and first
    # Plain sha256 of the values would be guessable; the stored key is not that.
    import hashlib

    assert first != hashlib.sha256(f"{IP}\x1f{USER_EMAIL}".encode()).hexdigest()


def test_a_lock_starts_the_count_again(engine):
    lockout = LoginLockout()
    for _ in range(5):
        lockout.record_failure(IP, USER_EMAIL, per_email=False)

    assert counter_rows(engine, "fail_pair") == []
    assert len(counter_rows(engine, "lock_pair")) == 1


# --- The Settings user lock (security review M1) -------------------------------------------


def fail_settings_from_many_ips(lockout, user_id=1, times=20):
    for i in range(times):
        lockout.record_failure(OTHER_IPS[i // 4], USER_EMAIL, per_email=False, user_id=user_id)


def test_twenty_settings_failures_from_many_ips_lock_that_users_settings_checks():
    lockout = LoginLockout()
    fail_settings_from_many_ips(lockout, times=19)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)

    fail_settings_from_many_ips(lockout, times=1)  # the 20th

    assert lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=2)
    # The sign-in doesn't see it: it is not an email lock.
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL)


def test_failed_logins_do_not_fill_the_settings_user_counter(engine):
    lockout = LoginLockout()
    fail_from_many_ips(lockout)
    assert counter_rows(engine, "fail_user") == [] and counter_rows(engine, "lock_user") == []
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)


def test_the_settings_user_lock_lasts_an_hour():
    lockout = LoginLockout()
    with freeze_time(utcnow()) as frozen:
        fail_settings_from_many_ips(lockout)
        frozen.tick(timedelta(minutes=59, seconds=59))
        assert lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)
        frozen.tick(timedelta(seconds=2))
        assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)


def test_clear_email_with_the_user_id_clears_the_settings_user_lock():
    lockout = LoginLockout()
    fail_settings_from_many_ips(lockout)
    lockout.clear_email(USER_EMAIL)  # without the id the user lock stays
    assert lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)

    lockout.clear_email(USER_EMAIL, 1)
    assert not lockout.is_locked("192.0.2.1", USER_EMAIL, per_email=False, user_id=1)


def settings_check(engine, user, password, ip=IP):
    with Session(engine) as check:
        signed_in = check.get(User, user.id)
        request = Request({"type": "http", "client": (ip, 1234), "headers": []})
        return auth_service.check_password_with_lockout(request, signed_in, password)


def test_a_stolen_session_cannot_guess_from_many_ips(auth_client, engine, user):
    for i in range(20):
        assert settings_check(engine, user, "wrong password!", ip=OTHER_IPS[i // 4]) == "wrong"

    # Even the right password from a fresh IP is refused now.
    assert settings_check(engine, user, USER_PASSWORD, ip="192.0.2.1") == "locked"
    # The owner can still sign in: login doesn't look at the user lock.
    assert log_in(TestClient(app)).status_code == 303


def test_the_settings_page_shows_the_lock_message(auth_client, user):
    fail_settings_from_many_ips(login_lockout, user_id=user.id)

    response = auth_client.post(
        "/settings/account/password",
        data={
            "current_password": USER_PASSWORD,
            "new_password": NEW_PASSWORD,
            "repeat_password": NEW_PASSWORD,
            "csrf_token": csrf_token(auth_client),
        },
    )

    assert "Too many wrong passwords. Try again later." in response.text


def test_a_password_reset_clears_the_settings_user_lock(client, engine, user):
    fail_settings_from_many_ips(login_lockout, user_id=user.id)
    assert reset(client, make_reset_token(user)).status_code == 303

    assert settings_check(engine, user, NEW_PASSWORD) == "ok"


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

        # Any other browser is still locked after 15 minutes.
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


def test_failures_from_the_exempt_browser_still_count_on_the_email(client, user):
    reset(client, make_reset_token(user))
    fail(client, times=4)  # the exemption skips the check, not the counting
    fail_from_many_ips(login_lockout, times=16)

    assert log_in(TestClient(app), password=NEW_PASSWORD).status_code == 429


def test_clear_email_also_forgets_the_emails_sent_to_it(engine):
    # Account deletion calls clear_email: nothing about the address stays (D-198).
    EmailLimiter().allow(USER_EMAIL)
    EmailLimiter().allow("other@example.com")

    LoginLockout().clear_email(USER_EMAIL, user_id=1)

    assert len(counter_rows(engine, "email_sent")) == 1  # only the other address


def test_counters_of_different_scopes_never_share_a_key(engine):
    lockout = LoginLockout()
    lockout.record_failure(IP, "5@example.com", user_id=5)
    keys = [row.key for row in counter_rows(engine)]
    assert len(set(keys)) == len(keys) == 3
