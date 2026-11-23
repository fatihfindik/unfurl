"""Unlocking a master (#89, D-048, D-169): only "Edit master" in the dialog (a POST)
unlocks it, with a value kept in the session; a link can't."""

import pytest

from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, unlock_master
from tests.test_application_documents import fresh
from tests.test_profile_sections import htmx_send


@pytest.fixture
def cv_master(session, user):
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def letter_master(session, user):
    get_or_create_profile(session, user)
    return letters.create_letter(session, user, "Master letter", "en")


def post(client, path, **extra):
    return client.post(
        path,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)} | extra,
    )


READ_ONLY = '<fieldset class="m-0 flex flex-col gap-4 border-0 p-0" disabled>'


# --- The dialog's POST ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "base"), [("cv", "/cvs"), ("letter", "/cover-letters")])
def test_edit_master_opens_the_editor_unlocked(auth_client, cv_master, letter_master, kind, base):
    master = cv_master if kind == "cv" else letter_master
    value = unlock_master(auth_client, kind, master.id)

    page = auth_client.get(f"{base}/{master.id}?unlock={value}").text

    assert READ_ONLY not in page and f'name="unlock" value="{value}"' in page


def test_a_normal_form_post_redirects(auth_client, cv_master):
    response = auth_client.post(
        f"/cvs/{cv_master.id}/edit-master",
        data={"csrf_token": csrf_token(auth_client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/cvs/{cv_master.id}?unlock=")


def test_the_value_is_reused_so_two_tabs_keep_working(auth_client, session, cv_master):
    first = unlock_master(auth_client, "cv", cv_master.id)
    second = unlock_master(auth_client, "cv", cv_master.id)  # the second tab

    assert first == second
    response = htmx_send(
        auth_client, "POST", f"/cvs/{cv_master.id}", {"title": "From tab one", "unlock": first}
    )
    assert response.status_code == 200
    assert fresh(session, cv_master).title == "From tab one"


def test_one_masters_value_does_not_unlock_another(auth_client, session, user, cv_master):
    german = docs.create_cv(session, user, "Lebenslauf", "de", "classic")
    value = unlock_master(auth_client, "cv", cv_master.id)

    assert READ_ONLY in auth_client.get(f"/cvs/{german.id}?unlock={value}").text
    response = htmx_send(
        auth_client, "POST", f"/cvs/{german.id}", {"title": "Changed", "unlock": value}
    )
    assert response.status_code == 409
    assert fresh(session, german).title == "Lebenslauf"


def test_a_cv_value_does_not_unlock_a_letter(auth_client, session, cv_master, letter_master):
    # The ids may be equal; the kind is part of the key.
    value = unlock_master(auth_client, "cv", cv_master.id)

    page = auth_client.get(f"/cover-letters/{letter_master.id}?unlock={value}").text

    assert 'name="unlock"' not in page


def test_the_value_is_gone_after_signing_out(client, auth_client, session, cv_master):
    value = unlock_master(auth_client, "cv", cv_master.id)
    post(auth_client, "/logout")
    auth_client.post(
        "/login",
        data={
            "csrf_token": csrf_token(auth_client),
            "email": USER_EMAIL,
            "password": USER_PASSWORD,
        },
    )

    assert READ_ONLY in auth_client.get(f"/cvs/{cv_master.id}?unlock={value}").text


@pytest.mark.parametrize(("kind", "base"), [("cv", "/cvs"), ("letter", "/cover-letters")])
def test_refresh_needs_the_value(auth_client, cv_master, letter_master, kind, base):
    master = cv_master if kind == "cv" else letter_master

    for form in ({}, {"unlock": "confirmed"}, {"edit_master": "confirmed"}):
        response = htmx_send(auth_client, "POST", f"{base}/{master.id}/refresh", form)
        assert response.status_code == 409, form


# --- Refused ----------------------------------------------------------------------------------


def test_a_copy_has_nothing_to_unlock(auth_client, session, user, cv_master):
    copy = docs.create_cv(session, user, "Copy", "en", "classic")

    assert post(auth_client, f"/cvs/{copy.id}/edit-master").status_code == 409


def test_another_users_master_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = docs.create_cv(session, other, "Theirs", "en", "classic")
    their_letter = letters.create_letter(session, other, "Theirs", "en")

    assert post(auth_client, f"/cvs/{theirs.id}/edit-master").status_code == 404
    assert post(auth_client, f"/cover-letters/{their_letter.id}/edit-master").status_code == 404


def test_without_the_csrf_token_nothing_is_unlocked(auth_client, cv_master):
    response = auth_client.post(f"/cvs/{cv_master.id}/edit-master", headers={"HX-Request": "true"})

    assert response.status_code == 403


def test_logged_out_goes_to_login(client, cv_master):
    response = client.post(
        f"/cvs/{cv_master.id}/edit-master",
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_at_most_30_a_minute(auth_client, cv_master):
    answers = [post(auth_client, f"/cvs/{cv_master.id}/edit-master").status_code for _ in range(31)]

    assert 429 not in answers[:30] and answers[30] == 429
