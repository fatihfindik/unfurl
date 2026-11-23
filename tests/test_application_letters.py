"""Cover letters in applications (SPEC §5.4, §6.7, §12 M8b; D-157, D-158)."""

import pytest

from app.models import Application, CoverLetter
from app.schemas.cover_letter import CoverLetterForm
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.helpers import csrf_token
from tests.test_application_documents import fresh, headers, toast, undo


@pytest.fixture
def master(session, user) -> CoverLetter:
    get_or_create_profile(session, user)
    return letters.create_letter(session, user, "Master letter", "en")


@pytest.fixture
def job(session, user) -> Application:
    job = apps.create(session, user, company="Kranich Health", job_title="Senior PM")
    job.contact_name = "Mira Weber"  # set in Job & details
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def use_master(client, job, master):
    return client.post(
        f"/applications/{job.id}/cover-letter", data={"master": master.id}, headers=headers(client)
    )


def use_letter(client, job, letter):
    return client.post(
        f"/applications/{job.id}/cover-letter", data={"letter": letter.id}, headers=headers(client)
    )


def remove(client, job):
    return client.delete(f"/applications/{job.id}/cover-letter", headers=headers(client))


def current(session, job) -> CoverLetter | None:
    letter_id = fresh(session, job).cover_letter_id
    return session.get(CoverLetter, letter_id) if letter_id else None


def edit(session, letter, **changes):
    values = {name: getattr(letter, name) for name in CoverLetterForm.model_fields} | changes
    form = CoverLetterForm.model_validate(values)
    # A master only changes after "Edit master" (D-048): the user did that here.
    letters.update_letter(session, letter, form, master_unlocked=letter.is_master)


# --- Copying the master (D-157, D-158) -------------------------------------------------------


def test_copying_the_master_makes_a_linked_draft(auth_client, session, user, master, job):
    response = use_master(auth_client, job, master)

    assert toast(response) == "Kranich Health · Cover letter is used for Kranich Health."
    letter = current(session, job)
    assert (letter.title, letter.application_id, letter.parent_id, letter.status) == (
        "Kranich Health · Cover letter",
        job.id,
        master.id,
        "draft",
    )
    assert fresh(session, master).application_id is None  # the master stays untouched
    assert apps.events_of(session, job)[-1].message == (
        "Cover letter: Kranich Health · Cover letter"
    )


def test_the_copy_gets_the_jobs_details_in_its_empty_fields(
    auth_client, session, user, master, job
):
    use_master(auth_client, job, master)

    letter = current(session, job)
    assert letter.recipient_company == "Kranich Health"
    assert letter.recipient_name == "Mira Weber"
    assert letter.subject == "Application for Senior PM"
    assert fresh(session, master).subject == ""  # the master keeps its own fields


def test_the_masters_own_text_is_never_overwritten(auth_client, session, user, master, job):
    edit(
        session,
        master,
        recipient_company="Hiring GmbH",
        recipient_name="HR team",
        subject="My own subject",
    )

    use_master(auth_client, job, master)

    letter = current(session, job)
    assert (letter.recipient_company, letter.recipient_name, letter.subject) == (
        "Hiring GmbH",
        "HR team",
        "My own subject",
    )


@pytest.mark.parametrize(
    ("language", "subject"),
    [
        ("de", "Bewerbung als Senior PM"),
        ("en", "Application for Senior PM"),
        ("tr", "Senior PM pozisyonu için başvuru"),
    ],
)
def test_the_subject_is_in_the_letters_language(auth_client, session, user, job, language, subject):
    get_or_create_profile(session, user)
    master = letters.create_letter(session, user, "Master", language)

    use_master(auth_client, job, master)

    assert current(session, job).subject == subject


def test_a_job_without_a_contact_leaves_the_name_empty(auth_client, session, user, master):
    job = apps.create(session, user, company="Acme", job_title="PM")

    use_master(auth_client, job, master)

    assert current(session, job).recipient_name is None


