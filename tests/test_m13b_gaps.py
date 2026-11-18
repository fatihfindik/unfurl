"""Edge cases of M13b (D-130…D-133) not covered by the main test files: odd request
bodies, races with a reset link or a second request, stale and forged cookies, a
photo file that can't be removed, and odd LEGAL_* values."""

import base64
import html
import json
import re
import time

import itsdangerous
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import get_settings
from app.main import SESSION_COOKIE, SESSION_MAX_AGE, app
from app.models import User
from app.routers import settings as settings_routes
from app.services import (
    auth_service,
    export_service,
    notification_service,
    photo_service,
    reminder_email,
)
from app.services.auth_service import (
    create_user,
    make_reset_token,
    reset_password,
    user_from_reset_token,
    verify_password,
)
from tests.account_data import fill_account
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

HTMX = {"HX-Request": "true"}
NEW = "a brand new password"
RESET = "set by the reset link"


@pytest.fixture(autouse=True)
def no_running_exports():
    settings_routes._exports_running.clear()


def saved(engine, user) -> User | None:
    with Session(engine) as check:
        return check.get(User, user if isinstance(user, int) else user.id)


def change(client, current=USER_PASSWORD, new=NEW, token=None):
    return client.post(
        "/settings/account/password",
        data={"current_password": current, "new_password": new, "repeat_password": new},
        headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)},
    )


def delete(client, password=USER_PASSWORD, token=None):
    return client.post(
        "/settings/delete-account",
        data={"password": password},
        headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)},
        follow_redirects=False,
    )


def sent_to_sign_in(response) -> bool:
    """Not logged in: a 303 to /login, or for htmx an HX-Redirect to /login (not the
    "account deleted" page, which only a real deletion leads to)."""
    target = response.headers.get("HX-Redirect") or response.headers.get("location") or ""
    return target.startswith("/login") and "deleted" not in target


def export(client, token=None):
    return client.post("/settings/export", data={"csrf_token": token or csrf_token(client)})


