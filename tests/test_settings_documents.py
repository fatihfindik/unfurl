"""Settings › Documents (D-129, SPEC §6.9): the language a new CV starts in."""

import json

import pytest
from sqlmodel import Session

from app.models import User
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in

HTMX = {"HX-Request": "true"}


def save(client, language, token=None):
    return client.post(
        "/settings/documents",
        data={"default_doc_language": language},
        headers=HTMX | {"X-CSRF-Token": token or csrf_token(client)},
    )


def saved_language(engine, user) -> str:
    with Session(engine) as check:
        return check.get(User, user.id).default_doc_language


def test_new_users_start_with_english(user):
    assert user.default_doc_language == "en"


def test_the_settings_page_shows_the_section(auth_client):
    html = auth_client.get("/settings").text

    assert "Documents" in html
    assert "Default document language" in html
    assert 'hx-post="/settings/documents"' in html


@pytest.mark.parametrize("language", ["de", "tr", "en"])
def test_the_language_is_saved(auth_client, engine, user, language):
    response = save(auth_client, language)

    assert response.status_code == 200
    assert json.loads(response.headers["HX-Trigger"])["toast"]["message"] == "Settings saved."
    assert f'value="{language}" selected' in response.text
    assert saved_language(engine, user) == language


def test_a_new_cv_starts_in_that_language(auth_client, user):
    save(auth_client, "de")

    page = auth_client.get("/documents").text  # the New CV modal is part of the page
    form = page[page.index('id="new-cv-language"') :]

    assert 'value="de" selected' in form.split("</select>")[0]


@pytest.mark.parametrize("language", ["", "fr", "EN", "en ", "x" * 1000])
def test_other_values_are_refused(auth_client, engine, user, language):
    response = save(auth_client, language)

    assert response.status_code == 200
    assert "Choose one of the languages." in response.text
    assert "HX-Trigger" not in response.headers
    assert saved_language(engine, user) == "en"


def test_saving_needs_a_csrf_token(auth_client, engine, user):
    response = auth_client.post(
        "/settings/documents", data={"default_doc_language": "de"}, headers=HTMX
    )

    assert response.status_code == 403
    assert saved_language(engine, user) == "en"


def test_saving_needs_a_login(client, engine, user):
    response = client.post(
        "/settings/documents",
        data={"default_doc_language": "de", "csrf_token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert saved_language(engine, user) == "en"


def test_another_user_is_not_changed(client, session, engine, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    log_in(client)

    save(client, "tr")

    assert saved_language(engine, other) == "en"


def test_saving_is_rate_limited(auth_client):
    token = csrf_token(auth_client)
    codes = [save(auth_client, "de", token).status_code for _ in range(31)]

    assert codes[:30] == [200] * 30 and codes[30] == 429
