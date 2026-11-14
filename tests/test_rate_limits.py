"""Emails per address and the forgot-password limit (SPEC §6.1, D-020)."""

from app.services.auth_service import make_reset_token
from tests.helpers import USER_EMAIL, csrf_token, log_in

FORGOT_MESSAGE = "If an account exists, we sent an email."
WAIT_MESSAGE = "Please wait a few minutes before asking for another email."


def resend(client, htmx=False):
    return client.post(
        "/resend-verification",
        data={"csrf_token": csrf_token(client)},
        headers={"HX-Request": "true"} if htmx else {},
        follow_redirects=False,
    )


def forgot(client, email=USER_EMAIL):
    return client.post(
        "/forgot-password",
        data={"email": email, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def test_fourth_resend_in_an_hour_sends_nothing(auth_client, outbox):
    for _ in range(3):
        assert resend(auth_client).status_code == 303

    response = resend(auth_client)
    assert response.status_code == 429
    assert WAIT_MESSAGE in response.text
    assert len(outbox) == 3


def test_fourth_resend_with_htmx_shows_the_message_with_200(auth_client, outbox):
    for _ in range(3):
        resend(auth_client, htmx=True)

    # htmx does not put 4xx answers into the page, so the message comes with 200.
    response = resend(auth_client, htmx=True)
    assert response.status_code == 200
    assert WAIT_MESSAGE in response.text
    assert len(outbox) == 3


def test_fourth_forgot_password_sends_nothing_but_answers_the_same(client, user, outbox):
    for _ in range(3):
        forgot(client)

    response = forgot(client)
    assert response.status_code == 200
    assert FORGOT_MESSAGE in response.text
    assert len(outbox) == 3


def test_forgot_password_allows_5_requests_per_hour_per_ip(client, user):
    for i in range(5):
        assert forgot(client, email=f"user{i}@example.com").status_code == 200
    assert forgot(client).status_code == 429


def test_reset_unlocks_a_locked_email(client, user):
    for _ in range(5):
        log_in(client, password="wrong password!")
    assert log_in(client).status_code == 429

    new_password = "a brand new password"
    response = client.post(
        "/reset-password",
        data={
            "token": make_reset_token(user),
            "password": new_password,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert log_in(client, password=new_password).status_code == 303
