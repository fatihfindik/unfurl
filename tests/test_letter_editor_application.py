"""The cover letter editor and its application: back link, band, versions, Duplicate
and "Copy text" (SPEC §6.6, §12 M8b; D-157, D-162)."""

import html

import pytest

from app.models import Application, CoverLetter
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_application_documents import fresh, headers


@pytest.fixture
def master(session, user) -> CoverLetter:
    get_or_create_profile(session, user)
    return letters.create_letter(session, user, "Master letter", "en")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def linked(session, user, master, job) -> CoverLetter:
    letter = letters.copy_for_application(session, user, master, job, "Kranich Health · Letter")
    apps.link_letter(session, user, job, letter)
    return fresh(session, letter)


def change(session, letter, **values) -> CoverLetter:
    for name, value in values.items():
        setattr(letter, name, value)
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


def post(client, url):
    return client.post(url, headers=headers(client))


# --- Back link, band, versions (D-162) --------------------------------------------------------


def test_a_letter_for_an_application_links_back_to_it(auth_client, job, linked):
    page = auth_client.get(f"/cover-letters/{linked.id}").text

    assert f'href="/applications/{job.id}"' in page
    assert "Kranich Health" in page
    assert 'href="/documents?tab=letters"' in page and "All documents" in page
    assert "Copy for Kranich Health, based on Master letter." in page


def test_a_letter_without_an_application_links_to_documents(auth_client, session, user, master):
    copy = letters.create_letter(session, user, "Spare", "en")

    page = auth_client.get(f"/cover-letters/{copy.id}").text

    assert "All documents" not in page
    assert "Copy based on Master letter." in page


def test_the_address_cannot_put_another_job_on_the_page(auth_client, session, user, master):
    copy = letters.create_letter(session, user, "Spare", "en")
    other = apps.create(session, user, company="Not This One", job_title="PM")

    page = auth_client.get(f"/cover-letters/{copy.id}?application={other.id}").text

    assert "Not This One" not in page


def test_another_users_application_never_shows(auth_client, session, user, master, linked):
    # Even if the letter pointed at it, only the user's own application is shown.
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Their Secret Co", job_title="PM")
    change(session, linked, application_id=theirs.id)

    page = auth_client.get(f"/cover-letters/{linked.id}").text

    assert "Their Secret Co" not in page


def test_the_version_picker_shows_this_applications_versions(
    auth_client, session, user, master, job, linked
):
    other_job = apps.create(session, user, company="Other", job_title="PM")
    other = letters.copy_for_application(session, user, master, other_job, "Other · Letter")

    page = auth_client.get(f"/cover-letters/{linked.id}").text

    assert f'href="/cover-letters/{master.id}"' in page
    assert f'href="/cover-letters/{other.id}"' not in page


# --- Save as new version keeps the application (D-157, M4c E1) --------------------------------


def test_a_new_version_stays_with_the_application_and_becomes_its_letter(
    auth_client, session, user, job, linked
):
    change(session, linked, body="Changed")

    response = post(auth_client, f"/cover-letters/{linked.id}/versions")

    new_id = int(response.headers["HX-Redirect"].rsplit("/", 1)[1])
    new = session.get(CoverLetter, new_id)
    assert new.application_id == job.id and new.version == linked.version + 1
    assert fresh(session, job).cover_letter_id == new.id


def test_a_new_version_of_a_sent_jobs_letter_does_not_take_its_place(
    auth_client, session, user, job, linked
):
    # The job was sent with a draft still linked (e.g. linked after sending).
    job = fresh(session, job)
    job.status = "applied"
    session.add(job)
    session.commit()
    change(session, linked, body="Changed")

    response = post(auth_client, f"/cover-letters/{linked.id}/versions")

    new_id = int(response.headers["HX-Redirect"].rsplit("/", 1)[1])
    assert session.get(CoverLetter, new_id).application_id == job.id
    assert fresh(session, job).cover_letter_id == linked.id


def test_duplicate_as_new_version_of_a_sent_letter_keeps_the_sent_one(
    auth_client, session, user, job, linked
):
    auth_client.patch(
        f"/applications/{job.id}/move", data={"status": "applied"}, headers=headers(auth_client)
    )

    response = post(auth_client, f"/cover-letters/{linked.id}/versions")

    new_id = int(response.headers["HX-Redirect"].rsplit("/", 1)[1])
    assert session.get(CoverLetter, new_id).status == "draft"
    assert fresh(session, linked).status == "locked"
    assert fresh(session, job).cover_letter_id == linked.id


# --- Duplicate (D-050, D-162) -----------------------------------------------------------------


