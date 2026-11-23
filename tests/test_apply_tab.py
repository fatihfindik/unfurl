"""The Apply tab: Prepare documents and Final check (SPEC §6.7, §12 M9a; D-164, D-165)."""

import datetime as dt
import json
import re

import pytest

from app.models import Application, CoverLetter, CVDocument
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.test_application_documents import fresh, headers, move, use_master


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def master_letter(session, user) -> CoverLetter:
    get_or_create_profile(session, user)
    return letters.create_letter(session, user, "Master letter", "en")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


def apply_tab(client, job) -> str:
    response = client.get(f"/applications/{job.id}")
    assert response.status_code == 200
    return response.text


def part(html: str, element_id: str) -> str:
    """The element with this id (a <details> or a <section>), as HTML."""
    match = re.search(rf'<(details|section|div) id="{element_id}".*?</\1>', html, re.S)
    assert match, element_id
    return match.group(0)


# --- Prepare documents -------------------------------------------------------------------------


def test_without_a_cv_prepare_is_open_and_final_check_waits(auth_client, job, master):
    html = apply_tab(auth_client, job)

    prepare = part(html, "prepare")
    assert prepare.startswith('<details id="prepare"') and " open>" in prepare.split("\n", 1)[0]
    assert "Copy my master CV (EN)" in prepare
    assert "Prepare your CV first." in part(html, "final-check")


def test_without_a_master_prepare_links_to_documents(auth_client, job):
    prepare = part(apply_tab(auth_client, job), "prepare")

    assert "No master CV yet." in prepare and 'href="/documents"' in prepare
    assert "No master letter yet." in prepare and 'href="/documents?tab=letters"' in prepare


def test_copying_from_prepare_never_changes_the_master(auth_client, session, job, master):
    before = (master.title, master.content, master.version, master.status, master.updated_at)

    response = use_master(auth_client, job, master)

    assert response.status_code == 204
    master = fresh(session, master)
    assert (master.title, master.content, master.version, master.status, master.updated_at) == (
        before
    )
    assert fresh(session, job).cv_id not in (None, master.id)


def test_with_a_cv_prepare_folds_up_and_final_check_opens(
    auth_client, session, job, master, master_letter
):
    use_master(auth_client, job, master)

    html = apply_tab(auth_client, job)

    first_line = part(html, "prepare").split("\n", 1)[0]
    assert " open" not in first_line
    assert "CV v1, no cover letter" in html
    assert "Prepare your CV first." not in part(html, "final-check")


def test_the_summary_names_the_letter_too(auth_client, session, job, master, master_letter):
    use_master(auth_client, job, master)
    auth_client.post(
        f"/applications/{job.id}/cover-letter",
        data={"master": master_letter.id},
        headers=headers(auth_client),
    )

    assert "CV v1, cover letter v1" in apply_tab(auth_client, job)


def test_every_id_on_the_page_is_unique(auth_client, session, user, job, master, master_letter):
    # Two spare copies so both "Use an existing copy" selects are on the page, twice.
    docs.create_cv(session, user, "Spare CV", "en", "classic")
    letters.create_letter(session, user, "Spare letter", "en")

    html = apply_tab(auth_client, job)

    ids = re.findall(r'\sid="([^"]+)"', html)
    assert "prepare-link-cv" in ids and "card-link-cv" in ids
    assert "prepare-link-letter" in ids and "card-link-letter" in ids
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_an_archived_job_shows_no_actions(auth_client, session, user, job, master):
    apps.set_status(session, user, job, "archived")

    html = apply_tab(auth_client, job)

    assert "Restore it to prepare documents." in html
    assert "Copy my master CV" not in part(html, "prepare")


def test_a_sent_cv_stays_after_going_back_to_saved(auth_client, session, job, master):
    use_master(auth_client, job, master)
    move(auth_client, job, "applied")
    move(auth_client, job, "saved")

    prepare = part(apply_tab(auth_client, job), "prepare")

    assert "Sent · locked" in prepare
    assert "Remove" not in prepare and "Copy my master CV" not in prepare


# --- Final check (D-151, D-165) ------------------------------------------------------------------


def link_letter(client, job, master_letter):
    return client.post(
        f"/applications/{job.id}/cover-letter",
        data={"master": master_letter.id},
        headers=headers(client),
    )


def mark_as_applied(client, job):
    return client.post(
        f"/applications/{job.id}/status", data={"status": "applied"}, headers=headers(client)
    )


