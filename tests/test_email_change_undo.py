"""Undoing an email change from the old address (#60, D-147): a link in the notice,
a page that changes nothing, and a button that puts the old address back, signs
everyone out, makes the password unknown and sends a reset link to the old address."""

import datetime as dt
import html
import re

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlmodel import Session

from app.main import app
from app.models import User
from app.models.base import utcnow
from app.services import auth_service
from app.services.auth_service import create_user, make_undo_email_token
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in
from tests.test_email_change import NEW, ask, confirm, link_in, saved, token_of, verified
from tests.test_ext_api import api_login, bearer

THIRD = "jana.third@example.com"


def take_over(client, session, user, outbox, new_email=NEW) -> str:
    """Whoever knows the password changes the address; returns the undo link sent to
    the old address."""
    verified(session, user)
    log_in(client)
    ask(client, new_email=new_email)
    confirm(client, token_of(link_in_for(outbox, new_email)))
    return undo_link(outbox)


def link_in_for(outbox, address) -> str:
    [email] = [email for email in outbox if email.to == address and "confirm-email" in email.body]
    return re.search(r"/confirm-email\?token=\S+", email.body).group(0)


def undo_link(outbox) -> str:
    [notice] = [email for email in outbox if "/undo-email-change" in email.body]
    return re.search(r"/undo-email-change\?token=\S+", notice.body).group(0)


