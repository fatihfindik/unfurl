"""Cover letter pages (SPEC §6.5, §6.6, M8a): the letters tab, new letter, the editor
with live preview, versions, refresh, delete with Undo, and who may use them."""

import datetime as dt
import json

import pytest
from freezegun import freeze_time
from sqlmodel import select

from app.i18n.document_labels import full_date
from app.models import CoverLetter, User
from app.schemas.cover_letter import LETTER_FIELDS, CoverLetterForm
from app.services import cover_letter_service as letters
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_profile_sections import htmx_send

# (method, url) for every letter route; "1" stands for any letter id.
ROUTES = [
    ("POST", "/cover-letters"),
    ("GET", "/cover-letters/1"),
    ("GET", "/cover-letters/1?edit_master=confirmed"),
    ("POST", "/cover-letters/1"),
    ("POST", "/cover-letters/1/versions"),
    ("POST", "/cover-letters/1/refresh"),
    ("DELETE", "/cover-letters/1"),
]
# The routes that take a letter id, as (method, path after the id).
LETTER_ROUTES = [
    ("GET", ""),
    ("POST", ""),
    ("POST", "/versions"),
    ("POST", "/refresh"),
    ("DELETE", ""),
]


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


def stored(session, letter) -> CoverLetter:
    session.expire_all()
    return session.get(CoverLetter, letter.id)


def save(client, letter, **changes):
    """Autosave with every field of `letter`, changed by `changes`."""
    form = {name: getattr(letter, name) or "" for name in LETTER_FIELDS} | changes
    return htmx_send(client, "POST", f"/cover-letters/{letter.id}", form)


# --- The letters tab ----------------------------------------------------------------


def test_the_tab_lists_masters_and_copies(auth_client, master, copy):
    page = auth_client.get("/documents?tab=letters").text

    assert "MASTER · EN" in page
    assert f'href="/cover-letters/{master.id}"' in page
    assert "For Kranich · v1" in page
    assert "based on Master letter" in page
    assert 'data-dialog-open="new-letter"' in page


def test_the_new_letter_form_starts_in_the_default_language(auth_client, session, user):
    user.default_doc_language = "de"
    session.add(user)
    session.commit()

    page = auth_client.get("/documents?tab=letters").text

    form = page.split('id="new-letter-form"', 1)[1].split("</form>", 1)[0]
    assert '<option value="de" selected' in form


def test_a_new_letter_opens_in_the_editor(auth_client, session, profile):
    response = htmx_send(
        auth_client, "POST", "/cover-letters", {"title": "Mein Anschreiben", "language": "de"}
    )

    (letter,) = session.exec(select(CoverLetter)).all()
    assert response.headers["HX-Redirect"] == f"/cover-letters/{letter.id}"
    assert letter.is_master and letter.language == "de"


def test_new_letter_errors_come_back_as_the_form(auth_client, session):
    response = htmx_send(auth_client, "POST", "/cover-letters", {"title": "", "language": "fr"})

    assert response.status_code == 200
    assert 'id="new-letter-form"' in response.text
    assert "Choose a language." in response.text
    assert session.exec(select(CoverLetter)).all() == []


def test_the_letter_limit_is_shown_in_the_form(auth_client, master, monkeypatch):
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 1)

    response = htmx_send(
        auth_client, "POST", "/cover-letters", {"title": "One more", "language": "en"}
    )

    # The message names the real limit (100); the test only lowers the count.
    assert "You can have up to 100 cover letters. Delete one to add another." in response.text


# --- The editor and the preview -----------------------------------------------------


def test_the_editor_shows_the_form_and_the_preview(auth_client, copy):
    page = auth_client.get(f"/cover-letters/{copy.id}").text

    assert 'id="letter-form"' in page and 'class="letter-paper"' in page
    assert "Dear Hiring Team," in page and "Kind regards," in page
    assert "Copy based on Master letter. Your master letter stays unchanged." in page
    assert "css/cover_letter.css" in page


