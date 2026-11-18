"""Delete my account (D-131, SPEC §10.2): after the password, every row and the photo
file are gone, the session ends, a verified address gets a confirmation. Nothing
happens with a wrong password, and nothing of anyone else is touched."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session

from app.main import app
from app.models import User
from app.security import login_lockout
from app.services import notification_service, reminder_email
from app.services.auth_service import create_user
from tests.account_data import fill_account
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in
from tests.test_photo import image_bytes, upload

HTMX = {"HX-Request": "true"}


def delete(client, password=USER_PASSWORD, htmx=True, token=None):
    headers = HTMX if htmx else {}
    return client.post(
        "/settings/delete-account",
        data={"password": password, "csrf_token": token or csrf_token(client)},
        headers=headers,
        follow_redirects=False,
    )


def rows_of(engine, user_id) -> dict:
    """{table: rows} for every table with a user_id column that has rows of user_id."""
    found = {}
    with engine.connect() as connection:
        for table in inspect(connection).get_table_names():
            columns = {c["name"] for c in inspect(connection).get_columns(table)}
            column = "id" if table == "user" else "user_id"
            if column in columns:
                count = connection.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE {column} = :id'), {"id": user_id}
                ).scalar()
                if count:
                    found[table] = count
        found_events = connection.execute(text("SELECT count(*) FROM applicationevent")).scalar()
        found_rows = connection.execute(text("SELECT count(*) FROM experience")).scalar()
    return found | {"_events": found_events, "_experience": found_rows}


def verified(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


# --- Deleting -------------------------------------------------------------------------------


def test_everything_of_the_user_is_deleted(auth_client, session, engine, user):
    fill_account(session, user)
    user_id = user.id

    response = delete(auth_client)

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login?deleted=1"
    assert rows_of(engine, user_id) == {"_events": 0, "_experience": 0}


def test_without_htmx_it_redirects(auth_client, user):
    response = delete(auth_client, htmx=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?deleted=1"


def test_the_login_page_says_so(client):
    page = client.get("/login?deleted=1").text

    assert "Your account and all your data were deleted." in page


def test_the_photo_file_is_deleted(auth_client, session, user, upload_dir):
    upload(auth_client, image_bytes())
    photos = list((upload_dir / "photos").iterdir())
    assert len(photos) == 1

    delete(auth_client)

    assert list((upload_dir / "photos").iterdir()) == []


def test_the_session_ends_here_and_on_other_devices(client, user):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)

    delete(client)

    assert client.get("/", follow_redirects=False).status_code == 303
    assert phone.get("/", follow_redirects=False).status_code == 303
    assert log_in(phone).status_code == 400  # the account is gone


def test_a_verified_user_gets_a_confirmation(auth_client, session, user, outbox):
    verified(session, user)

    delete(auth_client)

    [email] = outbox
    assert email.to == USER_EMAIL
    assert "deleted" in email.subject.lower()
    assert "Jana Novak" in email.body


def test_an_unverified_address_gets_nothing(auth_client, user, outbox):
    delete(auth_client)

    assert outbox == []


def test_the_email_can_sign_up_again(auth_client, client, user):
    delete(auth_client)

    response = client.post(
        "/register",
        data={
            "full_name": "Jana",
            "email": USER_EMAIL,
            "password": USER_PASSWORD,
            "privacy": "yes",
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


# --- Refused --------------------------------------------------------------------------------


def test_a_wrong_password_deletes_nothing(auth_client, session, engine, user):
    fill_account(session, user)
    before = rows_of(engine, user.id)

    response = delete(auth_client, password="not my password")

    assert response.status_code == 200
    assert "That&#39;s not your current password." in response.text
    assert "HX-Redirect" not in response.headers
    assert rows_of(engine, user.id) == before


def test_wrong_passwords_count_toward_the_sign_in_lockout(auth_client, engine, user):
    # The route allows 5 tries an hour per IP; the lockout (5 failures) is shared
    # with the sign-in form, so failures here and there add up.
    for _ in range(4):
        delete(auth_client, password="wrong password")
    login_lockout.record_failure("testclient", USER_EMAIL)  # a fifth one, at sign in

    assert "Too many wrong passwords" in delete(auth_client).text
    with Session(engine) as check:
        assert check.get(User, user.id) is not None


def test_while_locked_even_the_right_password_is_refused(auth_client, engine, user):
    for _ in range(5):
        login_lockout.record_failure("testclient", USER_EMAIL)

    response = delete(auth_client)

    assert "Too many wrong passwords" in response.text
    with Session(engine) as check:
        assert check.get(User, user.id) is not None


def test_a_huge_password_is_refused(auth_client, engine, user):
    response = delete(auth_client, password="x" * 100_000)

    assert "That&#39;s not your current password." in response.text


def test_another_users_data_stays(client, session, engine, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    fill_account(session, other)
    before = rows_of(engine, other.id)
    log_in(client)

    delete(client)

    assert rows_of(engine, other.id) == before


@pytest.mark.parametrize("htmx", [True, False])
def test_it_needs_a_csrf_token(auth_client, engine, user, htmx):
    response = auth_client.post(
        "/settings/delete-account",
        data={"password": USER_PASSWORD},
        headers=HTMX if htmx else {},
    )

    assert response.status_code == 403
    with Session(engine) as check:
        assert check.get(User, user.id) is not None


def test_it_needs_a_login(client, engine, user):
    response = client.post(
        "/settings/delete-account",
        data={"password": USER_PASSWORD, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    with Session(engine) as check:
        assert check.get(User, user.id) is not None


def test_it_is_rate_limited(auth_client):
    token = csrf_token(auth_client)
    codes = [delete(auth_client, "wrong", token=token).status_code for _ in range(6)]

    assert codes[5] == 429


def test_the_settings_page_has_the_modal(auth_client):
    page = auth_client.get("/settings").text

    assert 'data-dialog-open="delete-account"' in page
    assert 'hx-post="/settings/delete-account"' in page
    assert "There is no Undo." in page


# --- The scheduler at the same moment (D-131) -------------------------------------------------


def test_a_reminder_email_for_a_deleted_user_is_not_sent(session, user, monkeypatch):
    sent = []
    monkeypatch.setattr(reminder_email, "send_all", lambda emails: sent.extend(emails))
    verified(session, user)
    email = reminder_email.build(user, "Time to study", "Week 1")
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    verified(session, other)
    other_email = reminder_email.build(other, "Time to study", "Week 1")

    session.delete(user)
    session.commit()

    kept = notification_service.still_wanted(session, [(1, email), (other.id, other_email)])
    assert kept == [other_email]


def test_writing_a_reminder_for_a_deleted_user_is_rolled_back(session, user, monkeypatch):
    # The scheduler worked out the reminder, then the account was deleted before
    # the row was written: the insert fails on the foreign key and is rolled back.
    due = notification_service.Due("study_reminder", "study:2026-11-18", "Time", "Week 1")
    monkeypatch.setattr(notification_service, "due_for_user", lambda *args: [due])
    stale = session.get(User, user.id)
    with Session(session.get_bind()) as other_session:
        other_session.delete(other_session.get(User, user.id))
        other_session.commit()

    written = notification_service.generate_for_user(
        session, stale, dt.datetime(2026, 11, 18, 18, tzinfo=dt.UTC)
    )

    assert written == 0
