"""Changing the email address (#56, D-134): a link to the new address, notices to the
old one, nothing changes before the link is used, and the link dies with a new
password, a sign-out or a change already made."""

import html
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app
from app.models import User
from app.security import email_limiter, login_lockout
from app.services import auth_service, reminder_email
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

HTMX = {"HX-Request": "true"}
NEW = "jana.new@example.com"


def ask(client, new_email=NEW, password=USER_PASSWORD, token=None):
    return client.post(
        "/settings/account/email",
        data={"new_email": new_email, "password": password},
        headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)},
    )


def link_in(outbox) -> str:
    [confirm] = [email for email in outbox if email.to == NEW]
    return re.search(r"/confirm-email\?token=\S+", confirm.body).group(0)


def token_of(link: str) -> str:
    return link.split("token=", 1)[1]


def confirm(client, token):
    client.get("/confirm-email", params={"token": token})  # the page sets the CSRF token
    return client.post(
        "/confirm-email",
        data={"token": token, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def saved(engine, user) -> User:
    with Session(engine) as check:
        return check.get(User, user.id)


def verified(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


def is_logged_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


def text(response) -> str:
    return html.unescape(response.text)


# --- Asking ----------------------------------------------------------------------------------


def test_asking_sends_a_link_and_changes_nothing(auth_client, session, engine, user, outbox):
    verified(session, user)

    response = ask(auth_client)

    toast = json.loads(response.headers["HX-Trigger"])["toast"]["message"]
    assert toast == f"We sent a link to {NEW}. Your email changes when you open it."
    assert saved(engine, user).email == USER_EMAIL
    assert {email.to for email in outbox} == {NEW, USER_EMAIL}
    assert link_in(outbox)


def test_the_old_address_gets_a_masked_notice(auth_client, session, user, outbox):
    verified(session, user)

    ask(auth_client)

    [notice] = [email for email in outbox if email.to == USER_EMAIL]
    assert reminder_email.masked(NEW) in notice.body
    assert NEW not in notice.body
    assert "/forgot-password" in notice.body


def test_an_unverified_old_address_gets_no_notice(auth_client, user, outbox):
    ask(auth_client)

    assert [email.to for email in outbox] == [NEW]


def test_the_link_email_has_no_name(auth_client, session, user, outbox):
    user.full_name = "Click here http://evil.example"
    session.add(user)
    session.commit()

    ask(auth_client)

    [link_email] = [email for email in outbox if email.to == NEW]
    assert "evil" not in link_email.body and "Click here" not in link_email.body


def test_the_address_is_normalized(auth_client, outbox):
    ask(auth_client, new_email="  Jana.New@Example.COM ")

    assert any(email.to == NEW for email in outbox)


@pytest.mark.parametrize(
    ("new_email", "error"),
    [
        ("not-an-email", "Enter a valid email address."),
        ("", "Enter a valid email address."),
        ("a" * 330 + "@example.com", "Enter a valid email address."),
        (USER_EMAIL, "This is already your email."),
    ],
)
def test_bad_addresses_are_refused(auth_client, outbox, new_email, error):
    response = ask(auth_client, new_email=new_email)

    assert error in text(response)
    assert outbox == []


def test_a_wrong_password_is_refused_and_counted(auth_client, outbox):
    response = ask(auth_client, password="wrong password")

    assert "That's not your current password." in text(response)
    assert outbox == []
    for _ in range(4):
        login_lockout.record_failure("testclient", USER_EMAIL)
    assert login_lockout.is_locked("testclient", USER_EMAIL)


def test_a_taken_address_is_refused_only_after_the_password(auth_client, session, outbox):
    create_user(session, email=NEW, password="x" * 12, full_name="Other")

    # With a wrong password the page doesn't say whether the address is taken.
    wrong = ask(auth_client, password="wrong password")
    assert "already exists" not in text(wrong)

    right = ask(auth_client)
    assert "An account with this email already exists." in text(right)
    assert outbox == []


def test_a_stranger_gets_at_most_three_emails_an_hour(auth_client, outbox):
    token = csrf_token(auth_client)
    for _ in range(3):
        ask(auth_client, token=token)

    response = ask(auth_client, token=token)

    assert "Please wait a few minutes" in text(response)
    assert [email.to for email in outbox].count(NEW) == 3


def test_refused_requests_dont_use_the_strangers_quota(auth_client, outbox):
    token = csrf_token(auth_client)
    for _ in range(3):
        ask(auth_client, password="wrong", token=token)
    login_lockout.reset()

    assert email_limiter.allow(NEW)


def test_asking_needs_a_csrf_token_and_a_login(auth_client, client):
    response = auth_client.post(
        "/settings/account/email", data={"new_email": NEW, "password": "x"}, headers=HTMX
    )
    assert response.status_code == 403

    other = TestClient(app)
    response = other.post(
        "/settings/account/email",
        data={"new_email": NEW, "password": "x", "csrf_token": csrf_token(other)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_asking_is_rate_limited(auth_client):
    token = csrf_token(auth_client)
    codes = [ask(auth_client, password="wrong", token=token).status_code for _ in range(6)]

    assert codes[5] == 429


def test_the_settings_page_has_the_form(auth_client):
    page = auth_client.get("/settings").text

    assert 'hx-post="/settings/account/email"' in page


# --- Confirming ------------------------------------------------------------------------------


def test_opening_the_link_shows_a_page_and_changes_nothing(auth_client, engine, user, outbox):
    ask(auth_client)
    token = token_of(link_in(outbox))

    response = TestClient(app).get("/confirm-email", params={"token": token})

    assert response.status_code == 200
    assert NEW in response.text
    assert "Use this email" in response.text
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert saved(engine, user).email == USER_EMAIL


def test_the_button_changes_the_email(auth_client, engine, user, outbox):
    ask(auth_client)

    response = confirm(TestClient(app), token_of(link_in(outbox)))

    assert response.headers["location"] == "/login?email_changed=1"
    after = saved(engine, user)
    assert after.email == NEW
    assert after.is_verified
    assert after.session_version == 1


def test_in_the_same_browser_the_session_stays(auth_client, engine, user, outbox):
    ask(auth_client)

    response = confirm(auth_client, token_of(link_in(outbox)))

    assert response.headers["location"] == "/settings"
    # The toast waits for the next page (a flash, read once).
    assert f"Your email is now {NEW}." in auth_client.get("/settings").text
    assert is_logged_in(auth_client)


def test_other_devices_are_signed_out(client, user, outbox):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)
    ask(client)

    confirm(client, token_of(link_in(outbox)))

    assert is_logged_in(client)
    assert not is_logged_in(phone)


def test_the_new_address_signs_in_the_old_one_doesnt(auth_client, outbox):
    ask(auth_client)
    confirm(TestClient(app), token_of(link_in(outbox)))

    fresh = TestClient(app)
    assert log_in(fresh).status_code == 400
    assert log_in(fresh, email=NEW).status_code == 303


def test_the_old_address_is_told_after_the_change(auth_client, session, user, outbox):
    verified(session, user)
    ask(auth_client)
    before = len(outbox)

    confirm(TestClient(app), token_of(link_in(outbox)))

    [changed] = [email for email in outbox[before:] if email.to == USER_EMAIL]
    assert "was changed" in changed.subject
    assert reminder_email.masked(NEW) in changed.body


def test_a_link_works_once(auth_client, engine, user, outbox):
    ask(auth_client)
    token = token_of(link_in(outbox))
    confirm(TestClient(app), token)

    again = confirm(TestClient(app), token)

    assert again.status_code == 400
    assert "no longer valid" in again.text


@pytest.mark.parametrize("how", ["reset", "password", "sign out"])
def test_a_new_password_or_signing_out_cancels_the_change(
    auth_client, session, engine, user, outbox, how
):
    ask(auth_client)
    token = token_of(link_in(outbox))
    if how == "reset":
        auth_service.reset_password(session, session.get(User, user.id), "a brand new password")
    elif how == "password":
        auth_client.post(
            "/settings/account/password",
            data={
                "current_password": USER_PASSWORD,
                "new_password": "a brand new password",
                "repeat_password": "a brand new password",
            },
            headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
        )
    else:
        auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})

    response = confirm(TestClient(app), token)

    assert response.status_code == 400
    assert saved(engine, user).email == USER_EMAIL


def test_an_old_link_doesnt_come_back_after_a_round_trip(session, engine, user):
    # A→B, then B→A: the first A→B link must not work again although the email matches.
    first = auth_service.make_email_change_token(user, NEW)
    auth_service.change_email(session, user, "jana.other@example.com")
    session.refresh(user)
    auth_service.change_email(session, user, USER_EMAIL)

    assert auth_service.email_change_from_token(session, first) is None


def test_a_second_link_dies_when_the_first_is_used(auth_client, engine, user, outbox):
    token = csrf_token(auth_client)
    ask(auth_client, token=token)
    ask(auth_client, new_email="jana.third@example.com", token=token)
    first = token_of(link_in(outbox))
    [second_email] = [e for e in outbox if e.to == "jana.third@example.com"]
    second = token_of(re.search(r"/confirm-email\?token=\S+", second_email.body).group(0))

    confirm(TestClient(app), first)

    assert confirm(TestClient(app), second).status_code == 400
    assert saved(engine, user).email == NEW


def test_if_the_address_was_taken_meanwhile(auth_client, session, engine, user, outbox):
    ask(auth_client)
    token = token_of(link_in(outbox))
    create_user(session, email=NEW, password="x" * 12, full_name="Faster")

    response = confirm(TestClient(app), token)

    assert response.status_code == 409
    assert "already in use" in response.text
    assert saved(engine, user).email == USER_EMAIL


def test_old_email_links_stop_working(auth_client, session, user, outbox):
    verify = auth_service.make_verify_token(user)
    reset = auth_service.make_reset_token(user)
    off = reminder_email.make_off_token(user)
    ask(auth_client)
    confirm(TestClient(app), token_of(link_in(outbox)))

    other = TestClient(app)
    assert "no longer valid" in other.get("/reminders/off", params={"token": off}).text
    assert other.get("/reset-password", params={"token": reset}).status_code == 400
    assert "48 hours" in other.get("/verify-email", params={"token": verify}).text


def test_another_signed_in_users_browser_is_left_alone(auth_client, session, engine, user, outbox):
    ask(auth_client)
    token = token_of(link_in(outbox))
    other = create_user(session, email="other@example.com", password="y" * 12, full_name="O")
    their_browser = TestClient(app)
    log_in(their_browser, email="other@example.com", password="y" * 12)

    response = confirm(their_browser, token)

    assert response.headers["location"] == "/login?email_changed=1"
    assert is_logged_in(their_browser)  # still them
    assert saved(engine, other).email == "other@example.com"
    assert saved(engine, user).email == NEW


@pytest.mark.parametrize("token", ["", "garbage", "x" * 600])
def test_broken_links(client, token):
    response = client.get("/confirm-email", params={"token": token})

    assert response.status_code == 400
    assert "no longer valid" in response.text


def test_an_expired_link(auth_client, engine, user, outbox, monkeypatch):
    ask(auth_client)
    token = token_of(link_in(outbox))
    monkeypatch.setattr(auth_service, "EMAIL_CHANGE_TOKEN_MAX_AGE", -1)

    assert TestClient(app).get("/confirm-email", params={"token": token}).status_code == 400


def test_the_post_needs_a_csrf_token(auth_client, engine, user, outbox):
    ask(auth_client)
    token = token_of(link_in(outbox))

    response = TestClient(app).post("/confirm-email", data={"token": token})

    assert response.status_code == 403
    assert saved(engine, user).email == USER_EMAIL


def test_the_login_page_says_so(client):
    assert "Your email was changed." in client.get("/login?email_changed=1").text


def test_losing_the_race_for_the_address_is_rolled_back(session, user, monkeypatch):
    create_user(session, email=NEW, password="x" * 12, full_name="Faster")
    monkeypatch.setattr(auth_service, "email_in_use", lambda *args: False)

    assert auth_service.change_email(session, user, NEW) == "taken"
    session.expire_all()
    assert session.get(User, user.id).email == USER_EMAIL