def test_a_long_job_title_still_fits_the_subject(auth_client, session, user, master):
    # plan-reviewer M8b #1: "Bewerbung als " + 200 characters is longer than the column.
    job = apps.create(session, user, company="K" * 200, job_title="P" * 200)
    master_de = letters.create_letter(session, user, "Master DE", "de")

    use_master(auth_client, job, master_de)

    letter = current(session, job)
    assert len(letter.subject) == 200 and letter.subject.startswith("Bewerbung als PPP")
    assert len(letter.title) == 200 and letter.title.endswith(" · Cover letter")


def test_undo_of_copying_deletes_the_untouched_copy(auth_client, session, user, master, job):
    # The pre-filled fields are part of the new copy, not a later edit (D-158).
    response = use_master(auth_client, job, master)
    letter_id = fresh(session, job).cover_letter_id

    undo(auth_client, response)

    assert fresh(session, job).cover_letter_id is None
    assert session.get(CoverLetter, letter_id) is None
    assert apps.events_of(session, job)[-1].type == "created"


def test_undo_of_copying_keeps_an_edited_copy(auth_client, session, user, master, job):
    response = use_master(auth_client, job, master)
    letter = current(session, job)
    edit(session, letter, body="Dear team, I wrote this already.")

    undo(auth_client, response)

    assert session.get(CoverLetter, letter.id) is not None
    assert fresh(session, job).cover_letter_id is None


def test_the_letter_limit_is_a_toast(auth_client, session, user, master, job, monkeypatch):
    monkeypatch.setattr(letters, "MAX_LETTERS_PER_USER", 1)

    response = use_master(auth_client, job, master)

    assert response.status_code == 204
    assert toast(response) == "You have reached the limit of cover letters."
    assert fresh(session, job).cover_letter_id is None


# --- Using an existing copy ------------------------------------------------------------------


def test_an_existing_copy_can_be_used(auth_client, session, user, master, job):
    copy = letters.create_letter(session, user, "Spare copy", "en")

    use_letter(auth_client, job, copy)

    assert fresh(session, job).cover_letter_id == copy.id
    assert fresh(session, copy).application_id == job.id


def test_undo_gives_the_copy_back_its_old_application(auth_client, session, user, master, job):
    copy = letters.create_letter(session, user, "Spare copy", "en")

    response = use_letter(auth_client, job, copy)
    undo(auth_client, response)

    assert fresh(session, job).cover_letter_id is None
    assert fresh(session, copy).application_id is None
    assert session.get(CoverLetter, copy.id) is not None  # an existing copy is never deleted


def test_a_master_cannot_be_used_and_a_locked_copy_cannot_be_linked(
    auth_client, session, user, master, job
):
    copy = letters.create_letter(session, user, "Sent elsewhere", "en")
    letters.lock(session, copy)

    assert use_letter(auth_client, job, master).status_code == 422
    assert use_letter(auth_client, job, copy).status_code == 409
    assert fresh(session, job).cover_letter_id is None


def test_a_copy_must_be_a_master_to_copy_from(auth_client, session, user, master, job):
    copy = letters.create_letter(session, user, "A copy", "en")

    assert use_master(auth_client, job, copy).status_code == 422


def test_a_copy_of_another_application_cannot_be_used(auth_client, session, user, master, job):
    other_job = apps.create(session, user, company="Other", job_title="PM")
    use_master(auth_client, other_job, master)
    theirs = current(session, other_job)

    assert use_letter(auth_client, job, theirs).status_code == 409
    assert fresh(session, job).cover_letter_id is None


def test_one_letter_is_the_current_one_of_one_application(session, user, master, job):
    other_job = apps.create(session, user, company="Other", job_title="PM")
    copy = letters.create_letter(session, user, "Copy", "en")
    apps.link_letter(session, user, job, copy)
    copy.application_id = None  # pretend it is free again; the unique index still holds

    with pytest.raises(apps.CannotLink):
        apps.link_letter(session, user, other_job, copy)