def test_duplicate_makes_an_unlinked_draft(auth_client, session, user, job, linked):
    response = post(auth_client, f"/cover-letters/{linked.id}/duplicate")

    new_id = int(response.headers["HX-Redirect"].rsplit("/", 1)[1])
    new = session.get(CoverLetter, new_id)
    assert new.title == "Kranich Health · Letter (copy)"
    assert (new.application_id, new.status, new.parent_id) == (None, "draft", linked.parent_id)
    assert new.subject == linked.subject  # the same text
    assert fresh(session, job).cover_letter_id == linked.id


def test_duplicate_of_a_copy_says_copy_once_and_fits(auth_client, session, user, master):
    copy = letters.create_letter(session, user, "T" * 200, "en")
    first = letters.duplicate(session, user, copy)
    second = letters.duplicate(session, user, first)

    assert len(first.title) == 200 and first.title.endswith(" (copy)")
    assert second.title == first.title


def test_duplicate_of_a_sent_letter_is_a_draft(auth_client, session, user, job, linked):
    letters.lock(session, linked)

    response = post(auth_client, f"/cover-letters/{linked.id}/duplicate")

    new = session.get(CoverLetter, int(response.headers["HX-Redirect"].rsplit("/", 1)[1]))
    assert new.status == "draft" and new.application_id is None


def test_duplicate_respects_the_letter_limit(auth_client, linked, monkeypatch):
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 1)

    response = post(auth_client, f"/cover-letters/{linked.id}/duplicate")

    assert "HX-Redirect" not in response.headers
    # The message is built when the app starts, with the real limit.
    assert "You can have up to 100 cover letters" in response.text


def test_the_editor_has_duplicate_for_copies_only(auth_client, master, linked):
    copy_page = auth_client.get(f"/cover-letters/{linked.id}").text
    master_page = auth_client.get(f"/cover-letters/{master.id}").text

    assert f'hx-post="/cover-letters/{linked.id}/duplicate"' in copy_page
    assert "/duplicate" not in master_page


def test_duplicate_of_another_users_letter_is_not_found(auth_client, session, linked):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = letters.create_letter(session, other, "Theirs", "en")

    assert post(auth_client, f"/cover-letters/{theirs.id}/duplicate").status_code == 404
    assert post(auth_client, "/cover-letters/abc/duplicate").status_code == 404


def test_duplicate_needs_csrf_and_a_login(client, auth_client, linked):
    assert auth_client.post(f"/cover-letters/{linked.id}/duplicate").status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    response = post(client, f"/cover-letters/{linked.id}/duplicate")
    assert response.headers["HX-Redirect"].startswith("/login")


# --- Copy text (D-162) ------------------------------------------------------------------------


def test_plain_text_is_salutation_body_closing_and_name(session, user, linked):
    change(
        session,
        linked,
        salutation="Dear Ms Weber,",
        body="First paragraph.\n\nSecond one,\nwith a line break.",
        closing="Kind regards,",
        sender_name="Fatih Findik",
        recipient_company="Hidden GmbH",
        place="Wien",
    )

    text = letters.plain_text(linked, ["First paragraph.", "Second one,\nwith a line break."])

    assert text == (
        "Dear Ms Weber,\n\nFirst paragraph.\n\nSecond one,\nwith a line break.\n\n"
        "Kind regards,\nFatih Findik"
    )


def test_empty_parts_leave_no_gaps(session, user, linked):
    change(session, linked, salutation="", closing="", sender_name="", body="Only this.")

    assert letters.plain_text(linked, ["Only this."]) == "Only this."


def test_the_editor_has_the_copy_button_and_the_escaped_text(auth_client, session, linked):
    change(session, linked, body="<script>alert(1)</script>")

    page = auth_client.get(f"/cover-letters/{linked.id}").text

    assert 'x-data="copyText"' in page and 'data-source="letter-plain-text"' in page
    assert '<textarea id="letter-plain-text"' in page
    assert html.escape("<script>alert(1)</script>", quote=False) in page
    assert "<script>alert(1)</script>" not in page


def test_autosave_brings_the_new_plain_text(auth_client, session, linked):
    values = {name: getattr(linked, name) or "" for name in letters.LETTER_FIELDS}
    values["body"] = "A fresh paragraph."

    response = auth_client.post(
        f"/cover-letters/{linked.id}", data=values, headers=headers(auth_client)
    )

    assert '<textarea id="letter-plain-text"' in response.text
    assert "A fresh paragraph." in response.text


def test_a_locked_letter_can_still_be_copied(auth_client, session, linked):
    letters.lock(session, linked)

    page = auth_client.get(f"/cover-letters/{linked.id}").text

    assert 'x-data="copyText"' in page
