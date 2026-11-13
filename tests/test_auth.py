"""Register, log in and log out (SPEC §6.1, M1a acceptance criteria)."""

import pytest
from sqlmodel import select

from app.billing.entitlements import effective_plan
from app.models import User
from app.services.auth_service import PASSWORD_MAX_LENGTH, SESSION_USER_ID
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in


def register(client, **overrides):
    form = {
        "full_name": "Ana Silva",
        "email": "ana@example.com",
        "password": "a long enough password",
        "privacy": "yes",
        "csrf_token": csrf_token(client),
    }
    return client.post("/register", data=form | overrides, follow_redirects=False)


def logged_in_user_id(client) -> int | None:
    """Read the user id from the signed session cookie, the way the app does."""
    import base64
    import json

    cookie = client.cookies.get("unfurl_session")
    if cookie is None:
        return None
    payload = cookie.split(".")[0]
    data = json.loads(base64.b64decode(payload + "=" * (-len(payload) % 4)))
    return data.get(SESSION_USER_ID)


# --- Pages ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "heading"), [("/login", "Welcome back"), ("/register", "Create your free account")]
)
def test_auth_pages_render_with_csrf_field(client, url, heading):
    response = client.get(url)
    assert response.status_code == 200
    assert heading in response.text
    assert 'name="csrf_token"' in response.text
    assert 'aria-current="page"' in response.text  # the active tab


def test_no_forgot_password_link_in_m1a(client):
    # It comes together with the reset page in M1b.
    for url in ("/login", "/register"):
        assert "forgot" not in client.get(url).text.lower()


# --- Register ---------------------------------------------------------------------------


def test_register_logs_in_and_goes_home_on_the_free_plan(client, session):
    response = register(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    user = session.exec(select(User).where(User.email == "ana@example.com")).one()
    assert user.full_name == "Ana Silva"
    assert user.privacy_accepted_at is not None
    assert user.password_hash.startswith("$argon2")
    assert effective_plan(user) == "free"
    assert logged_in_user_id(client) == user.id


def test_register_stores_the_email_lowercase(client, session):
    register(client, email="  Ana@Example.COM ")
    assert session.exec(select(User).where(User.email == "ana@example.com")).one()


@pytest.mark.parametrize("email", [USER_EMAIL, USER_EMAIL.upper()])
def test_register_with_existing_email_says_so(client, user, email):
    response = register(client, email=email)

    assert response.status_code == 400
    assert "An account with this email already exists." in response.text
    assert '<a href="/login"' in response.text
    assert "forgot" not in response.text.lower()
    assert logged_in_user_id(client) is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("full_name", "   ", "Enter your full name."),
        ("full_name", "x" * 201, "Name must be at most 200 characters."),
        ("email", "not-an-email", "Enter a valid email address."),
        ("email", "a" * 310 + "@example.com", "Enter a valid email address."),
        ("password", "too short", "Password must be at least 10 characters."),
        ("password", "x" * (PASSWORD_MAX_LENGTH + 1), "Password must be at most 1024 characters."),
        ("privacy", "", "Please accept the privacy policy and terms to continue."),
    ],
)
def test_register_rejects_invalid_input(client, session, field, value, message):
    response = register(client, **{field: value})

    assert response.status_code == 400
    assert message in response.text
    assert 'aria-invalid="true"' in response.text
    assert session.exec(select(User)).first() is None


def test_register_accepts_password_of_exactly_the_max_length(client):
    assert register(client, password="x" * PASSWORD_MAX_LENGTH).status_code == 303


def test_register_form_keeps_values_but_never_the_password(client):
    response = register(client, email="not-an-email", password="my secret password")
    assert 'value="Ana Silva"' in response.text
    assert 'value="not-an-email"' in response.text
    assert "my secret password" not in response.text


def test_register_without_csrf_token_is_forbidden(client, session):
    response = client.post(
        "/register",
        data={
            "full_name": "Ana",
            "email": "ana@example.com",
            "password": "x" * 12,
            "privacy": "yes",
        },
    )
    assert response.status_code == 403
    assert session.exec(select(User)).first() is None


# --- Log in -----------------------------------------------------------------------------


def test_login_with_right_password_goes_home(client, user, session):
    response = log_in(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert logged_in_user_id(client) == user.id
    session.refresh(user)
    assert user.last_login_at is not None


def test_login_email_is_case_insensitive(client, user):
    assert log_in(client, email="  JANA@Example.com ").status_code == 303


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (USER_EMAIL, "wrong password"),
        ("nobody@example.com", USER_PASSWORD),
        (USER_EMAIL, ""),
        (USER_EMAIL, "x" * (PASSWORD_MAX_LENGTH + 1)),
    ],
)
def test_failed_login_shows_one_general_message(client, user, email, password):
    response = log_in(client, email=email, password=password)

    assert response.status_code == 400
    assert "Invalid email or password." in response.text
    assert logged_in_user_id(client) is None


def test_failed_login_keeps_the_email_but_not_the_password(client, user):
    response = log_in(client, password="wrong password")
    assert f'value="{USER_EMAIL}"' in response.text
    assert "wrong password" not in response.text


def test_login_without_csrf_token_is_forbidden(client, user):
    response = client.post("/login", data={"email": USER_EMAIL, "password": USER_PASSWORD})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [
        ("/settings", "/settings"),
        ("/documents?tab=cv", "/documents?tab=cv"),
        ("//evil.com", "/"),
        ("https://evil.com", "/"),
        ("/\\evil.com", "/"),
        ("javascript:alert(1)", "/"),
    ],
)
def test_login_goes_to_next_only_if_it_is_on_this_site(client, user, next_url, expected):
    response = client.post(
        "/login",
        data={
            "email": USER_EMAIL,
            "password": USER_PASSWORD,
            "next": next_url,
            "csrf_token": csrf_token(client),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == expected


def test_login_page_keeps_a_safe_next_in_the_form(client):
    assert 'name="next" value="/settings"' in client.get("/login?next=/settings").text
    assert 'name="next" value="/"' in client.get("/login?next=https://evil.com").text


def test_login_gives_a_new_session(client, user):
    # Session fixation: the token from before login must not stay valid after it.
    token_before = csrf_token(client)
    log_in(client)
    assert csrf_token(client) != token_before


# --- Already logged in, log out, session_version -----------------------------------------


@pytest.mark.parametrize("url", ["/login", "/register"])
def test_logged_in_user_skips_login_and_register(auth_client, url):
    response = auth_client.get(url, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_logout_ends_the_session(auth_client):
    response = auth_client.post(
        "/logout", data={"csrf_token": csrf_token(auth_client)}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert logged_in_user_id(auth_client) is None


def test_logout_needs_post_and_a_csrf_token(auth_client, user):
    assert auth_client.get("/logout").status_code == 405
    assert auth_client.post("/logout").status_code == 403
    assert logged_in_user_id(auth_client) == user.id


def test_session_ends_when_session_version_changes(auth_client, user, session):
    # What a password change or reset does (D-021, used in M1b).
    user.session_version += 1
    session.add(user)
    session.commit()

    response = auth_client.get("/login", follow_redirects=False)
    assert response.status_code == 200  # the login page, not a redirect: no longer logged in
    assert logged_in_user_id(auth_client) is None


def test_session_ends_when_the_user_is_gone(auth_client, user, session):
    session.delete(user)
    session.commit()
    assert auth_client.get("/login", follow_redirects=False).status_code == 200