def undo(client, token):
    client.get("/undo-email-change", params={"token": token})  # the page sets the CSRF token
    return client.post(
        "/undo-email-change",
        data={"token": token, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def text(response) -> str:
    return html.unescape(response.text)


def is_logged_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


# --- The notice ------------------------------------------------------------------------


def test_the_notice_to_the_old_address_has_an_undo_link(client, session, user, outbox):
    link = take_over(client, session, user, outbox)

    [notice] = [email for email in outbox if email.to == USER_EMAIL and "changed" in email.subject]
    assert "the link works for 7 days" in notice.body
    assert link.startswith("/undo-email-change?token=")


def test_an_unverified_old_address_gets_no_link(auth_client, outbox):
    ask(auth_client)
    confirm(auth_client, token_of(link_in(outbox)))

    assert not any("/undo-email-change" in email.body for email in outbox)


# --- The page --------------------------------------------------------------------------


def test_opening_the_link_shows_a_page_and_changes_nothing(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    owner = TestClient(app)

    page = owner.get(link)
    head = owner.head(link)

    assert page.status_code == head.status_code == 200
    assert "Undo the email change" in page.text
    assert f"<b>{USER_EMAIL}</b>" in page.text
    assert NEW not in page.text  # the current address is masked: it may be the taker's
    assert "j***@e***.com" in page.text
    assert ">Undo the change</button>" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    assert saved(engine, user).email == NEW


# --- The button ------------------------------------------------------------------------


def test_the_button_puts_the_old_address_back(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    version = saved(engine, user).session_version

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 200
    assert "Your email is back" in response.text
    after = saved(engine, user)
    assert after.email == USER_EMAIL
    assert after.is_verified
    assert after.session_version == version + 1


def test_the_taker_is_signed_out_and_the_password_stops_working(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    assert is_logged_in(client)

    undo(TestClient(app), token_of(link))

    assert not is_logged_in(client)
    fresh = TestClient(app)
    assert log_in(fresh).status_code == 400  # the old address, the old password
    assert log_in(fresh, email=NEW).status_code == 400


def test_the_extension_is_signed_out_too(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    extension = TestClient(app)
    response = api_login(extension, email=NEW)
    assert response.status_code == 200
    token = response.json()["token"]
    assert extension.get("/api/ext/me", headers=bearer(token)).status_code == 200

    undo(TestClient(app), token_of(link))

    assert extension.get("/api/ext/me", headers=bearer(token)).status_code == 401


def test_a_reset_link_goes_to_the_old_address_and_works(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    outbox.clear()

    undo(TestClient(app), token_of(link))

    [reset] = [email for email in outbox if "reset-password" in email.body]
    assert reset.to == USER_EMAIL
    path = re.search(r"/reset-password\?token=\S+", reset.body).group(0)
    owner = TestClient(app)
    owner.get(path)
    response = owner.post(
        "/reset-password",
        data={
            "token": token_of(path),
            "password": "a brand new password",
            "csrf_token": csrf_token(owner),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert log_in(TestClient(app), password="a brand new password").status_code == 303


def test_the_link_still_works_after_the_taker_signs_out_or_changes_the_password(
    client, session, engine, user, outbox
):
    link = take_over(client, session, user, outbox)
    client.post("/logout", data={"csrf_token": csrf_token(client)})
    taker = saved(engine, user)
    taker.password_hash = auth_service.hash_password("the takers new password")
    with Session(engine) as edit:
        edit.add(taker)
        edit.commit()

    assert undo(TestClient(app), token_of(link)).status_code == 200
    assert saved(engine, user).email == USER_EMAIL


def test_a_second_change_does_not_escape_the_link(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    # The taker changes once more, to an address whose notice goes to them (NEW).
    ask(client, new_email=THIRD, password=USER_PASSWORD)
    confirm(client, token_of(link_in_for(outbox, THIRD)))
    assert saved(engine, user).email == THIRD

    assert undo(TestClient(app), token_of(link)).status_code == 200
    assert saved(engine, user).email == USER_EMAIL


def test_a_change_back_is_told_to_the_replaced_address(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    outbox.clear()

    undo(TestClient(app), token_of(link))

    [notice] = [email for email in outbox if email.to == NEW]
    assert notice.subject == "Your Unfurl email was changed back"
    assert "reset-password" not in notice.body


def test_the_taker_gets_no_link_to_take_the_account_back(client, session, engine, user, outbox):
    # Security review #60, H1: A to B to C would send a link for B (the taker's own,
    # brand-new address) to B. With it, they could take the account back after the
    # owner's undo. An address set less than 7 days ago gets no undo link.
    link = take_over(client, session, user, outbox)
    outbox.clear()
    ask(client, new_email=THIRD, password=USER_PASSWORD)
    confirm(client, token_of(link_in_for(outbox, THIRD)))

    [notice_to_b] = [email for email in outbox if email.to == NEW and "changed" in email.subject]
    assert "/undo-email-change" not in notice_to_b.body
    assert "contact us" in notice_to_b.body

    assert undo(TestClient(app), token_of(link)).status_code == 200
    assert saved(engine, user).email == USER_EMAIL


def test_an_address_in_use_for_7_days_gets_a_link_again(client, session, engine, user, outbox):
    with freeze_time(utcnow()) as frozen:
        take_over(client, session, user, outbox)
        frozen.tick(dt.timedelta(days=7, seconds=1))
        outbox.clear()
        log_in(client, email=NEW)
        ask(client, new_email=THIRD, password=USER_PASSWORD)
        confirm(client, token_of(link_in_for(outbox, THIRD)))

    [notice_to_b] = [email for email in outbox if email.to == NEW and "changed" in email.subject]
    assert "/undo-email-change" in notice_to_b.body


def test_an_undone_address_gets_no_link_for_7_days(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    undo(TestClient(app), token_of(link))
    assert saved(engine, user).email_changed_at is not None


def test_the_settings_form_warns_about_the_undo(auth_client):
    assert "for 7 days it can undo the change" in auth_client.get("/settings").text


def test_a_pending_change_link_dies_with_the_undo(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    ask(client, new_email=THIRD, password=USER_PASSWORD)
    pending = token_of(link_in_for(outbox, THIRD))

    undo(TestClient(app), token_of(link))

    assert confirm(TestClient(app), pending).status_code == 400
    assert saved(engine, user).email == USER_EMAIL


def test_a_reset_link_sent_to_the_new_address_dies(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    takers_reset = auth_service.make_reset_token(saved(engine, user))

    undo(TestClient(app), token_of(link))

    with Session(engine) as check:
        assert auth_service.user_from_reset_token(check, takers_reset) is None


# --- When the link doesn't work ----------------------------------------------------------


def test_the_link_works_once(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    assert undo(TestClient(app), token_of(link)).status_code == 200

    again = undo(TestClient(app), token_of(link))

    assert again.status_code == 400
    assert "This link is no longer valid" in again.text


def test_the_link_works_for_7_days(client, session, engine, user, outbox):
    with freeze_time(utcnow()) as frozen:
        link = take_over(client, session, user, outbox)
        frozen.tick(dt.timedelta(days=7, seconds=1))
        response = undo(TestClient(app), token_of(link))

    assert response.status_code == 400
    assert saved(engine, user).email == NEW


def test_the_link_works_until_7_days(client, session, engine, user, outbox):
    with freeze_time(utcnow()) as frozen:
        link = take_over(client, session, user, outbox)
        frozen.tick(dt.timedelta(days=7) - dt.timedelta(seconds=5))
        response = undo(TestClient(app), token_of(link))

    assert response.status_code == 200
    assert saved(engine, user).email == USER_EMAIL


@pytest.mark.parametrize("token", ["", "garbage", "x" * 1100])
def test_broken_links(client, token):
    assert client.get("/undo-email-change", params={"token": token}).status_code == 400
    response = client.post(
        "/undo-email-change", data={"token": token, "csrf_token": csrf_token(client)}
    )
    assert response.status_code == 400


def test_a_link_for_a_deleted_account_or_a_new_one_with_its_id(engine, user):
    token = make_undo_email_token(user, "old@example.com")
    with Session(engine) as edit:
        edit.delete(edit.get(User, user.id))
        edit.commit()
    with Session(engine) as check:
        assert auth_service.undo_email_from_token(check, token) is None

    # SQLite gives the deleted id to the next account; the stamp keeps it apart.
    with Session(engine) as new:
        newcomer = create_user(new, email="newcomer@example.com", password="x" * 12, full_name="N")
        assert newcomer.id == user.id
        assert auth_service.undo_email_from_token(new, token) is None


def test_a_token_with_other_data_is_refused(session, user):
    serializer = auth_service.token_serializer(auth_service.UNDO_EMAIL_TOKEN_SALT)
    stamp = auth_service.account_stamp(user)
    for data in (
        {"user_id": user.id, "account": stamp, "old_email": None},
        {"user_id": user.id, "account": stamp, "old_email": ["old@example.com"]},
        {"user_id": user.id, "account": "another stamp", "old_email": "old@example.com"},
        {"user_id": True, "account": stamp, "old_email": "old@example.com"},
    ):
        assert auth_service.undo_email_from_token(session, serializer.dumps(data)) is None


def test_the_button_needs_a_csrf_token(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    other = TestClient(app)

    response = other.post("/undo-email-change", data={"token": token_of(link)})

    assert response.status_code == 403
    assert saved(engine, user).email == NEW


# --- The old address is used by another account --------------------------------------------


def test_if_someone_signed_up_with_the_old_address(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    create_user(session, email=USER_EMAIL, password="squatter pass 1", full_name="S")

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 409
    assert "use Forgot password with it" in text(response)
    assert saved(engine, user).email == NEW


def test_losing_the_race_for_the_old_address_is_rolled_back(
    client, session, engine, user, outbox, monkeypatch
):
    link = take_over(client, session, user, outbox)
    create_user(session, email=USER_EMAIL, password="squatter pass 1", full_name="S")
    monkeypatch.setattr(auth_service, "email_used_by_another", lambda *args: False)

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 409
    assert saved(engine, user).email == NEW


# --- Long addresses and limits ----------------------------------------------------------------


def test_long_addresses_fit_in_the_links(session, user):
    long_old = "a" * 64 + "@" + ("b" * 61 + ".") * 3 + "com"  # 254 characters
    long_new = "c" * 64 + "@" + ("d" * 61 + ".") * 3 + "com"
    assert len(long_old) == len(long_new) == 254
    user.email = long_old
    session.add(user)
    session.commit()

    confirm_token = auth_service.make_email_change_token(user, long_new)
    assert auth_service.email_change_from_token(session, confirm_token) == (user, long_new)
    undo_token = auth_service.make_undo_email_token(user, "someone.else@example.com")
    assert auth_service.undo_email_from_token(session, undo_token) == (
        user,
        "someone.else@example.com",
    )


def test_opening_the_link_is_limited(client):
    for _ in range(30):
        client.get("/undo-email-change", params={"token": "x"})
    assert client.get("/undo-email-change", params={"token": "x"}).status_code == 429


def test_the_button_is_limited(client):
    data = {"token": "x", "csrf_token": csrf_token(client)}
    for _ in range(10):
        client.post("/undo-email-change", data=data)
    assert client.post("/undo-email-change", data=data).status_code == 429
