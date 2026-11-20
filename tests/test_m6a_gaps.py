"""Gaps in the M6a tests: the extension API and Settings › Browser extension
(SPEC §8; D-136, D-137, D-138, D-140, D-141)."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.main import app
from app.models import ApiToken, Application, User
from app.services import application_service, auth_service, token_service
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token
from tests.test_ext_api import LINK, ORIGIN, api_login, bearer, error_code, job

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


@pytest.fixture
def token(client, user) -> str:
    return api_login(client).json()["token"]


@pytest.fixture
def other(session) -> User:
    return create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Omar")


@pytest.fixture
def other_token(client, other) -> str:
    return api_login(client, email=OTHER_EMAIL, password=OTHER_PASSWORD).json()["token"]


def htmx(client) -> dict:
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


# --- One user's token never reaches another user's data ---------------------------------


def test_a_saved_job_belongs_to_the_tokens_user(client, user, other, token, other_token, session):
    # A user_id in the body is ignored: the owner always comes from the token.
    response = client.post("/api/ext/jobs", json=job(user_id=other.id), headers=bearer(token))

    assert response.status_code == 201
    saved = session.exec(select(Application)).one()
    assert saved.user_id == user.id
    found = client.post("/api/ext/jobs/check", json={"url": LINK}, headers=bearer(other_token))
    assert found.json() == {"exists": False}


def test_each_token_finds_only_its_own_copy_of_a_job(client, user, other, token, other_token):
    mine = client.post("/api/ext/jobs", json=job(), headers=bearer(token)).json()
    theirs = client.post("/api/ext/jobs", json=job(), headers=bearer(other_token)).json()

    def check(raw):
        return client.post("/api/ext/jobs/check", json={"url": LINK}, headers=bearer(raw)).json()

    assert check(token)["application_id"] == mine["application_id"]
    assert check(other_token)["application_id"] == theirs["application_id"]


def test_me_shows_the_tokens_user(client, token, other_token):
    assert client.get("/api/ext/me", headers=bearer(token)).json()["email"] == USER_EMAIL
    assert client.get("/api/ext/me", headers=bearer(other_token)).json()["email"] == OTHER_EMAIL


# --- When the token stops working ------------------------------------------------------


def test_a_deleted_users_token_is_unauthorized(client, user, token, engine):
    client.post(
        "/login",
        data={"email": USER_EMAIL, "password": USER_PASSWORD, "csrf_token": csrf_token(client)},
    )
    response = client.post(
        "/settings/delete-account",
        data={"password": USER_PASSWORD, "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401
    assert client.post("/api/ext/jobs", json=job(), headers=bearer(token)).status_code == 401
    with Session(engine) as check:
        assert check.exec(select(ApiToken)).all() == []
        assert check.exec(select(Application)).all() == []


def test_confirming_a_new_email_signs_the_extension_out(client, user, token, session):
    link = auth_service.make_email_change_token(user, "jana.new@example.com")
    # Another browser, not signed in, opens the link from the email (D-134).
    browser = TestClient(app)
    browser.get("/confirm-email", params={"token": link})
    response = browser.post(
        "/confirm-email",
        data={"token": link, "csrf_token": csrf_token(browser)},
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)
    session.refresh(user)
    assert user.email == "jana.new@example.com"

    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401
    # Signing in again with the new address gives a working token.
    fresh = api_login(client, email="jana.new@example.com").json()["token"]
    assert client.get("/api/ext/me", headers=bearer(fresh)).status_code == 200


def test_signing_out_twice_is_unauthorized_the_second_time(client, token):
    assert client.post("/api/ext/auth/logout", headers=bearer(token)).status_code == 200

    response = client.post("/api/ext/auth/logout", headers=bearer(token))

    assert response.status_code == 401
    assert error_code(response) == "unauthorized"


def test_a_web_page_cannot_sign_the_extension_out(client, token):
    headers = {"Authorization": f"Bearer {token}", "Origin": "https://evil.example"}

    assert client.post("/api/ext/auth/logout", headers=headers).status_code == 403
    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 200


# --- Bodies that are not what we expect --------------------------------------------------


@pytest.mark.parametrize("body", [b"[]", b'"Acme"', b"null", b"42", b"{not json", b""])
def test_a_body_that_is_not_a_json_object_is_refused(client, token, session, body):
    response = client.post(
        "/api/ext/jobs",
        content=body,
        headers={**bearer(token), "Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert session.exec(select(Application)).all() == []


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_a_job_sent_with_another_content_type_is_not_saved(client, token, session, content_type):
    # text/plain and form bodies skip the browser's CORS preflight; they must not
    # be read as JSON.
    response = client.post(
        "/api/ext/jobs",
        content=b'{"company": "Acme", "job_title": "Dev", "url": "https://acme.com/jobs/1"}',
        headers={**bearer(token), "Content-Type": content_type},
    )

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert session.exec(select(Application)).all() == []


def test_the_error_message_never_echoes_the_values_sent(client, token):
    response = client.post(
        "/api/ext/jobs", json=job(url="javascript:alert('secret')"), headers=bearer(token)
    )

    assert response.status_code == 422
    assert "secret" not in response.text
    assert "url" in response.json()["error"]["message"]


# --- Origin ------------------------------------------------------------------------------


def test_the_origin_may_end_with_a_slash(client, token):
    headers = {"Authorization": f"Bearer {token}", "Origin": ORIGIN + "/"}
    assert client.get("/api/ext/me", headers=headers).status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        # Browsers always send the origin in lower case, so another case is not ours.
        "Chrome-Extension://TestExtensionId",
        "chrome-extension://testextensionid.evil.example",
        "chrome-extension://testextensionid/path",
        "chrome-extension://testextensionid,https://evil.example",
        " chrome-extension://testextensionid",
        "",
    ],
)
def test_origins_that_only_look_like_ours_are_refused(client, token, session, origin):
    headers = {"Authorization": f"Bearer {token}", "Origin": origin}

    response = client.post("/api/ext/jobs", json=job(), headers=headers)

    assert response.status_code == 403
    assert error_code(response) == "forbidden"
    assert session.exec(select(Application)).all() == []


def test_the_origin_is_checked_before_the_token(client):
    # A web page learns nothing about tokens: 403, not 401.
    response = client.get(
        "/api/ext/me", headers={"Authorization": "Bearer nonsense", "Origin": "https://x.example"}
    )
    assert response.status_code == 403


# --- The guard only covers /api/ext/ ----------------------------------------------------------


def test_a_big_form_post_outside_the_api_is_not_cut_off(client, user):
    # 300 KB is over the API's 256 KB limit; the login form answers as before.
    response = client.post(
        "/login",
        data={"email": USER_EMAIL, "password": "x" * 300_000, "csrf_token": csrf_token(client)},
        headers={"Origin": "https://some.site"},
        follow_redirects=False,
    )

    assert response.status_code != 413
    assert response.headers["content-type"].startswith("text/html")


def test_a_web_origin_is_fine_outside_the_api(auth_client):
    response = auth_client.get("/settings", headers={"Origin": "https://some.site"})
    assert response.status_code == 200


# --- Rate limit ------------------------------------------------------------------------------


def test_one_users_rate_limit_does_not_block_another(client, token, other_token):
    for _ in range(60):
        client.get("/api/ext/me", headers=bearer(token))
    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 429

    assert client.get("/api/ext/me", headers=bearer(other_token)).status_code == 200


def test_a_burst_on_one_route_does_not_block_saving(client, token):
    # Each route has its own 60/minute per token.
    for _ in range(60):
        client.get("/api/ext/me", headers=bearer(token))

    assert client.post("/api/ext/jobs", json=job(), headers=bearer(token)).status_code == 201


def test_a_saved_job_over_the_rate_limit_is_not_saved(client, token, session):
    for _ in range(60):
        client.post("/api/ext/jobs", json=job(), headers=bearer(token))

    response = client.post("/api/ext/jobs", json=job(), headers=bearer(token))

    assert response.status_code == 429
    assert error_code(response) == "rate_limited"
    assert len(session.exec(select(Application)).all()) == 60


# --- Settings › Browser extension ---------------------------------------------------------------


def test_revoking_twice_is_harmless(auth_client, session, user):
    raw, token = token_service.issue(session, user)
    url = f"/settings/extension/tokens/{token.id}/revoke"
    assert auth_client.post(url, headers=htmx(auth_client)).status_code == 200

    # A second click (another tab still shows the row): the same answer, the list
    # without it.
    response = auth_client.post(url, headers=htmx(auth_client))

    assert response.status_code == 200
    assert url not in response.text
    session.expire_all()
    assert token_service.check(session, raw) is None


def test_a_get_on_the_revoke_url_changes_nothing(auth_client, session, user):
    raw, token = token_service.issue(session, user)

    response = auth_client.get(f"/settings/extension/tokens/{token.id}/revoke")

    assert response.status_code == 405
    assert token_service.check(session, raw) is not None


def test_settings_never_lists_another_users_sign_ins(auth_client, session, other):
    _, theirs = token_service.issue(session, other)

    html = auth_client.get("/settings").text

    assert f"/settings/extension/tokens/{theirs.id}/revoke" not in html
    assert "No devices connected." in html


def test_revoke_answers_only_with_the_users_own_sign_ins(auth_client, session, user, other):
    _, mine = token_service.issue(session, user)
    _, kept = token_service.issue(session, user)
    _, theirs = token_service.issue(session, other)

    response = auth_client.post(
        f"/settings/extension/tokens/{mine.id}/revoke", headers=htmx(auth_client)
    )

    assert response.status_code == 200
    assert f"/settings/extension/tokens/{kept.id}/revoke" in response.text
    assert f"/settings/extension/tokens/{mine.id}/revoke" not in response.text
    assert f"/settings/extension/tokens/{theirs.id}/revoke" not in response.text


def test_check_finds_a_job_saved_on_the_web_with_tracking_in_the_link(client, user, token, session):
    # Saved on the web with the same link but different tracking: check finds it.
    saved = application_service.create(
        session, user, company="Acme", job_title="Dev", job_url=LINK + "&utm_source=x"
    )
    found = client.post("/api/ext/jobs/check", json={"url": LINK}, headers=bearer(token)).json()
    assert found["application_id"] == saved.id
