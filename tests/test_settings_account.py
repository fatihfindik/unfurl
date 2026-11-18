"""Settings › Account (D-132, SPEC §6.9): the name and the password. The current
password is checked like at sign in (login lockout, D-131); after a change this
session stays and the other devices are signed out."""

import html
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app
from app.models import User
from app.security import login_lockout
from app.services.auth_service import create_user, verify_password
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

HTMX = {"HX-Request": "true"}
NEW = "a brand new password"


def post(client, path, token=None, **data):
    return client.post(
        path, data=data, headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)}
    )


def change(client, current=USER_PASSWORD, new=NEW, repeat=None, token=None):
    return post(
        client,
        "/settings/account/password",
        token,
        current_password=current,
        new_password=new,
        repeat_password=new if repeat is None else repeat,
    )


def saved(engine, user) -> User:
    with Session(engine) as check:
        return check.get(User, user.id)


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def is_logged_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


# --- The page -------------------------------------------------------------------------------


def test_the_page_shows_the_account_section(auth_client):
    page = auth_client.get("/settings").text

    assert "Account" in page
    assert 'value="Jana Novak"' in page
    assert USER_EMAIL in page  # read-only
    assert 'name="email"' not in page  # no field to change it (#56)
    assert "Change password" in page


# --- Name ------------------------------------------------------------------------------------


def test_the_name_is_saved(auth_client, engine, user):
    response = post(auth_client, "/settings/account/name", full_name="  Jana Nováková  ")

    assert response.status_code == 200
    assert toast(response) == "Name saved."
    assert saved(engine, user).full_name == "Jana Nováková"


@pytest.mark.parametrize(
    ("name", "error"),
    [("", "Enter your full name."), ("   ", "Enter your full name."), ("x" * 201, "at most 200")],
)
def test_a_bad_name_is_refused(auth_client, engine, user, name, error):
    response = post(auth_client, "/settings/account/name", full_name=name)

    assert error in response.text
    assert "HX-Trigger" not in response.headers
    assert saved(engine, user).full_name == "Jana Novak"


def test_the_name_is_escaped(auth_client):
    response = post(auth_client, "/settings/account/name", full_name="<script>x</script>")

    assert "<script>x</script>" not in response.text
    assert "&lt;script&gt;" in response.text


# --- Password -------------------------------------------------------------------------------


def test_the_password_is_changed(auth_client, engine, user, outbox):
    response = change(auth_client)

    assert response.status_code == 200
    assert toast(response) == "Password changed. You were signed out on other devices."
    assert verify_password(NEW, saved(engine, user).password_hash)
    assert saved(engine, user).session_version == 1
    assert NEW not in response.text  # never written back into the page


def test_this_session_stays_and_its_csrf_token_still_works(auth_client, engine, user):
    token = csrf_token(auth_client)
    change(auth_client, token=token)

    assert is_logged_in(auth_client)
    # The open page keeps its token: the next htmx request still works.
    response = post(auth_client, "/settings/account/name", token=token, full_name="Jana N.")
    assert response.status_code == 200
    assert saved(engine, user).full_name == "Jana N."


def test_other_devices_are_signed_out(client, user):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)

    change(client)

    assert is_logged_in(client)
    assert not is_logged_in(phone)


def test_the_old_password_no_longer_signs_in(auth_client, client):
    change(auth_client)
    other = TestClient(app)

    assert log_in(other).status_code == 400
    assert log_in(other, password=NEW).status_code == 303


def test_a_verified_user_gets_an_email(auth_client, session, user, outbox):
    user.is_verified = True
    session.add(user)
    session.commit()

    change(auth_client)

    [email] = outbox
    assert email.to == USER_EMAIL
    assert "password was changed" in email.subject.lower()
    assert "/forgot-password" in email.body
    assert NEW not in email.body


def test_an_unverified_user_gets_no_email(auth_client, user, outbox):
    change(auth_client)

    assert outbox == []


@pytest.mark.parametrize(
    ("new", "repeat", "field_error"),
    [
        ("short", None, "at least 10"),
        ("x" * 1025, None, "at most 1024"),
        (NEW, "something else entirely", "don't match"),
    ],
)
def test_a_bad_new_password_is_refused(auth_client, engine, user, new, repeat, field_error):
    response = change(auth_client, new=new, repeat=repeat)

    assert field_error in html.unescape(response.text)
    assert verify_password(USER_PASSWORD, saved(engine, user).password_hash)


def test_a_wrong_current_password_is_refused_and_counted(auth_client, engine, user):
    response = change(auth_client, current="not my password")

    assert "That's not your current password." in html.unescape(response.text)
    assert verify_password(USER_PASSWORD, saved(engine, user).password_hash)
    assert saved(engine, user).session_version == 0


def test_five_wrong_passwords_lock_it_like_sign_in(auth_client, engine, user):
    for _ in range(5):
        change(auth_client, current="wrong password")

    response = change(auth_client)  # now even the right one

    assert "Too many wrong passwords" in response.text
    assert verify_password(USER_PASSWORD, saved(engine, user).password_hash)
    # The sign-in form is locked for this email too: one shared lockout.
    assert login_lockout.is_locked("testclient", USER_EMAIL)


def test_a_huge_current_password_counts_as_wrong(auth_client, engine, user):
    response = change(auth_client, current="x" * 100_000)

    assert "That's not your current password." in html.unescape(response.text)


def test_a_right_password_clears_earlier_failures(auth_client, engine, user):
    for _ in range(4):
        change(auth_client, current="wrong password")
    change(auth_client)  # right: the count starts again
    for _ in range(4):
        change(auth_client, current="wrong again", new="yet another password")

    response = change(auth_client, current=NEW, new="the third password")

    assert toast(response).startswith("Password changed.")


# --- Access ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/settings/account/name", "/settings/account/password"])
def test_saving_needs_a_csrf_token(auth_client, path):
    response = auth_client.post(path, data={"full_name": "X"}, headers=HTMX)

    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/settings/account/name", "/settings/account/password"])
def test_saving_needs_a_login(client, user, path):
    response = client.post(
        path, data={"full_name": "X", "csrf_token": csrf_token(client)}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_another_user_is_not_changed(client, session, engine, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    log_in(client)

    post(client, "/settings/account/name", full_name="Changed")
    change(client)

    assert saved(engine, other).full_name == "O"
    assert verify_password("x" * 12, saved(engine, other).password_hash)


def test_the_name_is_rate_limited(auth_client):
    token = csrf_token(auth_client)
    codes = [
        post(auth_client, "/settings/account/name", token, full_name="J").status_code
        for _ in range(31)
    ]

    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_the_password_is_rate_limited(auth_client):
    token = csrf_token(auth_client)
    codes = [change(auth_client, new="short", token=token).status_code for _ in range(11)]

    assert codes[:10] == [200] * 10 and codes[10] == 429
