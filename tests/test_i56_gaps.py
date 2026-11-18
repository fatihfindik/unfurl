"""Edge and abuse cases for changing the email address (#56, D-134) that
test_email_change.py doesn't cover: odd bodies, hostile addresses and names, forged
or stale links, other devices, escaping, rate limits and the login lockout."""

import html
import json
import re
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlmodel import Session

from app.main import app
from app.models import User
from app.security import login_lockout
from app.services import account_service
from app.services.auth_service import (
    EMAIL_CHANGE_TOKEN_SALT,
    VERIFY_TOKEN_SALT,
    create_user,
    make_email_change_token,
    token_serializer,
)
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

HTMX = {"HX-Request": "true"}
# Repeated fields are sent as a raw urlencoded body (the client can't build one).
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
NEW = "jana.new@example.com"
IP = "testclient"  # the client address TestClient sends


def ask(client, new_email=NEW, password=USER_PASSWORD, token=None):
    return client.post(
        "/settings/account/email",
        data={"new_email": new_email, "password": password},
        headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)},
    )


def link_token(outbox, to=NEW) -> str:
    [email] = [e for e in outbox if e.to == to]
    return re.search(r"/confirm-email\?token=(\S+)", email.body).group(1)


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


def is_logged_in(client) -> bool:
    return client.get("/", follow_redirects=False).status_code == 200


