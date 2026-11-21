"""Gaps in the M8a tests (cover letters): master and lock rules on refresh, Duplicate
of a locked letter, form errors per field, the limit on new versions, deleting a
master without copies, the delete rate limit and Undo edge cases (D-086, D-152…D-154)."""

import pytest
from sqlmodel import select

from app.models import CoverLetter
from app.services import cover_letter_service as letters
from app.services.profile_service import get_or_create_profile
from tests.test_cover_letter_pages import save, stored
from tests.test_delete_undo import flash_of, undo_delete
from tests.test_profile_sections import htmx_send


# The same letters as in test_cover_letter_pages.py.
@pytest.fixture
def profile(session, user):
    profile = get_or_create_profile(session, user)
    profile.location = "Vienna"
    session.add(profile)
    session.commit()
    return profile


@pytest.fixture
def master(session, user, profile) -> CoverLetter:
    return letters.create_letter(session, user, "Master letter", "en")


@pytest.fixture
def copy(session, user, master) -> CoverLetter:
    return letters.create_letter(session, user, "For Kranich", "en")


def all_letters(session) -> list[CoverLetter]:
    session.expire_all()
    return list(session.exec(select(CoverLetter).order_by(CoverLetter.id)))


def undo_token_of(client) -> str:
    """The Undo token in the toast that the letters tab shows after a delete."""
    page = client.get("/documents?tab=letters").text
    return page.split('data-undo-token="', 1)[1].split('"', 1)[0]


def delete(client, letter):
    return htmx_send(client, "DELETE", f"/cover-letters/{letter.id}")


# --- Masters and locks on refresh and save --------------------------------------------


def test_a_master_is_refreshed_only_after_it_was_unlocked(auth_client, session, profile, master):
    profile.phone = "+43 1 999"
    session.add(profile)
    session.commit()

    response = htmx_send(auth_client, "POST", f"/cover-letters/{master.id}/refresh")
    assert response.status_code == 409
    assert stored(session, master).sender_phone is None

    response = htmx_send(
        auth_client, "POST", f"/cover-letters/{master.id}/refresh", {"edit_master": "confirmed"}
    )
    # Back in the editor, still unlocked.
    assert response.headers["HX-Redirect"] == f"/cover-letters/{master.id}?edit_master=confirmed"
    assert stored(session, master).sender_phone == "+43 1 999"


def test_edit_master_does_not_unlock_a_locked_letter(auth_client, session, copy):
    save(auth_client, copy, body="Sent text.")
    letters.lock(session, copy)
    form = {"edit_master": "confirmed"}

    assert save(auth_client, copy, body="Changed.", **form).status_code == 409
    refresh = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/refresh", form)
    assert refresh.status_code == 409
    assert stored(session, copy).body == "Sent text."


def test_duplicate_of_a_locked_letter_opens_a_new_draft(auth_client, session, copy):
    save(auth_client, copy, body="Sent text.")
    letters.lock(session, copy)

    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/versions")

    newest = all_letters(session)[-1]
    assert response.headers["HX-Redirect"] == f"/cover-letters/{newest.id}"
    assert (newest.status, newest.version, newest.body) == ("draft", 2, "Sent text.")
    assert newest.parent_id == copy.parent_id
    locked = stored(session, copy)
    assert locked.is_locked and locked.body == "Sent text."


# --- Form errors --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad_value", "label"),
    [
        ("title", "", "Title"),
        ("title", "x" * 201, "Title"),
        ("sender_email", "not an address", "Your email"),
        ("recipient_address", "x" * 501, "Company address"),
    ],
)
def test_a_bad_field_keeps_its_saved_value(auth_client, session, copy, field, bad_value, label):
    before = getattr(copy, field)

    response = save(auth_client, copy, **{field: bad_value, "subject": "Bewerbung"})

    assert response.status_code == 200
    assert f"{label} was not saved:" in response.text
    letter = stored(session, copy)
    assert getattr(letter, field) == before
    assert letter.subject == "Bewerbung"


def test_a_form_with_too_many_fields_is_refused(auth_client, session, copy):
    # Far more fields than the editor sends: not from our page (MAX_FORM_FIELDS).
    form = {f"extra{number}": "x" for number in range(60)} | {"title": "Changed"}

    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}", form)

    assert response.status_code == 400
    assert stored(session, copy).title == "For Kranich"


# --- Limit --------------------------------------------------------------------------


def test_the_letter_limit_stops_a_new_version(auth_client, session, copy, monkeypatch):
    save(auth_client, copy, body="Edited.")
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 2)

    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/versions")

    assert "You can have up to 100 cover letters. Delete one to add another." in response.text
    assert "HX-Redirect" not in response.headers
    assert len(all_letters(session)) == 2


# --- Delete -------------------------------------------------------------------------


def test_a_master_without_copies_is_deleted_and_comes_back(auth_client, session, master):
    save(auth_client, master, body="My master text.", edit_master="confirmed")

    response = delete(auth_client, master)

    assert response.headers["HX-Redirect"] == "/documents?tab=letters"
    assert all_letters(session) == []

    undo_delete(auth_client, undo_token_of(auth_client))

    (back,) = all_letters(session)
    assert back.is_master and back.version == 0 and back.body == "My master text."


def test_letter_deletes_are_rate_limited(auth_client):
    # Missing letters answer 404; those still count.
    codes = [delete(auth_client, CoverLetter(id=999)).status_code for _ in range(61)]

    assert 429 not in codes[:60]
    assert codes[60] == 429


# --- Undo edge cases ----------------------------------------------------------------


def test_a_letter_undo_works_once(auth_client, session, copy):
    delete(auth_client, copy)
    token = undo_token_of(auth_client)

    undo_delete(auth_client, token)
    undo_delete(auth_client, token)

    assert len(all_letters(session)) == 2
    assert flash_of(auth_client) == "Nothing to undo."


def test_undo_of_a_copy_whose_master_is_gone_restores_nothing(auth_client, session, master, copy):
    delete(auth_client, copy)
    token = undo_token_of(auth_client)
    delete(auth_client, master)

    undo_delete(auth_client, token)

    assert all_letters(session) == []
    assert flash_of(auth_client) == "Nothing to undo."


def test_undo_of_an_old_master_keeps_the_new_one(auth_client, session, user, master):
    delete(auth_client, master)
    token = undo_token_of(auth_client)
    # SQLite gives the deleted id out again; forget the deleted row in this session.
    session.expunge(master)
    new_master = letters.create_letter(session, user, "New master", "en")

    undo_delete(auth_client, token)

    (only,) = all_letters(session)
    assert only.id == new_master.id
    assert flash_of(auth_client) == "Nothing to undo."


def test_a_copy_whose_number_was_taken_comes_back_as_the_next(auth_client, session, user, copy):
    save(auth_client, copy, body="Deleted text.")
    delete(auth_client, copy)
    token = undo_token_of(auth_client)
    session.expunge(copy)  # as above: SQLite gives the deleted id out again
    newer = letters.create_letter(session, user, "Newer", "en")  # takes v1 again
    assert newer.version == 1

    undo_delete(auth_client, token)

    back = all_letters(session)[-1]
    assert (back.body, back.version) == ("Deleted text.", 2)
    assert flash_of(auth_client) == "Undone."


def test_undo_does_not_go_over_the_letter_limit(auth_client, session, copy, monkeypatch):
    delete(auth_client, copy)
    token = undo_token_of(auth_client)
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 1)

    undo_delete(auth_client, token)

    assert len(all_letters(session)) == 1
    assert flash_of(auth_client) == "Nothing to undo."
