"""The extension's JSON API (SPEC §8; D-136, D-137, D-138, D-141)."""

import datetime as dt

import pytest
from freezegun import freeze_time
from sqlmodel import select

from app.models import ApiToken, Application
from app.security import limiter
from app.services import application_service, token_service
from app.services.auth_service import create_user
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

ORIGIN = "chrome-extension://testextensionid"  # EXTENSION_ORIGINS in tests/conftest.py
LINK = "https://www.linkedin.com/jobs/view/3912345678/?trk=abc"


def api_login(client, email=USER_EMAIL, password=USER_PASSWORD):
    return client.post(
        "/api/ext/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": ORIGIN},
    )


@pytest.fixture
def token(client, user) -> str:
    response = api_login(client)
    assert response.status_code == 200
    return response.json()["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Origin": ORIGIN}


def job(**fields) -> dict:
    return {"company": "Acme", "job_title": "Backend Developer", "url": LINK, **fields}


def error_code(response) -> str:
    return response.json()["error"]["code"]


# --- Sign in ------------------------------------------------------------------------


def test_sign_in_gives_a_token_once_and_stores_only_its_hash(client, user, session):
    response = api_login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"full_name": "Jana Novak", "email": USER_EMAIL}
    assert dt.datetime.fromisoformat(body["expires_at"])
    stored = session.exec(select(ApiToken)).one()
    assert stored.token_hash == token_service.hash_token(body["token"]) != body["token"]
    assert "set-cookie" not in response.headers


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(client, user):
    wrong = api_login(client, password="not the password")
    unknown = api_login(client, email="nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json() == {
        "error": {"code": "unauthorized", "message": "Invalid email or password."}
    }


def test_sign_in_is_locked_after_five_failures(client, user):
    for _ in range(5):
        api_login(client, password="wrong password")
    limiter.reset()  # so the 5/minute limit doesn't answer first

    response = api_login(client)  # even the right password

    assert response.status_code == 429
    assert response.json() == {
        "error": {"code": "rate_limited", "message": "Too many attempts. Try again in 15 minutes."}
    }


def test_sign_in_is_limited_to_five_a_minute(client, user):
    for _ in range(5):
        assert api_login(client).status_code == 200

    response = api_login(client)

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert error_code(response) == "rate_limited"


def test_an_unverified_account_gets_a_token(client, user):
    assert not user.is_verified
    assert api_login(client).status_code == 200


def test_a_bad_body_is_a_validation_error(client, user):
    response = client.post("/api/ext/auth/login", json={"email": USER_EMAIL})

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert "password" in response.json()["error"]["message"]


# --- The Bearer token ---------------------------------------------------------------


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer nonsense", "Basic abc", "bearer  "])
def test_without_a_working_token_the_api_says_unauthorized(client, user, header):
    headers = {"Authorization": header} if header is not None else {}

    response = client.get("/api/ext/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "Please sign in again."}
    }


def test_the_session_cookie_is_not_enough(auth_client):
    # Logged in on the web, but no Bearer token: the API never reads the cookie.
    response = auth_client.get("/api/ext/me")
    assert response.status_code == 401
    response = auth_client.post("/api/ext/jobs", json=job())
    assert response.status_code == 401


def test_an_expired_token_is_unauthorized(client, user):
    with freeze_time("2026-01-01 12:00:00"):
        raw = api_login(client).json()["token"]
    with freeze_time("2026-04-02 12:00:00"):
        assert client.get("/api/ext/me", headers=bearer(raw)).status_code == 401


def test_a_revoked_token_is_unauthorized(client, token, session):
    stored = session.exec(select(ApiToken)).one()
    token_service.revoke(session, stored)

    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401


def test_signing_out_on_the_web_signs_the_extension_out(client, user, token):
    assert log_in(client).status_code == 303
    client.post("/logout", data={"csrf_token": csrf_token(client)})

    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401


def test_changing_the_password_signs_the_extension_out(client, user, token):
    log_in(client)
    response = client.post(
        "/settings/account/password",
        data={
            "current_password": USER_PASSWORD,
            "new_password": "another long password",
            "repeat_password": "another long password",
            "csrf_token": csrf_token(client),
        },
    )
    assert response.status_code == 200

    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401