def verified(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


def token_data(user, new_email=NEW) -> dict:
    return {
        "user_id": user.id,
        "old_email": user.email,
        "new_email": new_email,
        "version": user.session_version,
    }


# --- Asking: request bodies -----------------------------------------------------------------


def test_a_json_body_is_not_read_and_sends_nothing(auth_client, engine, user, outbox):
    # The route reads a form; JSON fields are ignored, so the address is "empty".
    response = auth_client.post(
        "/settings/account/email",
        json={"new_email": NEW, "password": USER_PASSWORD},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code in (200, 422)
    assert outbox == []
    assert saved(engine, user).email == USER_EMAIL


def test_a_multipart_body_works_like_a_form(auth_client, outbox):
    response = auth_client.post(
        "/settings/account/email",
        data={"new_email": NEW, "password": USER_PASSWORD},
        files={"extra": ("note.txt", b"ignored", "text/plain")},
        headers=HTMX | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    assert response.status_code == 200
    assert [e.to for e in outbox] == [NEW]


def test_a_repeated_address_field_sends_one_link_to_the_address_shown(auth_client, outbox):
    response = auth_client.post(
        "/settings/account/email",
        content=urlencode(
            [
                ("new_email", NEW),
                ("new_email", "jana.third@example.com"),
                ("password", USER_PASSWORD),
            ]
        ),
        headers=HTMX | FORM | {"X-CSRF-Token": csrf_token(auth_client)},
    )

    [link_email] = outbox  # the user is unverified: no notice
    toast = json.loads(response.headers["HX-Trigger"])["toast"]["message"]
    assert link_email.to in toast
    assert link_token(outbox, to=link_email.to)


def test_asking_without_login_from_htmx_redirects_to_login(client, user, outbox):
    response = client.post(
        "/settings/account/email",
        data={"new_email": NEW, "password": USER_PASSWORD},
        headers=HTMX | {"X-CSRF-Token": csrf_token(client)},
    )

    assert response.headers["HX-Redirect"].startswith("/login")
    assert outbox == []


# --- Asking: hostile or odd addresses -------------------------------------------------------


@pytest.mark.parametrize(
    "new_email",
    [
        NEW + "\r\nBcc: evil@example.com",  # header injection
        NEW + "\nBcc: evil@example.com",
        "jana.new@example.com,evil@example.com",  # two recipients
        "jana new@example.com",
        "jana@" + "a" * 64 + ".com",  # domain label over 63
        "jana@" + ("a" * 60 + ".") * 5 + "com",  # address over 254
        "<script>@example.com",
        "jana@localhost",
    ],
)
def test_hostile_addresses_are_refused(auth_client, engine, user, outbox, new_email):
    response = ask(auth_client, new_email=new_email)

    assert "Enter a valid email address." in html.unescape(response.text)
    assert outbox == []
    assert saved(engine, user).email == USER_EMAIL


@pytest.mark.parametrize(
    "new_email",
    [
        "Evil <jana.new@example.com>",
        '"Evil\r\nBcc: evil@example.com" <jana.new@example.com>',
        '"<img src=x onerror=alert(1)>" <jana.new@example.com>',
    ],
)
def test_a_display_name_never_reaches_the_link_or_the_account(
    auth_client, engine, user, outbox, new_email
):
    # Pydantic's EmailStr accepts "Name <address>". The link must go to the bare address
    # (or be refused); the name must never be sent to, signed into a link or saved.
    ask(auth_client, new_email=new_email)

    assert all(e.to == NEW for e in outbox), [e.to for e in outbox]
    if outbox:
        confirm(TestClient(app), link_token(outbox))
        assert saved(engine, user).email in (USER_EMAIL, NEW)


def test_an_international_address_works_end_to_end(auth_client, engine, user, outbox):
    idn = "jana@exämple.com"

    ask(auth_client, new_email=idn)
    confirm(TestClient(app), link_token(outbox, to=idn))

    assert saved(engine, user).email == idn
    assert log_in(TestClient(app), email=idn).status_code == 303


def test_a_taken_address_is_found_whatever_the_case(auth_client, session, outbox):
    create_user(session, email="other@example.com", password="x" * 12, full_name="Other")

    response = ask(auth_client, new_email="  OTHER@Example.com ")

    assert "An account with this email already exists." in html.unescape(response.text)
    assert outbox == []


# --- Asking: hostile names in the notices ---------------------------------------------------


@pytest.mark.parametrize(
    "full_name",
    ["Jana\r\nBcc: evil@example.com", "Jana\n\nSubject: You won", "{{ base_url }}"],
)
def test_the_name_cant_change_the_notice_headers(auth_client, session, user, outbox, full_name):
    verified(session, user)
    user.full_name = full_name
    session.add(user)
    session.commit()

    ask(auth_client)
    [requested] = [e for e in outbox if e.to == USER_EMAIL]
    before = len(outbox)
    confirm(TestClient(app), link_token(outbox))
    [changed] = [e for e in outbox[before:] if e.to == USER_EMAIL]

    assert requested.subject == "Your Unfurl email is about to change"
    assert changed.subject == "Your Unfurl email was changed"
    # The name is shown as text, not run as a template.
    assert full_name in requested.body and full_name in changed.body


# --- Asking: password check and the login lockout -------------------------------------------


def test_while_locked_even_the_right_password_is_refused(auth_client, outbox):
    for _ in range(5):
        login_lockout.record_failure(IP, USER_EMAIL)

    response = ask(auth_client)

    assert "Too many wrong passwords" in html.unescape(response.text)
    assert outbox == []


@pytest.mark.parametrize("new_email", ["not-an-email", USER_EMAIL])
def test_refused_addresses_dont_count_against_the_password(auth_client, new_email):
    # Format and "same address" come before the password, so they never count.
    token = csrf_token(auth_client)
    for _ in range(5):
        ask(auth_client, new_email=new_email, password="wrong", token=token)

    assert not login_lockout.is_locked(IP, USER_EMAIL)


def test_wrong_passwords_here_lock_the_sign_in_form_too(auth_client, client, outbox):
    token = csrf_token(auth_client)
    for _ in range(5):
        ask(auth_client, password="wrong", token=token)

    assert log_in(TestClient(app)).status_code == 429
    assert outbox == []


def test_a_too_long_password_is_wrong_and_counted(auth_client, outbox):
    response = ask(auth_client, password="x" * 5000)

    assert "That's not your current password." in html.unescape(response.text)
    assert outbox == []


def test_after_the_change_the_lockout_follows_the_new_address(auth_client, outbox):
    ask(auth_client)
    confirm(TestClient(app), link_token(outbox))

    stranger = TestClient(app)
    for _ in range(5):
        log_in(stranger, email=NEW, password="wrong password")

    assert login_lockout.is_locked(IP, NEW)
    assert log_in(TestClient(app), email=NEW).status_code == 429


def test_a_lock_on_the_old_address_doesnt_block_the_confirm(auth_client, engine, user, outbox):
    # The link proves the new address; the password was checked when asking.
    ask(auth_client)
    for _ in range(5):
        login_lockout.record_failure(IP, USER_EMAIL)

    response = confirm(TestClient(app), link_token(outbox))

    assert response.status_code == 303
    assert saved(engine, user).email == NEW


# --- Confirming: forged and stale links -----------------------------------------------------


def test_a_link_for_a_deleted_account(session, engine, user, client):
    token = make_email_change_token(user, NEW)
    account_service.delete_account(session, session.get(User, user.id))

    assert client.get("/confirm-email", params={"token": token}).status_code == 400
    assert confirm(client, token).status_code == 400


def test_a_link_with_another_users_id_is_refused(session, engine, user, client):
    other = create_user(session, email="other@example.com", password="y" * 12, full_name="O")
    mine = make_email_change_token(user, NEW)
    theirs = make_email_change_token(other, NEW)
    # Their data with my signature: the signature doesn't match the data.
    # A token is "payload.timestamp.signature" (the payload may start with a dot).
    forged = theirs.rsplit(".", 2)[0] + "." + ".".join(mine.rsplit(".", 2)[1:])

    assert confirm(client, forged).status_code == 400
    assert saved(engine, other).email == "other@example.com"


def test_a_link_signed_with_another_key_is_refused(engine, user, client):
    forged = URLSafeTimedSerializer("not the secret", salt=EMAIL_CHANGE_TOKEN_SALT).dumps(
        token_data(user)
    )

    assert confirm(client, forged).status_code == 400
    assert saved(engine, user).email == USER_EMAIL


def test_a_link_from_another_kind_of_email_is_refused(engine, user, client):
    # Same data, but signed for verification links (another salt).
    other_salt = token_serializer(VERIFY_TOKEN_SALT).dumps(token_data(user))

    assert confirm(client, other_salt).status_code == 400
    assert saved(engine, user).email == USER_EMAIL


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "1"},
        {"user_id": None},
        {"new_email": None},
        {"new_email": ["jana.new@example.com"]},
        {"old_email": "someone@example.com"},
        {"version": 7},
    ],
)
def test_signed_links_with_wrong_data_are_refused(engine, user, client, change):
    token = token_serializer(EMAIL_CHANGE_TOKEN_SALT).dumps(token_data(user) | change)

    assert client.get("/confirm-email", params={"token": token}).status_code == 400
    assert confirm(client, token).status_code == 400
    assert saved(engine, user).email == USER_EMAIL


