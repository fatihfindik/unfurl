"""The Apply tab: Prepare documents and Final check (SPEC §6.7, §12 M9a; D-164, D-165)."""

import re

import pytest

from app.models import Application, CoverLetter, CVDocument
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import document_service as docs
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
