"""Sign out ends every session (D-128, SPEC §6.1): this browser, a copied cookie and
the user's other devices. Links in emails keep working."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app
from app.models import User
from app.services import auth_service, reminder_email
from tests.helpers import csrf_token, log_in


def sign_out(client):
    return client.post("/logout", data={"csrf_token": csrf_token(client)}, follow_redirects=False)


def is_logged_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


def version(engine, user) -> int:
    with Session(engine) as check:
        return check.get(User, user.id).session_version


def test_a_copied_cookie_stops_working(auth_client):
    copy = dict(auth_client.cookies)

    assert sign_out(auth_client).headers["location"] == "/login"

    thief = TestClient(app, cookies=copy)
    assert not is_logged_in(thief)


def test_other_devices_are_signed_out_too(client, engine, user):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)
    assert is_logged_in(phone)

    sign_out(client)

    assert not is_logged_in(phone)
    assert version(engine, user) == 1


def test_signing_in_again_works(auth_client):
    sign_out(auth_client)

    assert log_in(auth_client).status_code == 303
    assert is_logged_in(auth_client)


def test_an_old_cookie_does_not_sign_out_the_new_session(client, engine, user):
    log_in(client)
    old = dict(client.cookies)
    sign_out(client)  # version 0 -> 1
    log_in(client)  # a new session with version 1

    stale = TestClient(app, cookies=old)
    stale.post("/logout", data={"csrf_token": csrf_token(stale)})

    assert version(engine, user) == 1
    assert is_logged_in(client)


def test_signing_out_without_a_session_is_harmless(client, engine, user):
    response = sign_out(client)

    assert response.status_code == 303
    assert version(engine, user) == 0


def test_email_links_still_work_after_signing_out(auth_client, session, user, outbox):
    verify = auth_service.make_verify_token(user)
    off = reminder_email.make_off_token(user)
    sign_out(auth_client)

    assert auth_client.get("/verify-email", params={"token": verify}).status_code == 200
    assert auth_client.get("/reminders/off", params={"token": off}).status_code == 200


def test_signing_out_still_needs_a_csrf_token(auth_client, engine, user):
    response = auth_client.post("/logout", follow_redirects=False)

    assert response.status_code == 403
    assert is_logged_in(auth_client)
    assert version(engine, user) == 0


def test_the_menu_says_sign_out(auth_client):
    assert "Sign out" in auth_client.get("/").text