def test_a_signed_non_dict_link_is_refused(client, user):
    token = token_serializer(EMAIL_CHANGE_TOKEN_SALT).dumps([user.id, NEW])

    assert client.get("/confirm-email", params={"token": token}).status_code == 400


def test_the_page_without_a_token(client):
    response = client.get("/confirm-email")

    assert response.status_code == 400
    assert "no longer valid" in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_the_button_without_a_token(client, engine, user):
    client.get("/confirm-email")
    response = client.post("/confirm-email", data={"csrf_token": csrf_token(client)})

    assert response.status_code == 400
    assert saved(engine, user).email == USER_EMAIL


@pytest.mark.parametrize("bad_first", [True, False])
def test_a_repeated_token_field_is_handled_cleanly(auth_client, engine, user, outbox, bad_first):
    ask(auth_client)
    good = link_token(outbox)
    tokens = ["garbage", good] if bad_first else [good, "garbage"]
    browser = TestClient(app)
    browser.get("/confirm-email", params={"token": good})

    response = browser.post(
        "/confirm-email",
        content=urlencode([("token", t) for t in tokens] + [("csrf_token", csrf_token(browser))]),
        headers=FORM,
        follow_redirects=False,
    )

    # Whichever value is used, the answer and the database agree.
    changed = saved(engine, user).email == NEW
    assert response.status_code == (303 if changed else 400)


