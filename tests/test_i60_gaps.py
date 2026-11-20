"""Edge and abuse cases for undoing an email change (#60, D-147) that
test_email_change_undo.py doesn't cover: headers on every answer, CSRF from another
browser, tokens made for other purposes, escaping, the browser's own session, the
login lockout and email limit, case, a second undo link and a double submit."""

import html
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import set_committed_value

from app.main import app
from app.models import User
from app.routers import auth as auth_router
from app.security import email_limiter, login_lockout
from app.services import auth_service
from app.services.auth_service import (
    EMAIL_CHANGE_TOKEN_SALT,
    RESET_TOKEN_SALT,
    VERIFY_TOKEN_SALT,
    account_stamp,
    create_user,
    make_undo_email_token,
    token_serializer,
)
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in
from tests.test_email_change import NEW, ask, confirm, saved, token_of
from tests.test_email_change_undo import (
    THIRD,
    is_logged_in,
    link_in_for,
    take_over,
    undo,
    undo_link,
)

FORM = {"Content-Type": "application/x-www-form-urlencoded"}
IP = "testclient"  # the client address TestClient sends
HOSTILE = "<img src=x onerror=alert(1)>@example.com"


def undo_data(user, old_email=USER_EMAIL) -> dict:
    return {"user_id": user.id, "account": account_stamp(user), "old_email": old_email}


def resets_to(outbox, address) -> list:
    return [e for e in outbox if e.to == address and "/reset-password?token=" in e.body]


# --- Headers on every answer -----------------------------------------------------------------


