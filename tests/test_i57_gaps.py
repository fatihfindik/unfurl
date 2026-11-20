"""Gaps around the per-email login lock (#57, D-149, SPEC §6.1).

The TestClient always comes from one IP ("testclient") and /login allows 10 requests a
minute, so most locks are set up with record_failure from other IPs, like in
tests/test_login_lockout.py.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

from app.main import app
from app.models.base import utcnow
from app.security import login_lockout
from app.services.auth_service import make_reset_token
from tests.helpers import USER_EMAIL, csrf_token, log_in
from tests.test_account_delete import delete
from tests.test_email_change import ask
from tests.test_ext_api import api_login
from tests.test_login_lockout import LOCKED, OTHER_IPS
from tests.test_password_reset import NEW_PASSWORD, reset
from tests.test_settings_account import change, toast

UNKNOWN_EMAIL = "nobody@example.com"
WRONG = "wrong password!"


def fail_from_other_ips(email=USER_EMAIL, times=20):
    """Up to 4 failures per IP, so no IP + email pair is locked on its own."""
    for i in range(times):
        login_lockout.record_failure(OTHER_IPS[i // 4], email)


def email_locked(email=USER_EMAIL) -> bool:
    # A fresh IP: only the per-email lock can answer True here.
    return login_lockout.is_locked("192.0.2.200", email)


# --- Every way in counts toward the same email counter ------------------------------------


@pytest.mark.parametrize("email", [USER_EMAIL, UNKNOWN_EMAIL])
def test_web_failures_count_toward_the_email_lock(client, user, email):
    for _ in range(4):
        assert log_in(client, email=email, password=WRONG).status_code == 400
    fail_from_other_ips(email, times=15)
    assert not email_locked(email)  # 19

    fail_from_other_ips(email, times=1)  # the 20th

    assert email_locked(email)


def test_extension_failures_count_toward_the_email_lock(client, user):
    for _ in range(4):
        assert api_login(client, password=WRONG).status_code == 401
    fail_from_other_ips(times=16)

    assert email_locked()
    assert log_in(TestClient(app)).status_code == 429


@pytest.mark.parametrize("spelling", ["  JANA@Example.com ", "Jana@EXAMPLE.COM"])
def test_other_spellings_share_the_email_counter(client, user, spelling):
    for _ in range(4):
        log_in(client, email=spelling, password=WRONG)
    fail_from_other_ips(times=16)

    assert email_locked()


@pytest.mark.parametrize("spelling", ["  JANA@Example.com ", "Jana@EXAMPLE.COM"])
def test_other_spellings_do_not_get_past_the_email_lock(client, user, spelling):
    fail_from_other_ips()

    response = log_in(client, email=spelling)

    assert response.status_code == 429
    assert LOCKED in response.text


def test_other_spellings_do_not_get_past_the_lock_in_the_extension(client, user):
    fail_from_other_ips()

    response = api_login(client, email=USER_EMAIL.upper())

    assert response.status_code == 429
    assert response.json()["error"]["message"] == LOCKED


# --- No account guessing -------------------------------------------------------------------


def test_a_locked_unknown_email_looks_like_a_locked_account(client, user):
    fail_from_other_ips(USER_EMAIL)
    fail_from_other_ips(UNKNOWN_EMAIL)

    known = log_in(client, email=USER_EMAIL, password=WRONG)
    unknown = log_in(client, email=UNKNOWN_EMAIL, password=WRONG)

    assert known.status_code == unknown.status_code == 429
    assert LOCKED in known.text and LOCKED in unknown.text
    # The same page apart from the email typed into the field.
    assert known.text.replace(USER_EMAIL, "X") == unknown.text.replace(UNKNOWN_EMAIL, "X")


def test_a_locked_unknown_email_looks_the_same_in_the_extension(client, user):
    fail_from_other_ips(USER_EMAIL)
    fail_from_other_ips(UNKNOWN_EMAIL)

    known = api_login(client, email=USER_EMAIL)
    unknown = api_login(client, email=UNKNOWN_EMAIL)

    assert known.status_code == unknown.status_code == 429
    assert known.json() == unknown.json()


# --- Time -------------------------------------------------------------------------------------


def test_the_web_sign_in_works_again_after_an_hour(client, user):
    with freeze_time(utcnow()) as frozen:
        fail_from_other_ips()
        frozen.tick(timedelta(minutes=59, seconds=59))
        assert log_in(client).status_code == 429
        frozen.tick(timedelta(seconds=2))
        assert log_in(client).status_code == 303


def test_the_extension_sign_in_works_again_after_an_hour(client, user):
    with freeze_time(utcnow()) as frozen:
        fail_from_other_ips()
        assert api_login(client).status_code == 429
        frozen.tick(timedelta(hours=1, seconds=1))
        assert api_login(client).status_code == 200


# --- Password reset ----------------------------------------------------------------------------


def test_a_reset_unlocks_the_email_for_every_browser(client, user):
    fail_from_other_ips()

    assert reset(client, make_reset_token(user)).status_code == 303

    assert not email_locked()
    assert log_in(TestClient(app), password=NEW_PASSWORD).status_code == 303
    assert api_login(TestClient(app), password=NEW_PASSWORD).status_code == 200


def test_the_exemption_accepts_other_spellings_of_the_email(client, user):
    reset(client, make_reset_token(user))
    fail_from_other_ips()

    assert log_in(client, email="  JANA@Example.com ", password=NEW_PASSWORD).status_code == 303


def test_the_exemption_ends_with_the_sign_in(client, user):
    reset(client, make_reset_token(user))
    assert log_in(client, password=NEW_PASSWORD).status_code == 303
    client.post("/logout", data={"csrf_token": csrf_token(client)})
    fail_from_other_ips()  # locked again, still within the 15 minutes

    assert log_in(client, password=NEW_PASSWORD).status_code == 429


def test_the_exemption_does_not_lift_the_ip_and_email_lock(client, user):
    reset(client, make_reset_token(user))
    fail_from_other_ips()
    for _ in range(5):
        assert log_in(client, password=WRONG).status_code == 400

    response = log_in(client, password=NEW_PASSWORD)

    assert response.status_code == 429
    assert LOCKED in response.text


def test_the_extension_gets_no_exemption_from_the_reset_browser(client, user):
    # The extension has no session: even with the reset browser's cookies it is locked.
    reset(client, make_reset_token(user))
    fail_from_other_ips()

    response = api_login(client, password=NEW_PASSWORD)

    assert response.status_code == 429


# --- Settings password checks: only the IP + email lock -------------------------------------


def test_settings_password_change_works_while_the_email_is_locked(auth_client, user):
    fail_from_other_ips()

    response = change(auth_client)

    assert "Password changed" in toast(response)


def test_settings_email_change_works_while_the_email_is_locked(auth_client, user, outbox):
    fail_from_other_ips()

    response = ask(auth_client)

    assert "Too many" not in response.text
    assert "not your current password" not in response.text
    assert len(outbox) >= 1  # the confirmation link went out


def test_account_deletion_works_while_the_email_is_locked(auth_client, user):
    fail_from_other_ips()

    response = delete(auth_client)

    assert response.headers.get("HX-Redirect") == "/login?deleted=1"


@pytest.mark.parametrize(
    "wrong_check",
    [
        lambda c: change(c, current=WRONG),
        lambda c: ask(c, password=WRONG),
        lambda c: delete(c, password=WRONG),
    ],
    ids=["password", "email", "delete"],
)
def test_wrong_settings_passwords_do_not_count_toward_the_email_lock(
    auth_client, user, wrong_check
):
    for _ in range(4):
        wrong_check(auth_client)
    fail_from_other_ips(times=16)  # 20 if the settings checks counted

    assert not email_locked()
    assert log_in(TestClient(app)).status_code == 303
