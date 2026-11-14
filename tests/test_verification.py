"""Email verification (SPEC §6.1, M1b acceptance criteria)."""

import re
from datetime import timedelta
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app.db import get_session
from app.models import User
from app.models.base import utcnow
from app.services.auth_service import (
    LoginRequired,
    login_required_response,
    make_verify_token,
    require_verified,
    start_session,
)
from tests.helpers import USER_EMAIL, csrf_token


def register(client, email="ana@example.com"):
    form = {
        "full_name": "Ana Silva",
        "email": email,
        "password": "a long enough password",
        "privacy": "yes",
        "csrf_token": csrf_token(client),
    }
    return client.post("/register", data=form, follow_redirects=False)


def link_path(email) -> str:
    """The /verify-email?token=... part of the link in an email."""
    return re.search(r"https?://[^/\s]+(/verify-email\?token=\S+)", email.body).group(1)


def resend(client, htmx=False):
    headers = {"HX-Request": "true"} if htmx else {}
    return client.post(
        "/resend-verification",
        data={"csrf_token": csrf_token(client)},
        headers=headers,
        follow_redirects=False,
    )


# --- Register sends the email -----------------------------------------------------------


def test_register_sends_a_verification_email(client, outbox):
    response = register(client)

    assert response.headers["location"] == "/check-inbox"
    assert len(outbox) == 1
    assert outbox[0].to == "ana@example.com"
    assert outbox[0].subject == "Verify your email for Unfurl"
    assert "Hi Ana Silva," in outbox[0].body
    assert link_path(outbox[0]).startswith("/verify-email?token=")


def test_register_with_errors_sends_no_email(client, outbox, user):
    register(client, email=USER_EMAIL)  # email taken
    assert outbox == []


# --- Check your inbox -------------------------------------------------------------------


def test_check_inbox_page(client, outbox):
    register(client)
    response = client.get("/check-inbox")

    assert response.status_code == 200
    assert "Check your inbox" in response.text
    assert "<b>ana@example.com</b>" in response.text
    assert ">Continue to setup</a>" in response.text
    assert 'action="/resend-verification"' in response.text
    assert 'aria-current="page"' not in response.text  # no Sign in / Create account tabs


def test_check_inbox_needs_login(client):
    response = client.get("/check-inbox", follow_redirects=False)
    assert response.headers["location"] == "/login?next=/check-inbox"


def test_check_inbox_sends_verified_users_home(auth_client, session, user):
    user.is_verified = True
    session.add(user)
    session.commit()
    response = auth_client.get("/check-inbox", follow_redirects=False)
    assert response.headers["location"] == "/"


# --- The link ---------------------------------------------------------------------------


def test_link_verifies_the_email(client, session, outbox):
    register(client)
    response = client.get(link_path(outbox[0]))

    assert response.status_code == 200
    assert "Email verified" in response.text
    user = session.exec(select(User).where(User.email == "ana@example.com")).one()
    session.refresh(user)
    assert user.is_verified


def test_second_click_says_already_verified(client, outbox):
    register(client)
    client.get(link_path(outbox[0]))
    response = client.get(link_path(outbox[0]))

    assert response.status_code == 200
    assert "Email already verified" in response.text


def test_link_works_without_a_session(client, session, user):
    # The email may be opened on another device.
    response = client.get("/verify-email?token=" + make_verify_token(user))
    assert response.status_code == 200
    session.refresh(user)
    assert user.is_verified


def test_link_works_until_48_hours(client, session, user):
    with freeze_time(utcnow()) as frozen:
        token = make_verify_token(user)
        frozen.tick(timedelta(hours=48) - timedelta(seconds=1))
        assert client.get("/verify-email?token=" + token).status_code == 200


def test_expired_link_is_rejected(client, session, user):
    with freeze_time(utcnow()) as frozen:
        token = make_verify_token(user)
        frozen.tick(timedelta(hours=48, seconds=1))
        response = client.get("/verify-email?token=" + token)

    assert response.status_code == 400
    assert "This link has expired or is invalid" in response.text
    assert "Sign in to ask for a new one." in response.text
    session.refresh(user)
    assert not user.is_verified


@pytest.mark.parametrize(
    "query", ["", "?token=", "?token=not-a-token", "?token=" + "a" * 513], ids=str
)
def test_missing_or_broken_token_is_rejected(client, query):
    response = client.get("/verify-email" + query)
    assert response.status_code == 400
    assert "This link has expired or is invalid" in response.text