def test_signing_out_in_the_extension_revokes_only_that_token(client, user, token):
    other = api_login(client).json()["token"]

    response = client.post("/api/ext/auth/logout", headers=bearer(token))

    assert response.status_code == 200
    assert client.get("/api/ext/me", headers=bearer(token)).status_code == 401
    assert client.get("/api/ext/me", headers=bearer(other)).status_code == 200


# --- Origin and body size -------------------------------------------------------------


def test_requests_from_web_pages_are_refused(client, token):
    for origin in ("https://evil.example", "null", "chrome-extension://otherextension"):
        response = client.get(
            "/api/ext/me", headers={"Authorization": f"Bearer {token}", "Origin": origin}
        )
        assert response.status_code == 403
        assert error_code(response) == "forbidden"


def test_requests_without_an_origin_still_need_the_token(client, token):
    assert client.get("/api/ext/me", headers={"Authorization": f"Bearer {token}"}).is_success
    assert client.get("/api/ext/me").status_code == 401


def test_no_cors_headers_are_sent(client, token):
    response = client.get("/api/ext/me", headers=bearer(token))
    assert not any(name.startswith("access-control-") for name in response.headers)


def test_a_body_over_256_kb_is_refused(client, token):
    response = client.post(
        "/api/ext/jobs",
        content=b'{"x": "' + b"a" * (256 * 1024) + b'"}',
        headers={**bearer(token), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert error_code(response) == "validation_error"


def test_a_body_over_256_kb_without_a_length_is_refused(client, token):
    def chunks():
        for _ in range(300):
            yield b"a" * 1024

    response = client.post(
        "/api/ext/jobs",
        content=chunks(),
        headers={**bearer(token), "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_an_unknown_api_path_is_a_json_404(client):
    response = client.get("/api/ext/nothing-here")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


# --- /me ---------------------------------------------------------------------------------


def test_me(client, token):
    response = client.get("/api/ext/me", headers=bearer(token))

    assert response.json() == {
        "full_name": "Jana Novak",
        "email": USER_EMAIL,
        "is_verified": False,
        "credits_remaining": None,  # M6b
        "using_byok": None,
    }


# --- Already saved? ----------------------------------------------------------------------


def check(client, token, url):
    response = client.post("/api/ext/jobs/check", json={"url": url}, headers=bearer(token))
    assert response.status_code == 200
    return response.json()


def test_check_finds_a_saved_job_by_its_canonical_link(client, user, token, session):
    saved = application_service.create(
        session, user, company="Acme", job_title="Dev", job_url=LINK, status="applied"
    )

    other_form = "https://at.linkedin.com/jobs/search/?currentJobId=3912345678&keywords=x"
    assert check(client, token, other_form) == {
        "exists": True,
        "application_id": saved.id,
        "status": "applied",
        "app_url": f"http://localhost:8000/applications/{saved.id}",
    }
    assert check(client, token, "https://linkedin.com/jobs/view/111") == {"exists": False}
    assert check(client, token, "not a link") == {"exists": False}


def test_check_counts_archived_jobs_and_gives_the_newest(client, user, token, session):
    with freeze_time("2026-11-01"):
        older = application_service.create(
            session, user, company="Acme", job_title="Dev", job_url=LINK
        )
    newer = application_service.create(session, user, company="Acme", job_title="Dev", job_url=LINK)
    application_service.archive(session, user, newer)

    found = check(client, token, LINK)

    assert found["application_id"] == newer.id != older.id


def test_check_never_shows_another_users_job(client, user, token, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    application_service.create(session, other, company="Acme", job_title="Dev", job_url=LINK)

    assert check(client, token, LINK) == {"exists": False}


def test_a_new_link_in_details_moves_the_match(client, user, token, session):
    saved = application_service.create(
        session, user, company="Acme", job_title="Dev", job_url="https://acme.com/jobs?id=1"
    )
    values = {field: getattr(saved, field) for field in application_service.DETAIL_FIELDS}
    application_service.update_details(
        session, user, saved, {**values, "job_url": "https://acme.com/jobs?id=2"}
    )

    assert check(client, token, "https://acme.com/jobs?id=2")["exists"]
    assert not check(client, token, "https://acme.com/jobs?id=1")["exists"]


# --- Saving a job ----------------------------------------------------------------------------


def test_saving_a_job(client, user, token, session):
    response = client.post(
        "/api/ext/jobs",
        json=job(
            location="Vienna",
            remote_type="hybrid",
            salary_text="ab € 4.000 brutto/Monat",
            posting_closes_at=(dt.date.today() + dt.timedelta(days=10)).isoformat(),
            job_text="We are looking for …",
            site="indeed",  # ignored: the source comes from the link (D-141)
        ),
        headers=bearer(token),
    )

    assert response.status_code == 201
    saved = session.exec(select(Application)).one()
    assert response.json() == {
        "application_id": saved.id,
        "app_url": f"http://localhost:8000/applications/{saved.id}",
    }
    assert (saved.user_id, saved.company, saved.job_title) == (user.id, "Acme", "Backend Developer")
    assert (saved.location, saved.remote_type, saved.status) == ("Vienna", "hybrid", "saved")
    assert saved.salary_text == "ab € 4.000 brutto/Monat"
    assert saved.posting_closes_at == dt.date.today() + dt.timedelta(days=10)
    assert saved.description_raw == "We are looking for …"
    assert saved.source == "linkedin"
    assert saved.canonical_url == "https://linkedin.com/jobs/view/3912345678"
    assert check(client, token, LINK)["application_id"] == saved.id


@freeze_time("2026-11-18 12:30:00")
def test_applied_gets_the_date_where_the_user_is(client, user, token, session):
    user.timezone = "Pacific/Auckland"  # already 19 November there
    session.add(user)
    session.commit()

    client.post("/api/ext/jobs", json=job(status="applied"), headers=bearer(token))

    saved = session.exec(select(Application)).one()
    assert (saved.status, saved.applied_at) == ("applied", dt.date(2026, 11, 19))


@pytest.mark.parametrize(
    "fields",
    [
        {"company": ""},
        {"job_title": "x" * 201},
        {"url": "javascript:alert(1)"},
        {"url": "https://acme.com/a job"},
        {"url": "https://acme.com/" + "a" * 2000},
        {"remote_type": "moon"},
        {"status": "offer"},
        {"posting_closes_at": "1999-01-01"},
        {"job_text": "x" * 50_001},
    ],
)
def test_invalid_jobs_are_refused(client, token, session, fields):
    response = client.post("/api/ext/jobs", json=job(**fields), headers=bearer(token))

    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert session.exec(select(Application)).all() == []


def test_a_job_text_of_50000_characters_is_fine(client, token):
    response = client.post("/api/ext/jobs", json=job(job_text="x" * 50_000), headers=bearer(token))
    assert response.status_code == 201


def test_saving_respects_the_2000_jobs_limit(client, token, monkeypatch):
    monkeypatch.setattr(application_service, "MAX_APPLICATIONS_PER_USER", 1)
    assert client.post("/api/ext/jobs", json=job(), headers=bearer(token)).status_code == 201

    response = client.post("/api/ext/jobs", json=job(), headers=bearer(token))

    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_api_requests_are_limited_per_token(client, token):
    for _ in range(60):
        assert client.get("/api/ext/me", headers=bearer(token)).status_code == 200

    response = client.get("/api/ext/me", headers=bearer(token))

    assert response.status_code == 429
    assert error_code(response) == "rate_limited"


def test_bad_tokens_are_limited_per_ip(client, token):
    # The route limits only see accepted tokens; guessing has its own limit (review L1).
    for _ in range(30):
        assert client.get("/api/ext/me", headers=bearer("guess")).status_code == 401

    response = client.get("/api/ext/me", headers=bearer("guess"))

    assert response.status_code == 429
    assert error_code(response) == "rate_limited"


def test_spellings_of_one_token_share_its_limit(client, token):
    spellings = [f"Bearer {token}", f"bearer {token}", f"Bearer  {token} "]
    for number in range(60):
        header = {"Authorization": spellings[number % 3], "Origin": ORIGIN}
        assert client.get("/api/ext/me", headers=header).status_code == 200

    response = client.get("/api/ext/me", headers={"Authorization": spellings[1], "Origin": ORIGIN})

    assert response.status_code == 429
