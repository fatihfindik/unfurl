"""Edge and abuse cases for verifying the email with a button (#66, D-146) that
test_verification.py doesn't cover: response headers, forged or stale tokens, other
users' sessions, the resend button on the error page and escaping."""

import html
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from sqlmodel import Session

from app.main import app
from app.models import User
from app.services import account_service
from app.services.auth_service import (
    EMAIL_CHANGE_TOKEN_SALT,
    RESET_TOKEN_SALT,
    VERIFY_TOKEN_SALT,
    create_user,
    make_verify_token,
    token_serializer,
)
from tests.helpers import csrf_token, log_in

# Repeated fields are sent as a raw urlencoded body (the client can't build one).
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


def confirm(client, token):
    """Press "Verify my email" (D-146)."""
    return client.post(
        "/verify-email",
        data={"token": token, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )


def page(client, token):
    return client.get("/verify-email", params={"token": token})


def is_verified(engine, user) -> bool:
    with Session(engine) as check:
        return check.get(User, user.id).is_verified


def verified(session, user):
    user.is_verified = True
    session.add(user)
    session.commit()


def assert_token_headers(response):
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.fixture
def other(session) -> User:
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")


# --- Response headers -----------------------------------------------------------------------
# The token is in the URL and in the page: it must not be cached or sent on as a referrer.


def test_the_confirm_page_has_the_token_headers(client, user):
    response = page(client, make_verify_token(user))

    assert response.status_code == 200
    assert_token_headers(response)


def test_the_invalid_link_page_has_the_token_headers(client):
    response = page(client, "broken")

    assert response.status_code == 400
    assert_token_headers(response)


def test_the_already_verified_page_has_the_token_headers(client, session, user):
    verified(session, user)
    response = page(client, make_verify_token(user))

    assert "Email already verified" in response.text
    assert_token_headers(response)


@pytest.mark.parametrize("state", ["fresh", "already_verified", "broken"])
def test_every_button_answer_has_the_token_headers(client, session, user, state):
    token = "broken" if state == "broken" else make_verify_token(user)
    if state == "already_verified":
        verified(session, user)

    response = confirm(client, token)

    assert response.status_code == (400 if state == "broken" else 200)
    assert_token_headers(response)


# --- Forged and stale tokens ----------------------------------------------------------------


def test_a_token_for_a_deleted_account(session, engine, user, client):
    token = make_verify_token(user)
    account_service.delete_account(session, session.get(User, user.id))

    assert page(client, token).status_code == 400
    assert confirm(client, token).status_code == 400


def test_a_token_for_a_deleted_account_doesnt_verify_a_new_account_with_its_id(
    session, engine, user, client
):
    # Ids can be reused after a delete (SQLite does). The email in the token stops it
    # unless the new account has the same address, which proves the same inbox anyway.
    token = make_verify_token(user)
    account_service.delete_account(session, session.get(User, user.id))
    newcomer = create_user(session, email="new@example.com", password="z" * 12, full_name="N")

    assert confirm(client, token).status_code == 400
    assert not is_verified(engine, newcomer)


@pytest.mark.parametrize("salt", [RESET_TOKEN_SALT, EMAIL_CHANGE_TOKEN_SALT])
def test_the_button_refuses_a_token_made_for_another_purpose(engine, user, client, salt):
    other_salt = token_serializer(salt).dumps({"user_id": user.id, "email": user.email})

    assert confirm(client, other_salt).status_code == 400
    assert not is_verified(engine, user)


def test_a_token_signed_with_another_key_is_refused(engine, user, client):
    forged = URLSafeTimedSerializer("not the secret", salt=VERIFY_TOKEN_SALT).dumps(
        {"user_id": user.id, "email": user.email}
    )

    assert page(client, forged).status_code == 400
    assert confirm(client, forged).status_code == 400
    assert not is_verified(engine, user)


def test_a_token_with_another_users_id_is_refused(session, engine, user, other, client):
    mine = make_verify_token(user)
    theirs = make_verify_token(other)
    # Their data with my signature. A token is "payload.timestamp.signature".
    forged = theirs.rsplit(".", 2)[0] + "." + ".".join(mine.rsplit(".", 2)[1:])

    assert confirm(client, forged).status_code == 400
    assert not is_verified(engine, other)


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": "1"},
        {"user_id": None},
        {"user_id": True},  # True is an int in Python; it must not mean user 1
        {"email": None},
        {"email": [OTHER_EMAIL]},
        {"email": "someone@example.com"},
    ],
    ids=str,
)
def test_signed_tokens_with_wrong_data_are_refused(engine, user, client, change):
    data = {"user_id": user.id, "email": user.email} | change
    token = token_serializer(VERIFY_TOKEN_SALT).dumps(data)

    assert page(client, token).status_code == 400
    assert confirm(client, token).status_code == 400
    assert not is_verified(engine, user)


@pytest.mark.parametrize("data", [[1, "jana@example.com"], "jana@example.com", 1, None])
def test_a_signed_non_dict_token_is_refused(engine, user, client, data):
    token = token_serializer(VERIFY_TOKEN_SALT).dumps(data)

    assert page(client, token).status_code == 400
    assert confirm(client, token).status_code == 400
    assert not is_verified(engine, user)


def test_a_too_long_token_on_the_button_is_refused(engine, user, client):
    assert confirm(client, make_verify_token(user) + "a" * 600).status_code == 400
    assert not is_verified(engine, user)


def test_a_token_in_the_query_of_the_post_is_not_used(engine, user, client):
    response = client.post(
        "/verify-email",
        params={"token": make_verify_token(user)},
        data={"csrf_token": csrf_token(client)},
    )

    assert response.status_code == 400
    assert not is_verified(engine, user)