def test_changed_token_is_rejected(client, user):
    token = make_verify_token(user)
    changed = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert client.get("/verify-email?token=" + changed).status_code == 400


def test_token_for_an_old_email_address_is_rejected(client, session, user):
    token = make_verify_token(user)
    user.email = "new@example.com"
    session.add(user)
    session.commit()
    assert client.get("/verify-email?token=" + token).status_code == 400


def test_token_made_for_another_purpose_is_rejected(client, user):
    from itsdangerous import URLSafeTimedSerializer

    from app.config import get_settings

    secret = get_settings().secret_key.get_secret_value()
    other = URLSafeTimedSerializer(secret, salt="reset-password").dumps(
        {"user_id": user.id, "email": user.email}
    )
    assert client.get("/verify-email?token=" + other).status_code == 400


def test_invalid_link_offers_resend_when_logged_in(auth_client):
    response = auth_client.get("/verify-email?token=broken")
    assert response.status_code == 400
    assert 'action="/resend-verification"' in response.text


# --- Resend -----------------------------------------------------------------------------


def test_resend_sends_a_new_email(auth_client, outbox):
    response = resend(auth_client)

    assert response.headers["location"] == "/check-inbox?resent=1"
    assert len(outbox) == 1
    assert outbox[0].to == USER_EMAIL
    page = auth_client.get("/check-inbox?resent=1").text
    assert "Sent again. Check your spam folder too." in page


def test_resend_with_htmx_returns_the_message(auth_client, outbox):
    response = resend(auth_client, htmx=True)

    assert response.status_code == 200
    assert "Sent again. Check your spam folder too." in response.text
    assert "<html" not in response.text  # only the piece that replaces the button
    assert len(outbox) == 1


def test_resend_does_nothing_for_verified_users(auth_client, session, user, outbox):
    user.is_verified = True
    session.add(user)
    session.commit()
    resend(auth_client)
    assert outbox == []


def test_resend_needs_login_and_csrf(client, auth_client):
    assert client.post("/resend-verification", follow_redirects=False).status_code == 403
    no_token = auth_client.post("/resend-verification", follow_redirects=False)
    assert no_token.status_code == 403


# --- Banner -----------------------------------------------------------------------------


def test_banner_is_shown_until_verified(auth_client, session, user):
    html = auth_client.get("/").text
    assert "Verify your email to unlock AI features." in html
    assert 'hx-post="/resend-verification"' in html

    user.is_verified = True
    session.add(user)
    session.commit()
    assert "Verify your email" not in auth_client.get("/").text


# --- require_verified -------------------------------------------------------------------
# Tested on a small separate app: a test route on the real app would end up in its
# cached OpenAPI schema and break test_every_page_except_the_public_ones_needs_login.


@pytest.fixture
def verified_only_client(engine) -> TestClient:
    test_app = FastAPI()
    test_app.add_middleware(SessionMiddleware, secret_key="test-secret")
    test_app.add_exception_handler(LoginRequired, login_required_response)

    def test_session():
        with Session(engine) as session:
            yield session

    test_app.dependency_overrides[get_session] = test_session

    @test_app.get("/sign-in-as/{user_id}")
    def sign_in_as(user_id: int, request: Request):
        with Session(engine) as session:
            start_session(request, session.get(User, user_id))
        return {}

    @test_app.get("/verified-only")
    def verified_only(user: Annotated[User, Depends(require_verified)]):
        return {"email": user.email}

    return TestClient(test_app)


def test_require_verified_rejects_unverified_users(verified_only_client, user):
    verified_only_client.get(f"/sign-in-as/{user.id}")
    assert verified_only_client.get("/verified-only").status_code == 403


def test_require_verified_lets_verified_users_in(verified_only_client, session, user):
    user.is_verified = True
    session.add(user)
    session.commit()
    verified_only_client.get(f"/sign-in-as/{user.id}")

    response = verified_only_client.get("/verified-only")
    assert response.status_code == 200
    assert response.json() == {"email": USER_EMAIL}


def test_require_verified_needs_login(verified_only_client):
    response = verified_only_client.get("/verified-only", follow_redirects=False)
    assert response.headers["location"] == "/login?next=/verified-only"
