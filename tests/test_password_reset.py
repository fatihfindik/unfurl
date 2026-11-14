"""Forgot / reset password (SPEC §6.1, M1b acceptance criteria)."""

import re
from datetime import timedelta

import pytest
from freezegun import freeze_time

from app.models.base import utcnow
from app.services.auth_service import PASSWORD_MAX_LENGTH, make_reset_token
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

NEW_PASSWORD = "a brand new password"
FORGOT_MESSAGE = "If an account exists, we sent an email."


def forgot(client, email=USER_EMAIL):
    return client.post(
        "/forgot-password",
        data={"email": email, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def reset(client, token, password=NEW_PASSWORD):
    return client.post(
        "/reset-password",
        data={"token": token, "password": password, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def token_from(email) -> str:
    return re.search(r"/reset-password\?token=(\S+)", email.body).group(1)


# --- Forgot password --------------------------------------------------------------------


def test_forgot_password_page(client):
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert 'action="/forgot-password"' in response.text
    assert 'name="csrf_token"' in response.text


def test_known_email_gets_a_reset_link(client, user, outbox):
    response = forgot(client)

    assert response.status_code == 200
    assert FORGOT_MESSAGE in response.text
    assert len(outbox) == 1
    assert outbox[0].to == USER_EMAIL
    assert outbox[0].subject == "Reset your Unfurl password"
    assert re.search(r"https?://\S+/reset-password\?token=\S+", outbox[0].body)


@pytest.mark.parametrize(
    "email", ["nobody@example.com", "not-an-email", "a" * 400 + "@example.com"]
)
def test_unknown_email_gets_the_same_answer_and_no_email(client, user, outbox, email):
    known = forgot(client)
    unknown = forgot(client, email=email)

    assert unknown.status_code == known.status_code == 200
    assert FORGOT_MESSAGE in unknown.text
    assert len(outbox) == 1  # only the one for the known email


def test_email_is_matched_case_insensitively(client, user, outbox):
    forgot(client, email="  " + USER_EMAIL.upper())
    assert len(outbox) == 1


def test_empty_email_asks_for_one(client, outbox):
    response = forgot(client, email="  ")
    assert response.status_code == 400
    assert "Enter your email address." in response.text
    assert outbox == []


# --- Reset password ---------------------------------------------------------------------


def test_reset_link_shows_the_form(client, user):
    response = client.get("/reset-password?token=" + make_reset_token(user))

    assert response.status_code == 200
    assert "Choose a new password" in response.text
    assert 'name="token"' in response.text
    assert response.headers["referrer-policy"] == "no-referrer"


def test_full_reset_flow(client, user, outbox):
    forgot(client)
    token = token_from(outbox[0])
    response = reset(client, token)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?reset=1"
    page = client.get("/login?reset=1").text
    assert "Your password was changed. Sign in with your new password." in page

    assert log_in(client, password=USER_PASSWORD).status_code == 400
    assert log_in(client, password=NEW_PASSWORD).status_code == 303


def test_reset_marks_the_email_verified(client, session, user):
    assert not user.is_verified
    reset(client, make_reset_token(user))
    session.refresh(user)
    assert user.is_verified


def test_used_link_is_rejected(client, user):
    token = make_reset_token(user)
    assert reset(client, token).status_code == 303

    second = reset(client, token, password="yet another password")
    assert second.status_code == 400
    assert "This link has expired or is invalid" in second.text
    assert client.get("/reset-password?token=" + token).status_code == 400
    assert log_in(client, password=NEW_PASSWORD).status_code == 303


def test_link_works_until_1_hour(client, user):
    with freeze_time(utcnow()) as frozen:
        token = make_reset_token(user)
        frozen.tick(timedelta(hours=1) - timedelta(seconds=1))
        assert reset(client, token).status_code == 303


def test_expired_link_is_rejected(client, user):
    with freeze_time(utcnow()) as frozen:
        token = make_reset_token(user)
        frozen.tick(timedelta(hours=1, seconds=1))
        response = client.get("/reset-password?token=" + token)
        assert response.status_code == 400
        assert reset(client, token).status_code == 400
    assert '<a href="/forgot-password"' in response.text
    assert log_in(client).status_code == 303  # old password still works


def test_old_session_is_logged_out_after_reset(auth_client, user):
    # The reset happens in a second browser; the first one's session must end too.
    from fastapi.testclient import TestClient

    from app.main import app

    other_browser = TestClient(app)
    reset(other_browser, make_reset_token(user))

    response = auth_client.get("/settings", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/settings"


def test_reset_logs_out_the_browser_that_did_it(auth_client, user):
    reset(auth_client, make_reset_token(user))
    response = auth_client.get("/", follow_redirects=False)
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("too short", "Password must be at least 10 characters."),
        ("x" * (PASSWORD_MAX_LENGTH + 1), "Password must be at most 1024 characters."),
    ],
)
def test_reset_rejects_invalid_passwords(client, user, password, message):
    token = make_reset_token(user)
    response = reset(client, token, password=password)

    assert response.status_code == 400
    assert message in response.text
    # The token was not used up: the form still works with a good password.
    assert reset(client, token).status_code == 303


@pytest.mark.parametrize("query", ["", "?token=", "?token=broken", "?token=" + "a" * 513], ids=str)
def test_missing_or_broken_token_is_rejected(client, query):
    response = client.get("/reset-password" + query)
    assert response.status_code == 400
    assert "This link has expired or is invalid" in response.text
    assert response.headers["referrer-policy"] == "no-referrer"


def test_verify_token_is_not_a_reset_token(client, user):
    from app.services.auth_service import make_verify_token

    assert reset(client, make_verify_token(user)).status_code == 400


def test_reset_needs_csrf(client, user):
    response = client.post(
        "/reset-password",
        data={"token": make_reset_token(user), "password": NEW_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 403
