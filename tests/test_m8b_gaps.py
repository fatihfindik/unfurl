"""M8b authorization and error cases the other M8b test files leave out: logged out,
odd ids, archived jobs, locked letters, rate limits, the letter limit and Undo tokens
of another user (SPEC §12 M8b; D-157, D-159, D-162, D-163)."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.models import Application, CoverLetter
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token, log_in
from tests.test_application_documents import fresh, headers, toast

OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


@pytest.fixture
def master(session, user) -> CoverLetter:
    get_or_create_profile(session, user)
    return letters.create_letter(session, user, "Master letter", "en")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def linked(session, user, master, job) -> CoverLetter:
    letter = letters.copy_for_application(session, user, master, job, "Kranich · Letter")
    apps.link_letter(session, user, job, letter)
    return fresh(session, letter)


@pytest.fixture
def other_client(session, client) -> TestClient:
    """A second browser, logged in as another user (its own cookies)."""
    create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")
    other = TestClient(app)  # the database override from `client` still applies
    assert log_in(other, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return other


def letter_count(session) -> int:
    session.expire_all()
    return len(session.exec(select(CoverLetter)).all())


# (method, path after /applications/{id}) of the three application letter routes.
APPLICATION_ROUTES = [
    ("POST", "/cover-letter"),
    ("DELETE", "/cover-letter"),
    ("POST", "/cover-letter/send"),
]


# --- Logged out, odd ids ----------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), APPLICATION_ROUTES)
def test_logged_out_htmx_requests_go_to_login(client, session, user, linked, job, method, path):
    response = client.request(
        method,
        f"/applications/{job.id}{path}",
        data={"letter": linked.id} if method == "POST" else None,
        headers=headers(client),
        follow_redirects=False,
    )

    assert response.headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).cover_letter_id == linked.id
    assert fresh(session, linked).status == "draft"


@pytest.mark.parametrize(("method", "path"), APPLICATION_ROUTES)
def test_logged_out_plain_requests_go_to_login(client, job, method, path):
    response = client.request(
        method,
        f"/applications/{job.id}{path}",
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**31), str(2**70)])
@pytest.mark.parametrize(("method", "path"), APPLICATION_ROUTES)
def test_odd_application_ids_are_not_found(auth_client, session, master, method, path, bad_id):
    response = auth_client.request(
        method,
        f"/applications/{bad_id}{path}",
        data={"master": master.id} if method == "POST" else None,
        headers=headers(auth_client),
    )

    assert response.status_code == 404
    assert letter_count(session) == 1  # only the master: no copy was made


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "1.5", str(2**70)])
def test_odd_master_ids_are_not_found(auth_client, session, master, job, bad_id):
    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"master": bad_id},
        headers=headers(auth_client),
    )

    assert response.status_code == 404
    assert fresh(session, job).cover_letter_id is None


@pytest.mark.parametrize("form", [{}, {"master": "", "letter": ""}, {"other": "1"}])
def test_a_form_without_a_letter_is_not_found(auth_client, session, master, job, form):
    response = auth_client.post(
        f"/applications/{job.id}/cover-letter", data=form, headers=headers(auth_client)
    )

    assert response.status_code == 404
    assert fresh(session, job).cover_letter_id is None
    assert letter_count(session) == 1


def test_a_weird_application_in_the_editor_address_is_ignored(auth_client, linked, job):
    for value in ("abc", "-1", str(2**70), ""):
        response = auth_client.get(f"/cover-letters/{linked.id}?application={value}")

        assert response.status_code == 200
        # The back link still comes from the letter itself (D-162).
        assert f'href="/applications/{job.id}"' in response.text


# --- Archived jobs ------------------------------------------------------------------------------


def test_an_archived_job_takes_no_existing_copy(auth_client, session, user, master, job):
    copy = letters.create_letter(session, user, "Spare copy", "en")
    apps.archive(session, user, job)

    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"letter": copy.id},
        headers=headers(auth_client),
    )

    assert response.status_code == 409
    assert fresh(session, job).cover_letter_id is None
    assert fresh(session, copy).application_id is None


def test_lock_as_sent_is_refused_for_an_archived_job(auth_client, session, user, master):
    job = apps.create(session, user, company="Acme", job_title="PM", status="applied")
    letter = letters.copy_for_application(session, user, master, job, "Acme · Letter")
    apps.link_letter(session, user, job, letter)
    apps.archive(session, user, fresh(session, job))

    response = auth_client.post(
        f"/applications/{job.id}/cover-letter/send", headers=headers(auth_client)
    )

    assert response.status_code == 409
    assert fresh(session, letter).status == "draft"
    assert apps.sent_documents(session, user, fresh(session, job)) == []


# --- Lock as sent and locked letters ----------------------------------------------------------


def test_lock_as_sent_without_a_letter_is_a_conflict(auth_client, session, user):
    job = apps.create(session, user, company="Acme", job_title="PM", status="applied")

    response = auth_client.post(
        f"/applications/{job.id}/cover-letter/send", headers=headers(auth_client)
    )

    assert response.status_code == 409
    assert apps.sent_documents(session, user, fresh(session, job)) == []


def test_a_sent_letter_cannot_be_replaced_by_another_draft(
    auth_client, session, user, master, job, linked
):
    auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
    )
    assert fresh(session, linked).status == "locked"
    spare = letters.create_letter(session, user, "Spare copy", "en")

    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"letter": spare.id},
        headers=headers(auth_client),
    )

    assert response.status_code == 409
    assert fresh(session, job).cover_letter_id == linked.id


def test_a_sent_letter_is_not_replaced_by_a_new_copy_of_a_master(
    auth_client, session, user, master, job, linked
):
    auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
    )
    count = len(letters.list_letters(session, user))

    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"master": master.id},
        headers=headers(auth_client),
    )

    assert response.status_code == 409
    assert fresh(session, job).cover_letter_id == linked.id
    session.expire_all()
    assert len(letters.list_letters(session, user)) == count  # no copy left over


def test_a_locked_letter_cannot_be_copied_as_a_master(auth_client, session, user, linked):
    other_job = apps.create(session, user, company="Other", job_title="PM")
    letters.lock(session, linked)

    response = auth_client.post(
        f"/applications/{other_job.id}/cover-letter",
        data={"master": linked.id},
        headers=headers(auth_client),
    )

    assert response.status_code == 422
    assert fresh(session, other_job).cover_letter_id is None


def test_a_letter_of_another_job_cannot_be_moved_even_after_it_was_archived(
    auth_client, session, user, master, job, linked
):
    apps.archive(session, user, job)
    other_job = apps.create(session, user, company="Other", job_title="PM")

    response = auth_client.post(
        f"/applications/{other_job.id}/cover-letter",
        data={"letter": linked.id},
        headers=headers(auth_client),
    )

    assert response.status_code == 409
    assert fresh(session, linked).application_id == job.id
    assert fresh(session, other_job).cover_letter_id is None


# --- Rate limits ------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path"), APPLICATION_ROUTES)
def test_at_most_60_requests_a_minute(auth_client, job, method, path):
    url = f"/applications/{job.id}{path}"
    token = headers(auth_client)
    # Every answer here is a refusal (no letter yet): the limit counts them too.
    answers = [
        auth_client.request(
            method, url, data={"letter": "abc"} if method == "POST" else None, headers=token
        ).status_code
        for _ in range(61)
    ]

    assert 429 not in answers[:60]
    assert answers[60] == 429


# --- The letter limit -------------------------------------------------------------------------


def test_save_as_new_version_respects_the_letter_limit(
    auth_client, session, user, job, linked, monkeypatch
):
    linked.body = "Changed"
    session.add(linked)
    session.commit()
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", letter_count(session))

    response = auth_client.post(
        f"/cover-letters/{linked.id}/versions", headers=headers(auth_client)
    )

    assert "HX-Redirect" not in response.headers
    assert letter_count(session) == 2
    assert fresh(session, job).cover_letter_id == linked.id


def test_undo_of_a_delete_at_the_letter_limit_brings_nothing_back(
    auth_client, session, user, master, job, linked, monkeypatch
):
    auth_client.delete(f"/cover-letters/{linked.id}", headers=headers(auth_client))
    page = auth_client.get("/documents?tab=letters").text
    token = page.split('data-undo-token="', 1)[1].split('"', 1)[0]
    # Meanwhile the user is at the limit again (here: the limit is lowered).
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 1)

    response = auth_client.post("/undo/delete", data={"token": token}, headers=headers(auth_client))

    assert response.status_code == 204
    # The message is a flash on the next page.
    assert "Nothing to undo." in auth_client.get("/documents?tab=letters").text
    assert letter_count(session) == 1
    assert fresh(session, job).cover_letter_id is None


# --- Undo tokens belong to one user -----------------------------------------------------------


def test_another_user_cannot_use_the_undo_of_linking(
    auth_client, other_client, session, user, master, job
):
    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"master": master.id},
        headers=headers(auth_client),
    )
    token = response.headers["HX-Trigger"].split('"undo_token": "', 1)[1].split('"', 1)[0]
    letter_id = fresh(session, job).cover_letter_id

    answer = other_client.post(
        "/applications/undo", data={"token": token}, headers=headers(other_client)
    )

    assert toast(answer) == "Nothing to undo."
    assert fresh(session, job).cover_letter_id == letter_id
    assert session.get(CoverLetter, letter_id) is not None


def test_another_user_cannot_use_the_undo_of_sending(
    auth_client, other_client, session, user, job, linked
):
    response = auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
    )
    token = response.headers["HX-Trigger"].split('"undo_token": "', 1)[1].split('"', 1)[0]

    answer = other_client.post(
        "/applications/undo", data={"token": token}, headers=headers(other_client)
    )

    assert toast(answer) == "Nothing to undo."
    assert fresh(session, linked).status == "locked"
    assert fresh(session, job).status == "applied"


def test_another_user_cannot_use_the_undo_of_a_delete(
    auth_client, other_client, session, user, job, linked
):
    auth_client.delete(f"/cover-letters/{linked.id}", headers=headers(auth_client))
    page = auth_client.get("/documents?tab=letters").text
    token = page.split('data-undo-token="', 1)[1].split('"', 1)[0]

    answer = other_client.post("/undo/delete", data={"token": token}, headers=headers(other_client))

    assert answer.status_code == 204
    assert "Nothing to undo." in other_client.get("/documents?tab=letters").text
    assert letter_count(session) == 1  # only the master
    assert fresh(session, job).cover_letter_id is None


# --- Another user's letter as the current one -------------------------------------------------


def test_another_users_letter_is_never_shown_or_locked(auth_client, session, user, job):
    # A broken row: the job points at someone else's letter. It must stay theirs.
    other = create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="O")
    get_or_create_profile(session, other)
    letters.create_letter(session, other, "Their master", "en")
    theirs = letters.create_letter(session, other, "Their Secret Letter", "en")
    job.cover_letter_id = theirs.id
    session.add(job)
    session.commit()

    page = auth_client.get(f"/applications/{job.id}").text
    removed = auth_client.delete(
        f"/applications/{job.id}/cover-letter", headers=headers(auth_client)
    )
    auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
    )

    assert "Their Secret Letter" not in page
    assert removed.status_code == 409
    assert fresh(session, theirs).status == "draft"
    assert apps.sent_documents(session, user, fresh(session, job)) == []