def test_autosave_saves_and_returns_the_preview(auth_client, session, copy):
    response = save(
        auth_client,
        copy,
        body="First paragraph.\r\n\r\nSecond paragraph.",
        recipient_company="Kranich Health",
    )

    assert response.status_code == 200
    assert '<p class="letter-lines">First paragraph.</p>' in response.text
    assert '<p class="letter-lines">Second paragraph.</p>' in response.text
    letter = stored(session, copy)
    assert letter.body == "First paragraph.\n\nSecond paragraph."
    assert letter.recipient_company == "Kranich Health"


def test_a_bad_field_keeps_its_value_and_the_rest_is_saved(auth_client, session, copy):
    letters.update_letter(
        session, copy, CoverLetterForm.model_validate({"title": "For Kranich", "body": "Old."})
    )

    response = save(auth_client, copy, body="x" * 10_001, subject="Bewerbung")

    assert "Letter text was not saved:" in response.text
    assert "Your other changes are saved." in response.text
    letter = stored(session, copy)
    assert letter.body == "Old." and letter.subject == "Bewerbung"


def test_user_text_is_escaped_in_the_preview(auth_client, copy):
    response = save(auth_client, copy, body="<script>alert(1)</script>", subject="<b>Hi</b>")

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "&lt;b&gt;Hi&lt;/b&gt;" in response.text


@pytest.mark.parametrize("language", ["de", "en", "tr"])
def test_the_date_line_is_today_where_the_user_is(auth_client, session, user, profile, language):
    # 23:30 UTC is already the next day in Vienna. The moment is tomorrow, not a fixed
    # date: a frozen time before the login makes the session cookie invalid (M8b).
    user.timezone = "Europe/Vienna"
    session.add(user)
    session.commit()
    letter = letters.create_letter(session, user, "L", language)
    late = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).replace(hour=23, minute=30)

    with freeze_time(late):
        page = auth_client.get(f"/cover-letters/{letter.id}").text

    date = full_date(late.date() + dt.timedelta(days=1), language)
    assert f'<p class="letter-date">Vienna, {date}</p>' in page


# --- Masters, locks, versions, refresh -----------------------------------------------


def test_a_master_is_read_only_until_unlocked(auth_client, session, master):
    page = auth_client.get(f"/cover-letters/{master.id}").text
    assert 'Read only. Use "Edit master" above to change your master letter.' in page

    assert save(auth_client, master, body="Changed.").status_code == 409
    assert stored(session, master).body == ""

    response = save(auth_client, master, body="Changed.", edit_master="confirmed")
    assert response.status_code == 200
    assert stored(session, master).body == "Changed."


def test_a_locked_letter_cant_be_saved_refreshed_or_deleted(auth_client, session, copy):
    letters.lock(session, copy)

    page = auth_client.get(f"/cover-letters/{copy.id}").text
    assert "Read only. This letter was sent" in page
    assert save(auth_client, copy, body="Changed.").status_code == 409
    assert htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/refresh").status_code == 409
    assert htmx_send(auth_client, "DELETE", f"/cover-letters/{copy.id}").status_code == 409
    assert stored(session, copy).is_locked


def test_save_as_new_version_opens_the_new_draft(auth_client, session, copy):
    save(auth_client, copy, body="Edited.")

    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/versions")

    newest = session.exec(select(CoverLetter).order_by(CoverLetter.id.desc())).first()
    assert response.headers["HX-Redirect"] == f"/cover-letters/{newest.id}"
    assert newest.version == 2 and newest.body == "Edited."


def test_nothing_changed_is_said_instead_of_a_copy(auth_client, session, copy):
    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/versions")

    assert "Nothing changed since this version." in response.text
    assert len(session.exec(select(CoverLetter)).all()) == 2