def assert_token_headers(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_the_page_and_the_result_have_the_token_headers(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    owner = TestClient(app)

    page = owner.get(link)
    result = undo(owner, token_of(link))

    assert (page.status_code, result.status_code) == (200, 200)
    assert_token_headers(page)
    assert_token_headers(result)


@pytest.mark.parametrize("token", ["", "garbage"])
def test_invalid_link_answers_have_the_token_headers(client, token):
    page = client.get("/undo-email-change", params={"token": token})
    button = client.post(
        "/undo-email-change", data={"token": token, "csrf_token": csrf_token(client)}
    )

    assert (page.status_code, button.status_code) == (400, 400)
    assert_token_headers(page)
    assert_token_headers(button)


def test_the_taken_answer_has_the_token_headers(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    create_user(session, email=USER_EMAIL, password="squatter pass 1", full_name="S")

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 409
    assert_token_headers(response)


def test_the_page_without_a_token(client):
    response = client.get("/undo-email-change")

    assert response.status_code == 400
    assert "This link is no longer valid" in response.text


# --- CSRF ------------------------------------------------------------------------------------


def test_another_browsers_csrf_token_is_refused(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    attacker, victim = TestClient(app), TestClient(app)
    attacker.get(link)
    victim.get(link)

    response = victim.post(
        "/undo-email-change",
        data={"token": token_of(link), "csrf_token": csrf_token(attacker)},
    )

    assert response.status_code == 403
    assert saved(engine, user).email == NEW


# --- Tokens made for other purposes ----------------------------------------------------------


@pytest.mark.parametrize("salt", [EMAIL_CHANGE_TOKEN_SALT, RESET_TOKEN_SALT, VERIFY_TOKEN_SALT])
def test_the_same_data_signed_for_another_purpose_is_refused(
    client, session, engine, user, outbox, salt
):
    take_over(client, session, user, outbox)
    token = token_serializer(salt).dumps(undo_data(saved(engine, user)))
    browser = TestClient(app)

    assert browser.get("/undo-email-change", params={"token": token}).status_code == 400
    assert undo(browser, token).status_code == 400
    assert saved(engine, user).email == NEW


def test_real_links_of_other_kinds_are_refused(client, session, engine, user, outbox):
    take_over(client, session, user, outbox)
    current = saved(engine, user)
    tokens = [
        auth_service.make_email_change_token(current, THIRD),
        auth_service.make_reset_token(current),
        auth_service.make_verify_token(current),
    ]

    for token in tokens:
        assert undo(TestClient(app), token).status_code == 400
    assert saved(engine, user).email == NEW


def test_an_undo_link_is_refused_by_the_other_link_pages(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    token = token_of(link)
    browser = TestClient(app)

    for path in ("/confirm-email", "/reset-password", "/verify-email"):
        browser.get(path, params={"token": token})
        response = browser.post(
            path,
            data={
                "token": token,
                "password": "a brand new password",
                "csrf_token": csrf_token(browser),
            },
            follow_redirects=False,
        )
        assert response.status_code == 400, path
    assert saved(engine, user).email == NEW


def test_a_token_in_the_query_of_the_post_is_not_used(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    browser = TestClient(app)
    browser.get(link)

    response = browser.post(
        "/undo-email-change",
        params={"token": token_of(link)},
        data={"csrf_token": csrf_token(browser)},
    )

    assert response.status_code == 400
    assert saved(engine, user).email == NEW


@pytest.mark.parametrize("bad_first", [True, False])
def test_a_repeated_token_field_is_handled_cleanly(
    client, session, engine, user, outbox, bad_first
):
    link = take_over(client, session, user, outbox)
    good = token_of(link)
    tokens = ["garbage", good] if bad_first else [good, "garbage"]
    browser = TestClient(app)
    browser.get(link)

    response = browser.post(
        "/undo-email-change",
        content=urlencode([("token", t) for t in tokens] + [("csrf_token", csrf_token(browser))]),
        headers=FORM,
    )

    # Whichever value is used, the answer and the database agree.
    undone = saved(engine, user).email == USER_EMAIL
    assert response.status_code == (200 if undone else 400)


# --- Escaping --------------------------------------------------------------------------------


def test_the_page_and_the_result_escape_the_old_address(client, session, engine, user, outbox):
    # Signed by the app itself, so this checks the templates, not the address rule.
    token = make_undo_email_token(user, HOSTILE)
    browser = TestClient(app)

    page = browser.get("/undo-email-change", params={"token": token})
    result = undo(browser, token)

    assert page.status_code == result.status_code == 200
    for response in (page, result):
        assert HOSTILE not in response.text
        assert html.escape(HOSTILE, quote=True) in response.text
    assert saved(engine, user).email == HOSTILE


def test_the_page_escapes_the_masked_current_address(session, engine, user, client):
    user.email = "<b@<i.com"  # masked: "<***@<***.com"
    session.add(user)
    session.commit()
    token = make_undo_email_token(user, "jana.old@example.com")

    page = client.get("/undo-email-change", params={"token": token})

    assert page.status_code == 200
    assert "<***@<***.com" not in page.text
    assert "&lt;***@&lt;***.com" in page.text


def test_the_taken_page_escapes_the_old_address(session, engine, user, client):
    create_user(session, email=HOSTILE, password="squatter pass 1", full_name="S")
    token = make_undo_email_token(user, HOSTILE)

    response = undo(client, token)

    assert response.status_code == 409
    assert HOSTILE not in response.text
    assert HOSTILE in html.unescape(response.text)
    assert saved(engine, user).email == USER_EMAIL


# --- This browser's session ------------------------------------------------------------------


def test_the_button_signs_out_a_browser_signed_in_as_someone_else(
    client, session, engine, user, outbox
):
    link = take_over(client, session, user, outbox)
    create_user(session, email="other@example.com", password="other password 1", full_name="O")
    browser = TestClient(app)
    assert (
        log_in(browser, email="other@example.com", password="other password 1").status_code == 303
    )

    response = undo(browser, token_of(link))

    assert response.status_code == 200
    assert not is_logged_in(browser)
    assert saved(engine, user).email == USER_EMAIL


def test_a_dead_link_doesnt_touch_the_session(client, session, engine, user, outbox):
    link = take_over(client, session, user, outbox)
    undo(TestClient(app), token_of(link))
    create_user(session, email="other@example.com", password="other password 1", full_name="O")
    browser = TestClient(app)
    log_in(browser, email="other@example.com", password="other password 1")

    response = undo(browser, token_of(link))

    assert response.status_code == 400
    assert is_logged_in(browser)


# --- Login lockout and email limit -----------------------------------------------------------


def test_the_login_lockout_of_the_old_address_is_cleared(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    for ip in (IP, "203.0.113.9"):
        for _ in range(5):
            login_lockout.record_failure(ip, USER_EMAIL)
    assert login_lockout.is_locked(IP, USER_EMAIL)

    undo(TestClient(app), token_of(link))

    assert not login_lockout.is_locked(IP, USER_EMAIL)
    assert not login_lockout.is_locked("203.0.113.9", USER_EMAIL)


def test_a_dead_link_doesnt_clear_the_lockout(client):
    for _ in range(5):
        login_lockout.record_failure(IP, USER_EMAIL)

    undo(client, "garbage")

    assert login_lockout.is_locked(IP, USER_EMAIL)


def test_the_reset_email_is_sent_even_when_the_email_limit_is_used(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    while email_limiter.allow(USER_EMAIL):
        pass
    outbox.clear()

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 200
    assert len(resets_to(outbox, USER_EMAIL)) == 1


def test_the_reset_email_counts_toward_the_email_limit(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    email_limiter.reset()

    undo(TestClient(app), token_of(link))

    # 3 per hour: the undo used one.
    assert [email_limiter.allow(USER_EMAIL) for _ in range(3)] == [True, True, False]


# --- Case ------------------------------------------------------------------------------------


def test_a_sign_up_with_the_old_address_in_other_case_blocks_the_undo(
    client, session, engine, user, outbox
):
    link = take_over(client, session, user, outbox)
    create_user(session, email="  JANA@Example.COM ", password="squatter pass 1", full_name="S")

    response = undo(TestClient(app), token_of(link))

    assert response.status_code == 409
    assert saved(engine, user).email == NEW


def test_a_lockout_on_the_old_address_in_other_case_is_cleared(client, session, user, outbox):
    link = take_over(client, session, user, outbox)
    for _ in range(5):
        login_lockout.record_failure(IP, "JANA@Example.com")

    undo(TestClient(app), token_of(link))

    assert not login_lockout.is_locked(IP, USER_EMAIL)


# --- A second undo link ----------------------------------------------------------------------


def test_the_takers_own_undo_link_cant_take_the_account_back(client, session, engine, user, outbox):
    # A -> B (undo link to A, the owner), then B -> C: B is verified now. Whether B (the
    # taker's inbox) gets no undo link at all or one that is refused, after the owner
    # undoes to A it must not put B back: that would sign the owner out and send B a
    # reset link (security review #60, H1).
    owners_link = take_over(client, session, user, outbox)
    outbox_before = len(outbox)
    ask(client, new_email=THIRD, password=USER_PASSWORD)
    confirm(client, token_of(link_in_for(outbox, THIRD)))
    takers_links = [
        undo_link([e]) for e in outbox[outbox_before:] if "/undo-email-change" in e.body
    ]

    assert undo(TestClient(app), token_of(owners_link)).status_code == 200
    assert saved(engine, user).email == USER_EMAIL
    outbox.clear()

    for link in takers_links:
        assert undo(TestClient(app), token_of(link)).status_code == 400
    assert saved(engine, user).email == USER_EMAIL
    assert resets_to(outbox, NEW) == []


def test_after_a_second_change_the_owners_link_still_wins(client, session, engine, user, outbox):
    owners_link = take_over(client, session, user, outbox)
    ask(client, new_email=THIRD, password=USER_PASSWORD)
    confirm(client, token_of(link_in_for(outbox, THIRD)))
    outbox.clear()

    response = undo(TestClient(app), token_of(owners_link))

    assert response.status_code == 200
    assert saved(engine, user).email == USER_EMAIL
    # The only reset link goes to A; B and C get none.
    assert [e.to for e in outbox if "/reset-password?token=" in e.body] == [USER_EMAIL]


# --- Double submit ---------------------------------------------------------------------------


def stale_read(user, read_email=NEW, old_email=USER_EMAIL):
    """What a second request read before the first one saved the undo: the account,
    in the request's own session, still with the address it had then."""

    def read(session, token):
        stale = session.get(User, user.id)
        set_committed_value(stale, "email", read_email)  # as loaded, not a change
        return stale, old_email

    return read


def test_two_clicks_at_the_same_moment_are_refused_cleanly(
    client, session, engine, user, outbox, monkeypatch
):
    # Both requests checked the token and the "taken" query before either saved.
    link = take_over(client, session, user, outbox)
    second = stale_read(user)
    assert undo(TestClient(app), token_of(link)).status_code == 200
    version = saved(engine, user).session_version
    monkeypatch.setattr(auth_router, "undo_email_from_token", second)
    monkeypatch.setattr(auth_service, "email_used_by_another", lambda *args: False)
    outbox.clear()

    response = undo(TestClient(app), token_of(link))

    assert response.status_code in (400, 409)  # not a 500
    assert saved(engine, user).session_version == version  # nothing changed twice
    assert outbox == []  # no second reset link


def test_a_second_click_after_the_first_saved_isnt_called_another_account(
    client, session, engine, user, outbox, monkeypatch
):
    # The second request read the token before the first saved, and asks "is the old
    # address taken?" after: it is, by this same account. The "another account" page
    # would tell the owner to sign in to that account and delete it: their own.
    link = take_over(client, session, user, outbox)
    second = stale_read(user)
    assert undo(TestClient(app), token_of(link)).status_code == 200
    monkeypatch.setattr(auth_router, "undo_email_from_token", second)
    outbox.clear()

    response = undo(TestClient(app), token_of(link))

    assert response.status_code in (400, 409)
    assert "Another Unfurl account" not in html.unescape(response.text)
    assert outbox == []