def test_a_token_in_the_query_of_the_post_is_not_used(auth_client, engine, user, outbox):
    ask(auth_client)
    good = link_token(outbox)
    browser = TestClient(app)
    browser.get("/confirm-email", params={"token": good})

    response = browser.post(
        "/confirm-email",
        params={"token": good},
        data={"csrf_token": csrf_token(browser)},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert saved(engine, user).email == USER_EMAIL


def test_the_error_pages_have_the_token_headers(auth_client, session, user, outbox):
    ask(auth_client)
    token = link_token(outbox)
    create_user(session, email=NEW, password="x" * 12, full_name="Faster")

    taken = confirm(TestClient(app), token)

    assert taken.status_code == 409
    assert taken.headers["Cache-Control"] == "no-store"
    assert taken.headers["Referrer-Policy"] == "no-referrer"


# --- Confirming: the same user on other devices ---------------------------------------------


def test_a_device_signed_in_after_asking_stays_signed_in(client, engine, user, outbox):
    laptop, phone = client, TestClient(app)
    log_in(laptop)
    ask(laptop)
    log_in(phone)

    response = confirm(phone, link_token(outbox))

    assert response.headers["location"] == "/settings"
    assert is_logged_in(phone)
    assert not is_logged_in(laptop)
    assert saved(engine, user).email == NEW


def test_a_signed_out_device_of_the_same_user_is_not_signed_back_in(client, engine, user, outbox):
    laptop, phone = client, TestClient(app)
    log_in(laptop)
    log_in(phone)
    # A new password on the laptop signs the phone out; the laptop goes on.
    laptop.post(
        "/settings/account/password",
        data={
            "current_password": USER_PASSWORD,
            "new_password": "a brand new password",
            "repeat_password": "a brand new password",
        },
        headers=HTMX | {"X-CSRF-Token": csrf_token(laptop)},
    )
    assert not is_logged_in(phone)
    ask(laptop, password="a brand new password")

    response = confirm(phone, link_token(outbox))

    # The phone's old session has A's id but an old version: it must not come back.
    assert response.headers["location"] == "/login?email_changed=1"
    assert not is_logged_in(phone)
    assert saved(engine, user).email == NEW


def test_after_signing_out_an_old_link_fails_on_a_new_session(client, engine, user, outbox):
    laptop, phone = client, TestClient(app)
    log_in(laptop)
    ask(laptop)
    token = link_token(outbox)
    laptop.post("/logout", data={"csrf_token": csrf_token(laptop)})
    log_in(phone)

    response = confirm(phone, token)

    assert response.status_code == 400
    assert is_logged_in(phone)  # a dead link doesn't touch the session
    assert saved(engine, user).email == USER_EMAIL


def test_the_old_address_gets_no_notice_from_a_dead_link(auth_client, session, user, outbox):
    verified(session, user)
    ask(auth_client)
    token = link_token(outbox)
    confirm(TestClient(app), token)
    before = len(outbox)

    confirm(TestClient(app), token)

    assert outbox[before:] == []


# --- Escaping ------------------------------------------------------------------------------


HOSTILE = "<img src=x onerror=alert(1)>@example.com"


def test_the_confirm_page_escapes_the_address(session, user, client):
    # Signed by the app itself, so this checks the template, not the address rule.
    token = make_email_change_token(user, HOSTILE)

    page = client.get("/confirm-email", params={"token": token})

    assert page.status_code == 200
    assert HOSTILE not in page.text
    assert html.escape(HOSTILE, quote=True) in page.text


def test_the_flash_after_confirming_escapes_the_address(auth_client, user):
    token = make_email_change_token(user, HOSTILE)

    response = confirm(auth_client, token)
    page = auth_client.get(response.headers["location"]).text

    assert HOSTILE not in page
    assert f"Your email is now {HOSTILE}." in html.unescape(page)


def test_a_valid_address_with_quotes_and_ampersands_is_escaped(auth_client, outbox):
    odd = "o'neil&co@example.com"

    ask(auth_client, new_email=odd)
    token = link_token(outbox, to=odd)
    page = auth_client.get("/confirm-email", params={"token": token}).text
    response = confirm(auth_client, token)
    settings = auth_client.get(response.headers["location"]).text

    assert odd not in page and odd in html.unescape(page)
    assert odd not in settings and f"Your email is now {odd}." in html.unescape(settings)


# --- Rate limits ---------------------------------------------------------------------------


def test_the_confirm_page_is_rate_limited(client):
    codes = [client.get("/confirm-email", params={"token": "x"}).status_code for _ in range(31)]

    assert codes[:30] == [400] * 30
    assert codes[30] == 429


def test_the_confirm_button_is_rate_limited(client, engine, user):
    token = csrf_token(client)
    codes = [
        client.post("/confirm-email", data={"token": "x", "csrf_token": token}).status_code
        for _ in range(11)
    ]

    assert codes[:10] == [400] * 10
    assert codes[10] == 429


def test_a_good_link_is_refused_once_the_button_limit_is_used(auth_client, engine, user, outbox):
    ask(auth_client)
    good = link_token(outbox)
    browser = TestClient(app)
    browser.get("/confirm-email", params={"token": good})
    csrf = csrf_token(browser)
    for _ in range(10):
        browser.post("/confirm-email", data={"token": "x", "csrf_token": csrf})

    response = browser.post("/confirm-email", data={"token": good, "csrf_token": csrf})

    assert response.status_code == 429
    assert saved(engine, user).email == USER_EMAIL