def test_final_check_lists_the_checks_and_the_pdfs(auth_client, session, job, master):
    use_master(auth_client, job, master)

    check = part(apply_tab(auth_client, job), "final-check")

    assert 'data-check="cv_linked" data-status="ok"' in check
    assert 'data-check="letter_linked" data-status="warn"' in check
    assert re.search(r"\d of \d passed", check)
    cv_id = fresh(session, job).cv_id
    assert f'href="/cvs/{cv_id}/pdf"' in check and ">CV (PDF)<" in check
    assert "Cover letter (PDF)" not in check  # no letter linked
    assert f'hx-post="/applications/{job.id}/status"' in check
    assert "I have applied · mark as applied" in check


def test_the_letter_pdf_is_offered_when_a_letter_is_linked(
    auth_client, session, job, master, master_letter
):
    use_master(auth_client, job, master)
    link_letter(auth_client, job, master_letter)

    check = part(apply_tab(auth_client, job), "final-check")

    assert f'href="/cover-letters/{fresh(session, job).cover_letter_id}/pdf"' in check


def test_every_fix_link_opens_its_page(auth_client, session, user, job, master, master_letter):
    # A CV without name or email (empty profile), a letter in another language and
    # for another company, a closed posting: every kind of link shows up.
    use_master(auth_client, job, master)
    de_master = letters.create_letter(session, user, "Brief", "de")
    link_letter(auth_client, job, de_master)
    letter = session.get(CoverLetter, fresh(session, job).cover_letter_id)
    letter.recipient_company = "Nordbau"
    letter.body = ""
    session.add(letter)
    session.commit()
    job = fresh(session, job)
    job.posting_closes_at = dt.date(2020, 1, 1)
    session.add(job)
    session.commit()

    html = apply_tab(auth_client, job)
    links = set(re.findall(r'data-status="(?:warn|missing)".*?<a href="([^"]+)"', html, re.S))

    assert links  # there is something to fix
    for url in links:
        path, _, anchor = url.partition("#")
        page = auth_client.get(path)
        assert page.status_code == 200, url
        if anchor:
            assert f'id="{anchor}"' in page.text, url


def test_mark_as_applied_shows_progress_and_locks_and_undo_reverts(
    auth_client, session, user, job, master, master_letter
):
    use_master(auth_client, job, master)
    link_letter(auth_client, job, master_letter)

    response = mark_as_applied(auth_client, job)

    assert response.status_code == 204
    job = fresh(session, job)
    assert job.status == "applied"
    assert session.get(CVDocument, job.cv_id).is_locked
    assert session.get(CoverLetter, job.cover_letter_id).is_locked
    assert len(apps.sent_documents(session, user, job)) == 2
    html = apply_tab(auth_client, job)
    assert 'id="progress"' in html and 'id="apply"' not in html

    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    undo = auth_client.post(
        "/applications/undo", data={"token": token}, headers=headers(auth_client)
    )

    assert undo.status_code in (200, 204)
    job = fresh(session, job)
    assert job.status == "saved"
    assert not session.get(CVDocument, job.cv_id).is_locked
    assert not session.get(CoverLetter, job.cover_letter_id).is_locked
    assert 'id="apply"' in apply_tab(auth_client, job)


def test_applying_again_after_going_back_adds_no_second_record(
    auth_client, session, user, job, master
):
    use_master(auth_client, job, master)
    mark_as_applied(auth_client, job)
    move(auth_client, job, "saved")

    mark_as_applied(auth_client, job)

    assert len(apps.sent_documents(session, user, fresh(session, job))) == 1


def test_an_archived_job_has_no_mark_as_applied(auth_client, session, user, job, master):
    use_master(auth_client, job, master)
    apps.set_status(session, user, fresh(session, job), "archived")

    check = part(apply_tab(auth_client, job), "final-check")

    assert "I have applied" not in check and "CV (PDF)" not in check


# --- Another user's application (acceptance) ----------------------------------------------------


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("cv", "master"),
        ("cover-letter", "master_letter"),
        ("status", None),
    ],
)
def test_apply_actions_on_another_users_job_are_not_found(
    auth_client, session, master, master_letter, path, data
):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Their Co", job_title="PM")
    form = (
        {"status": "applied"}
        if data is None
        else {"master": {"master": master, "master_letter": master_letter}[data].id}
    )

    response = auth_client.post(
        f"/applications/{theirs.id}/{path}", data=form, headers=headers(auth_client)
    )

    assert response.status_code == 404
    theirs = fresh(session, theirs)
    assert (theirs.status, theirs.cv_id, theirs.cover_letter_id) == ("saved", None, None)
    assert auth_client.get(f"/applications/{theirs.id}").status_code == 404