def verified(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


def copy_of(client) -> TestClient:
    """A second browser with the same session cookie (a stolen or replayed cookie)."""
    other = TestClient(app)
    other.cookies.set(SESSION_COOKIE, client.cookies.get(SESSION_COOKIE))
    return other


def signed_cookie(data: dict, secret: str, age_seconds: int = 0) -> str:
    """A session cookie like Starlette's SessionMiddleware writes it, made `age_seconds` ago."""

    class Signer(itsdangerous.TimestampSigner):
        def get_timestamp(self):
            return int(time.time()) - age_seconds

    payload = base64.b64encode(json.dumps(data).encode())
    return Signer(secret).sign(payload).decode()


# --- Odd request bodies ---------------------------------------------------------------------


def test_a_json_body_saves_no_name(auth_client, engine, user):
    # The routes read form fields; a JSON body has none, so it's an empty name.
    response = auth_client.post(
        "/settings/account/name",
        json={"full_name": "From JSON"},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code in (200, 400, 422)
    assert "HX-Trigger" not in response.headers
    assert saved(engine, user).full_name == "Jana Novak"


def test_a_json_body_changes_no_password(auth_client, engine, user):
    response = auth_client.post(
        "/settings/account/password",
        json={"current_password": USER_PASSWORD, "new_password": NEW, "repeat_password": NEW},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code in (200, 400, 422)
    assert verify_password(USER_PASSWORD, saved(engine, user).password_hash)


def test_a_json_body_deletes_nothing(auth_client, engine, user):
    response = auth_client.post(
        "/settings/delete-account",
        json={"password": USER_PASSWORD},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
        follow_redirects=False,
    )

    assert "HX-Redirect" not in response.headers
    assert saved(engine, user) is not None


def test_a_multipart_body_works_like_a_form(auth_client, engine, user):
    response = auth_client.post(
        "/settings/account/name",
        files={"full_name": (None, "Jana M.")},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    assert saved(engine, user).full_name == "Jana M."


def test_a_repeated_name_field_saves_one_of_them(auth_client, engine, user):
    response = auth_client.post(
        "/settings/account/name",
        data={"full_name": ["First", "Second"]},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    assert saved(engine, user).full_name in ("First", "Second")


def test_missing_password_fields_change_nothing_and_are_not_counted(auth_client, engine, user):
    token = csrf_token(auth_client)
    for _ in range(6):
        response = auth_client.post(
            "/settings/account/password", headers=HTMX | {"X-CSRF-Token": token}
        )
        assert "at least 10" in response.text

    # The new password is checked first: empty posts never touch the lockout.
    assert change(auth_client, token=token).headers.get("HX-Trigger")
    assert verify_password(NEW, saved(engine, user).password_hash)


def test_a_missing_delete_password_deletes_nothing(auth_client, engine, user):
    response = auth_client.post(
        "/settings/delete-account",
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
        follow_redirects=False,
    )

    assert "not your current password" in html.unescape(response.text)
    assert saved(engine, user) is not None


# --- Password change races and stale sessions -----------------------------------------------


def test_a_reset_link_used_in_the_meantime_wins(auth_client, engine, user, monkeypatch):
    # The current password is checked, then a reset link changes it before the UPDATE:
    # the conditional UPDATE matches nothing and the reset password stays.
    token = make_reset_token(user)
    original = settings_routes.check_password_with_lockout

    def check_then_reset(request, current, password):
        answer = original(request, current, password)
        with Session(engine) as other:
            reset_user = user_from_reset_token(other, token)
            assert reset_password(other, reset_user, RESET)
        return answer

    monkeypatch.setattr(settings_routes, "check_password_with_lockout", check_then_reset)

    response = change(auth_client)

    assert "changed in the meantime" in response.text
    assert "HX-Trigger" not in response.headers
    assert verify_password(RESET, saved(engine, user).password_hash)
    # The reset signed out every session, this one too.
    assert auth_client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("path", ["/settings/export", "/settings/theme"])
def test_the_old_csrf_token_works_on_other_routes_after_a_change(auth_client, user, path):
    token = csrf_token(auth_client)
    change(auth_client, token=token)

    response = auth_client.post(
        path, data={"theme": "dark"}, headers=HTMX | {"X-CSRF-Token": token}
    )

    assert response.status_code in (200, 204)


def test_a_cookie_from_before_a_password_change_cannot_delete(client, engine, user):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)
    phone_token = csrf_token(phone)

    change(client)
    response = delete(phone, password=NEW, token=phone_token)

    assert sent_to_sign_in(response) or response.status_code == 403
    assert saved(engine, user) is not None


def test_a_cookie_from_before_a_password_change_cannot_export(client, user):
    phone = TestClient(app)
    log_in(client)
    log_in(phone)
    phone_token = csrf_token(phone)

    change(client)
    response = phone.post(
        "/settings/export", data={"csrf_token": phone_token}, follow_redirects=False
    )

    assert response.status_code in (303, 403)
    assert USER_EMAIL not in response.text


def test_the_reset_link_no_longer_works_after_a_change(auth_client, engine, user):
    # A link mailed before the change must not undo it.
    token = make_reset_token(user)

    change(auth_client)

    with Session(engine) as check:
        assert user_from_reset_token(check, token) is None


# --- Export ---------------------------------------------------------------------------------


def test_another_users_export_does_not_block_mine(auth_client, session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    settings_routes._start_export(other.id)

    response = export(auth_client)

    assert response.status_code == 200
    assert json.loads(response.content)["account"]["email"] == USER_EMAIL
    assert other.id in settings_routes._exports_running  # theirs is untouched


def test_the_file_name_never_contains_user_input(auth_client, session, user):
    user.full_name = 'x"; filename="evil.html'
    session.add(user)
    session.commit()

    disposition = export(auth_client).headers["content-disposition"]

    assert re.fullmatch(
        r'attachment; filename="unfurl-export-\d{4}-\d{2}-\d{2}\.json"', disposition
    )


def test_an_account_deleted_before_the_stream_starts_leaks_nothing(
    auth_client, session, engine, user, monkeypatch
):
    fill_account(session, user)
    user_id = user.id
    original = export_service.stream

    def delete_first(export_session, user_id, now=None):
        with Session(engine) as other:
            other.delete(other.get(User, user_id))
            other.commit()
        yield from original(export_session, user_id, now)

    monkeypatch.setattr(export_service, "stream", delete_first)

    response = export(auth_client)

    assert "Acme Secret Corp" not in response.text
    assert USER_EMAIL not in response.text
    assert user_id not in settings_routes._exports_running  # the slot is freed


def test_an_account_deleted_during_the_stream_ends_cleanly(
    auth_client, session, engine, user, monkeypatch
):
    fill_account(session, user)
    user_id = user.id
    original = export_service.stream

    def delete_after_the_head(export_session, user_id, now=None):
        pieces = original(export_session, user_id, now)
        yield next(pieces)
        with Session(engine) as other:
            other.delete(other.get(User, user_id))
            other.commit()
        yield from pieces

    monkeypatch.setattr(export_service, "stream", delete_after_the_head)

    response = export(auth_client)

    json.loads(response.content)  # still one valid file
    assert user_id not in settings_routes._exports_running


def test_a_cookie_signed_with_another_key_cannot_export(client, user):
    cookie = signed_cookie(
        {"user_id": user.id, "session_version": 0, "csrf": "t"}, "not the secret key"
    )
    client.cookies.set(SESSION_COOKIE, cookie)

    response = client.post(
        "/settings/export", headers={"X-CSRF-Token": "t"}, follow_redirects=False
    )

    assert response.status_code in (303, 403)
    assert USER_EMAIL not in response.text


@pytest.mark.parametrize(("age", "allowed"), [(0, True), (SESSION_MAX_AGE + 60, False)])
def test_an_expired_session_cannot_export(client, user, age, allowed):
    secret = get_settings().secret_key.get_secret_value()
    data = {
        "user_id": user.id,
        "session_version": 0,
        "account": auth_service.account_stamp(user),  # see SESSION_ACCOUNT (M2)
        "csrf": "t",
    }
    client.cookies.set(SESSION_COOKIE, signed_cookie(data, secret, age))

    response = client.post(
        "/settings/export", headers={"X-CSRF-Token": "t"}, follow_redirects=False
    )

    # A fresh cookie with the right key proves the cookie format is right.
    assert (response.status_code == 200) is allowed
    assert (USER_EMAIL in response.text) is allowed


def test_a_deleted_accounts_cookie_cannot_export(auth_client, user):
    old = copy_of(auth_client)
    token = csrf_token(auth_client)
    delete(auth_client, token=token)

    response = old.post("/settings/export", data={"csrf_token": token}, follow_redirects=False)

    assert response.status_code == 303
    assert USER_EMAIL not in response.text


# --- Delete my account ----------------------------------------------------------------------


def test_a_photo_file_that_cannot_be_removed_still_deletes_the_account(
    auth_client, session, engine, user, outbox, monkeypatch
):
    verified(session, user)
    fill_account(session, user, photo_path="photos/locked.jpg")
    user_id = user.id

    def refuse(relative):
        raise OSError("permission denied")

    monkeypatch.setattr(photo_service, "delete_photo_file", refuse)

    response = delete(auth_client)

    assert response.headers["HX-Redirect"] == "/login?deleted=1"
    assert saved(engine, user_id) is None
    assert len(outbox) == 1


def test_a_photo_file_already_gone_still_deletes_the_account(auth_client, session, engine, user):
    fill_account(session, user, photo_path="photos/gone.jpg")
    user_id = user.id

    response = delete(auth_client)

    assert response.headers["HX-Redirect"] == "/login?deleted=1"
    assert saved(engine, user_id) is None


def test_a_replayed_cookie_cannot_delete_twice(auth_client, session, engine, user, outbox):
    verified(session, user)
    token = csrf_token(auth_client)
    old = copy_of(auth_client)

    delete(auth_client, token=token)
    response = delete(old, token=token)

    assert sent_to_sign_in(response)
    assert len(outbox) == 1  # one confirmation, not two


def test_a_second_request_at_the_same_moment_ends_on_the_login_page(
    auth_client, session, engine, user, monkeypatch
):
    # A double click: both requests pass the password check, the first one deletes the
    # account before the second one does. The second must not end in a server error.
    original = settings_routes.check_password_with_lockout

    def check_then_lose_the_race(request, current, password):
        answer = original(request, current, password)
        with Session(engine) as other:
            other.delete(other.get(User, current.id))
            other.commit()
        return answer

    monkeypatch.setattr(settings_routes, "check_password_with_lockout", check_then_lose_the_race)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE, auth_client.cookies.get(SESSION_COOKIE))

    response = delete(client, token=csrf_token(auth_client))

    assert response.status_code != 500
    assert saved(engine, user) is None


@pytest.mark.parametrize(
    "value", ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "0", ""]
)
def test_the_deleted_notice_never_echoes_the_query(client, value):
    page = client.get("/login", params={"deleted": value}).text

    assert "<script>alert(1)</script>" not in page
    assert "onerror=alert(1)" not in page


def test_the_deleted_notice_is_shown_only_when_asked(client):
    assert "were deleted" not in client.get("/login").text


# --- Scheduler emails (D-131) ---------------------------------------------------------------


def test_still_wanted_keeps_nothing_for_an_empty_list(session):
    assert notification_service.still_wanted(session, []) == []


def test_still_wanted_drops_an_unverified_user(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    verified(session, other)
    mine = reminder_email.build(user, "Time to study", "Week 1")
    theirs = reminder_email.build(other, "Time to study", "Week 1")

    kept = notification_service.still_wanted(session, [(user.id, mine), (other.id, theirs)])

    assert kept == [theirs]


# --- Legal pages with odd LEGAL_* values ----------------------------------------------------


@pytest.mark.parametrize("url", ["/privacy", "/terms", "/imprint"])
def test_odd_operator_details_render_safely(client, monkeypatch, url):
    settings = get_settings()
    monkeypatch.setattr(settings, "legal_operator_name", "Max " + "Muster" * 2000)
    monkeypatch.setattr(settings, "legal_address", "Line 1\nLine 2\r\n<b>3</b>")
    monkeypatch.setattr(settings, "legal_email", 'a"onclick="alert(1)@example.com')

    response = client.get(url)

    assert response.status_code == 200
    assert "<b>3</b>" not in response.text
    assert 'onclick="alert(1)' not in response.text


def test_quotes_in_the_email_stay_inside_the_mailto_link(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "legal_email", 'a"onclick="alert(1)@example.com')

    page = client.get("/imprint").text

    [href] = re.findall(r'href="(mailto:[^"]*)"', page)
    assert html.unescape(href) == 'mailto:a"onclick="alert(1)@example.com'
