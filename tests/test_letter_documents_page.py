"""Documents › Cover letters: copies grouped by application, and a deleted linked letter
going back to its application on Undo (SPEC §6.6, §12 M8b; D-163)."""

import pytest

from app.models import Application, CoverLetter
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token


def headers(client):
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}


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
    return letter


def letters_tab(client) -> str:
    return client.get("/documents?tab=letters").text


def delete_and_get_token(client, letter) -> str:
    """Delete a letter on its page and read the Undo token from the next page's toast."""
    response = client.delete(f"/cover-letters/{letter.id}", headers=headers(client))
    assert response.status_code in (200, 204)
    return letters_tab(client).split('data-undo-token="', 1)[1].split('"', 1)[0]


def undo(client, token):
    return client.post("/undo/delete", data={"token": token}, headers=headers(client))


def the_copy(session, user) -> CoverLetter:
    session.expire_all()
    (copy,) = [letter for letter in letters.list_letters(session, user) if not letter.is_master]
    return copy


# --- Grouping ---------------------------------------------------------------------------------


def test_copies_are_grouped_by_their_application(auth_client, session, user, master, linked, job):
    letters.create_letter(session, user, "Loose letter", "en")  # a copy of the master, unlinked

    page = letters_tab(auth_client)

    assert "Copies for applications" in page
    assert f'href="/applications/{job.id}"' in page
    assert "Kranich Health · Senior PM" in page
    assert "Not linked to an application" in page
    # The linked letter sits in its application's group, before the unlinked group.
    assert page.index("Kranich Health · Senior PM") < page.index("Kranich · Letter")
    assert page.index("Kranich · Letter") < page.index("Not linked to an application")
    assert page.index("Not linked to an application") < page.index("Loose letter")
    assert "comes soon" not in page


def test_only_unlinked_copies_show_one_group(auth_client, session, user, master):
    letters.create_letter(session, user, "Loose letter", "en")

    page = letters_tab(auth_client)

    assert "Not linked to an application" in page
    assert 'href="/applications/' not in page


def test_another_users_application_never_shows_as_a_group(auth_client, session, user, master):
    stranger = create_user(session, email="stranger@example.com", password="x" * 12, full_name="S")
    their_job = apps.create(session, stranger, company="Secret GmbH", job_title="CTO")
    # A broken row pointing at someone else's application must not reveal it.
    copy = letters.create_letter(session, user, "Odd letter", "en")
    copy.application_id = their_job.id
    session.add(copy)
    session.commit()

    page = letters_tab(auth_client)

    assert "Secret GmbH" not in page
    assert "Odd letter" in page and "Not linked to an application" in page


# --- Delete and Undo (D-163) ------------------------------------------------------------------


def test_deleting_a_linked_letter_empties_the_place_and_undo_fills_it(
    auth_client, session, user, linked, job
):
    token = delete_and_get_token(auth_client, linked)
    session.expire_all()
    assert session.get(Application, job.id).cover_letter_id is None

    undo(auth_client, token)

    back = the_copy(session, user)
    job = session.get(Application, job.id)
    assert job.cover_letter_id == back.id
    assert back.application_id == job.id and back.title == "Kranich · Letter"


def test_undo_does_not_replace_a_letter_linked_meanwhile(
    auth_client, session, user, master, linked, job
):
    token = delete_and_get_token(auth_client, linked)
    session.expunge(linked)  # the row is gone; its id may be used again
    other = letters.copy_for_application(session, user, master, job, "Second try")
    apps.link_letter(session, user, job, other)

    undo(auth_client, token)

    session.expire_all()
    assert session.get(Application, job.id).cover_letter_id == other.id
    titles = {letter.title for letter in letters.list_letters(session, user)}
    assert "Kranich · Letter" in titles  # the letter is back, just not in use


def test_undo_does_not_link_into_a_job_that_was_sent_meanwhile(
    auth_client, session, user, linked, job
):
    token = delete_and_get_token(auth_client, linked)
    session.expire_all()
    job = session.get(Application, job.id)
    job.status = "applied"
    session.add(job)
    session.commit()

    undo(auth_client, token)

    session.expire_all()
    assert session.get(Application, job.id).cover_letter_id is None
    assert the_copy(session, user).title == "Kranich · Letter"


def test_undo_after_the_application_is_gone_brings_an_unlinked_letter(
    auth_client, session, user, linked, job
):
    token = delete_and_get_token(auth_client, linked)
    session.expire_all()
    session.delete(session.get(Application, job.id))
    session.commit()

    undo(auth_client, token)

    back = the_copy(session, user)
    assert back.application_id is None
    assert "Not linked to an application" in letters_tab(auth_client)


def test_an_unlinked_letter_comes_back_unlinked(auth_client, session, user, master):
    loose = letters.create_letter(session, user, "Loose letter", "en")
    token = delete_and_get_token(auth_client, loose)

    undo(auth_client, token)

    back = the_copy(session, user)
    assert back.title == "Loose letter" and back.application_id is None