def test_an_archived_job_takes_no_letter(auth_client, session, user, master, job):
    apps.archive(session, user, job)

    assert use_master(auth_client, job, master).status_code == 409
    assert fresh(session, job).cover_letter_id is None


# --- Removing ---------------------------------------------------------------------------------


def test_removing_and_its_undo(auth_client, session, user, master, job):
    use_master(auth_client, job, master)
    letter_id = fresh(session, job).cover_letter_id

    response = remove(auth_client, job)
    assert toast(response) == "Cover letter removed."
    assert fresh(session, job).cover_letter_id is None
    undo(auth_client, response)

    assert fresh(session, job).cover_letter_id == letter_id


def test_a_locked_letter_or_an_archived_job_cannot_be_removed(
    auth_client, session, user, master, job
):
    use_master(auth_client, job, master)
    letter = current(session, job)
    letters.lock(session, letter)

    assert remove(auth_client, job).status_code == 409
    assert fresh(session, job).cover_letter_id == letter.id

    other_job = apps.create(session, user, company="Other", job_title="PM")
    use_master(auth_client, other_job, master)
    apps.archive(session, user, fresh(session, other_job))
    assert remove(auth_client, other_job).status_code == 409


def test_removing_without_a_letter_is_a_conflict(auth_client, job):
    assert remove(auth_client, job).status_code == 409


# --- Another user, logged out, no CSRF --------------------------------------------------------


def test_another_users_letter_or_application_is_not_found(auth_client, session, user, master, job):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    their_master = letters.create_letter(session, other, "Theirs", "en")
    their_copy = letters.create_letter(session, other, "Their copy", "en")
    their_job = apps.create(session, other, company="Theirs", job_title="PM")

    assert use_master(auth_client, job, their_master).status_code == 404
    assert use_letter(auth_client, job, their_copy).status_code == 404
    assert use_master(auth_client, their_job, master).status_code == 404
    assert remove(auth_client, their_job).status_code == 404
    assert fresh(session, job).cover_letter_id is None
    assert fresh(session, their_job).cover_letter_id is None


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "99999999999999999999"])
def test_odd_ids_are_not_found(auth_client, job, master, bad_id):
    response = auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"letter": bad_id},
        headers=headers(auth_client),
    )
    assert response.status_code == 404


def test_logged_out_and_without_csrf_nothing_is_linked(
    client, auth_client, session, user, master, job
):
    url = f"/applications/{job.id}/cover-letter"
    assert auth_client.post(url, data={"master": master.id}).status_code == 403
    assert auth_client.delete(url).status_code == 403
    auth_client.post("/logout", data={"csrf_token": csrf_token(auth_client)})
    assert use_master(client, job, master).headers["HX-Redirect"].startswith("/login")
    assert fresh(session, job).cover_letter_id is None


# --- On the page ------------------------------------------------------------------------------


def test_the_documents_card_shows_the_letter(auth_client, session, user, master, job):
    html = auth_client.get(f"/applications/{job.id}").text
    assert "Copy my master letter (EN)" in html

    use_master(auth_client, job, master)

    html = auth_client.get(f"/applications/{job.id}").text
    letter_id = fresh(session, job).cover_letter_id
    assert "Kranich Health · Cover letter · v1" in html
    assert f'href="/cover-letters/{letter_id}?application={job.id}"' in html
    assert f'hx-delete="/applications/{job.id}/cover-letter"' in html


def test_without_a_master_the_card_points_to_documents(auth_client, job):
    html = auth_client.get(f"/applications/{job.id}").text

    assert "No master letter yet." in html and 'href="/documents?tab=letters"' in html


def test_the_card_offers_free_copies_only(auth_client, session, user, master, job):
    spare = letters.create_letter(session, user, "Spare copy", "en")
    other_job = apps.create(session, user, company="Other", job_title="PM")
    use_master(auth_client, other_job, master)

    html = auth_client.get(f"/applications/{job.id}").text

    assert "Spare copy · v" in html
    assert "Other · Cover letter" not in html
    assert f'<option value="{spare.id}">' in html