def test_refresh_replaces_only_the_sender(auth_client, session, profile, copy):
    save(auth_client, copy, sender_phone="old", body="Kept.")
    profile.phone = "+43 1 999"
    session.add(profile)
    session.commit()

    response = htmx_send(auth_client, "POST", f"/cover-letters/{copy.id}/refresh")

    assert response.headers["HX-Redirect"] == f"/cover-letters/{copy.id}"
    letter = stored(session, copy)
    assert letter.sender_phone == "+43 1 999" and letter.body == "Kept."


# --- Delete and Undo ----------------------------------------------------------------


def test_delete_goes_to_the_letters_tab_with_undo(auth_client, session, user, copy):
    save(auth_client, copy, body="Dear Kranich.")

    response = htmx_send(auth_client, "DELETE", f"/cover-letters/{copy.id}")

    assert response.headers["HX-Redirect"] == "/documents?tab=letters"
    page = auth_client.get("/documents?tab=letters").text
    assert 'data-message="&#34;For Kranich&#34; deleted."' in page
    token = page.split('data-undo-token="', 1)[1].split('"', 1)[0]

    auth_client.post(
        "/undo/delete",
        data={"token": token},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(auth_client)},
    )

    session.expire_all()
    (back,) = [letter for letter in letters.list_letters(session, user) if not letter.is_master]
    assert (back.title, back.body, back.version) == ("For Kranich", "Dear Kranich.", 1)


def test_a_master_with_copies_says_why_it_stays(auth_client, session, master, copy):
    response = htmx_send(auth_client, "DELETE", f"/cover-letters/{master.id}")

    assert response.status_code == 204
    toast = json.loads(response.headers["HX-Trigger"])["toast"]
    assert toast["message"] == "This master has 1 copy. Delete the copy first."
    assert stored(session, master) is not None


# --- Who may use the routes ---------------------------------------------------------


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_visitors_are_sent_to_login(client, method, url):
    headers = {"X-CSRF-Token": csrf_token(client)}
    response = client.request(method, url, headers=headers, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize(("method", "url"), ROUTES)
def test_logged_out_htmx_requests_get_hx_redirect(client, method, url):
    headers = {"X-CSRF-Token": csrf_token(client), "HX-Request": "true"}
    response = client.request(method, url, headers=headers, follow_redirects=False)

    assert response.headers["HX-Redirect"].startswith("/login")


@pytest.mark.parametrize(("method", "url"), [r for r in ROUTES if r[0] != "GET"])
def test_writes_need_the_csrf_token(auth_client, method, url):
    response = auth_client.request(method, url, follow_redirects=False)
    assert response.status_code == 403


@pytest.mark.parametrize("letter_id", ["abc", "0", "-1", str(2**31), str(2**70)])
@pytest.mark.parametrize(("method", "path"), LETTER_ROUTES)
def test_ids_that_cannot_exist_are_404(auth_client, session, method, path, letter_id):
    response = htmx_send(auth_client, method, f"/cover-letters/{letter_id}{path}", {"title": "X"})

    assert response.status_code == 404
    assert session.exec(select(CoverLetter)).all() == []


@pytest.fixture
def their_master(session) -> CoverLetter:
    other: User = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    return letters.create_letter(session, other, "Theirs", "en")


@pytest.mark.parametrize(("method", "path"), LETTER_ROUTES)
def test_another_users_letter_is_404_even_when_unlocked(
    auth_client, session, their_master, method, path
):
    url = f"/cover-letters/{their_master.id}{path}"
    if method == "GET":
        url += "?edit_master=confirmed"
    form = {"title": "Taken", "body": "Taken", "edit_master": "confirmed"}

    response = htmx_send(auth_client, method, url, form)

    assert response.status_code == 404
    assert "Theirs" not in response.text
    (only,) = session.exec(select(CoverLetter)).all()
    assert (only.id, only.title, only.body) == (their_master.id, "Theirs", "")