@pytest.mark.parametrize("bad_first", [True, False])
def test_a_repeated_token_field_is_handled_cleanly(engine, user, client, bad_first):
    good = make_verify_token(user)
    tokens = ["garbage", good] if bad_first else [good, "garbage"]

    response = client.post(
        "/verify-email",
        content=urlencode([("token", t) for t in tokens] + [("csrf_token", csrf_token(client))]),
        headers=FORM,
    )

    # Whichever value is used, the answer and the database agree.
    assert response.status_code == (200 if is_verified(engine, user) else 400)


def test_a_json_body_verifies_nobody(engine, user, client):
    response = client.post(
        "/verify-email",
        json={"token": make_verify_token(user)},
        headers={"X-CSRF-Token": csrf_token(client)},
    )

    assert response.status_code in (400, 422)
    assert not is_verified(engine, user)


# --- Other users' sessions ------------------------------------------------------------------


def test_the_button_verifies_the_tokens_owner_not_the_signed_in_user(client, engine, user, other):
    # B opens A's link in B's browser (a shared computer): A's address is proven, not B's.
    log_in(client, email=OTHER_EMAIL, password=OTHER_PASSWORD)

    response = confirm(client, make_verify_token(user))

    assert response.status_code == 200
    assert is_verified(engine, user)
    assert not is_verified(engine, other)


def test_the_button_doesnt_switch_the_session_to_the_tokens_owner(client, user, other):
    log_in(client, email=OTHER_EMAIL, password=OTHER_PASSWORD)

    confirm(client, make_verify_token(user))

    # Still B (unverified), so B's banner is still there.
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "Verify your email to unlock AI features." in home.text


def test_the_button_doesnt_sign_anyone_in(client, user):
    confirm(client, make_verify_token(user))

    assert client.get("/", follow_redirects=False).status_code in (302, 303, 307)


def test_the_confirm_page_shows_the_tokens_owner_to_another_user(client, user, other):
    log_in(client, email=OTHER_EMAIL, password=OTHER_PASSWORD)

    response = page(client, make_verify_token(user))

    assert response.status_code == 200
    assert f"<b>{user.email}</b>" in response.text
    assert OTHER_EMAIL not in response.text


def test_the_confirm_page_carries_the_token_and_a_csrf_field(client, user):
    token = make_verify_token(user)

    text = page(client, token).text

    assert f'name="token" value="{token}"' in text
    assert f'name="csrf_token" value="{csrf_token(client)}"' in text


# --- The resend button on the invalid link page ---------------------------------------------


def test_invalid_link_offers_no_resend_when_signed_out(client, user):
    for response in (page(client, "broken"), confirm(client, "broken")):
        assert response.status_code == 400
        assert 'action="/resend-verification"' not in response.text
        assert "Sign in to ask for a new one." in response.text


def test_invalid_link_offers_no_resend_to_a_verified_user(auth_client, session, user):
    verified(session, user)

    for response in (page(auth_client, "broken"), confirm(auth_client, "broken")):
        assert response.status_code == 400
        assert 'action="/resend-verification"' not in response.text
        assert "Ask for a new one below." not in response.text
        assert "Sign in to ask for a new one." not in response.text


def test_invalid_button_offers_resend_to_an_unverified_user(auth_client):
    response = confirm(auth_client, "broken")

    assert response.status_code == 400
    assert 'action="/resend-verification"' in response.text
    assert "Ask for a new one below." in response.text


# --- Escaping -------------------------------------------------------------------------------


HOSTILE = "<img src=x onerror=alert(1)>@example.com"


@pytest.mark.parametrize("email", [HOSTILE, "o'neil&co@example.com", '"quoted"@example.com'])
def test_the_confirm_page_escapes_the_address(session, user, client, email):
    # Saved directly, so this checks the template, not the address rule.
    user.email = email
    session.add(user)
    session.commit()

    response = page(client, make_verify_token(user))

    assert response.status_code == 200
    assert email not in response.text
    # Jinja escapes quotes as &#39; / &#34;, so compare after unescaping.
    assert f"<b>{email}</b>" in html.unescape(response.text)


def test_a_hostile_token_in_the_query_is_not_echoed(client):
    hostile = '"><script>alert(1)</script>'

    response = page(client, hostile)

    assert response.status_code == 400
    assert hostile not in response.text
    assert "<script>alert(1)" not in response.text


# --- Rate limits ----------------------------------------------------------------------------


def test_the_button_limit_is_separate_from_the_page_limit(client, engine, user):
    token = make_verify_token(user)
    for _ in range(30):
        page(client, token)
    assert page(client, token).status_code == 429

    assert confirm(client, token).status_code == 200
    assert is_verified(engine, user)


def test_the_button_limit_holds_for_a_signed_in_user(auth_client, engine, user):
    csrf = csrf_token(auth_client)
    codes = [
        auth_client.post("/verify-email", data={"token": "x", "csrf_token": csrf}).status_code
        for _ in range(11)
    ]

    assert codes[:10] == [400] * 10
    assert codes[10] == 429


def test_a_new_browser_shares_the_ip_limit(client, engine, user):
    # The limit is per IP, not per session: a fresh browser doesn't reset it.
    csrf = csrf_token(client)
    for _ in range(10):
        client.post("/verify-email", data={"token": "x", "csrf_token": csrf})

    assert confirm(TestClient(app), make_verify_token(user)).status_code == 429
    assert not is_verified(engine, user)
