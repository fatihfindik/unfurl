"""The document drawer on the application page: a read-only preview of the CV or the
cover letter used for the job (SPEC §6.7, §12 M9a; D-166)."""

import re

import pytest

from app.models import Application, CoverLetter, CVDocument
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.test_application_documents import fresh, move


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def cv(session, user, job) -> CVDocument:
    get_or_create_profile(session, user)
    master = docs.create_cv(session, user, "Master CV", "en", "classic")
    copy = docs.copy_for_application(session, user, master, job, "Kranich · CV")
    apps.link_cv(session, user, job, copy)
    return fresh(session, copy)


@pytest.fixture
def letter(session, user, job) -> CoverLetter:
    get_or_create_profile(session, user)
    master = letters.create_letter(session, user, "Master letter", "en")
    copy = letters.copy_for_application(session, user, master, job, "Kranich · Letter")
    copy.body = "I build patient apps.\n\n<script>alert(1)</script>"
    session.add(copy)
    session.commit()
    apps.link_letter(session, user, job, copy)
    return fresh(session, copy)


def drawer(client, job, doc):
    return client.get(f"/applications/{job.id}/drawer?doc={doc}")


# --- The preview --------------------------------------------------------------------------------


def test_the_cv_preview_has_the_editor_and_the_pdf(auth_client, job, cv):
    response = drawer(auth_client, job, "cv")

    assert response.status_code == 200
    html = response.text
    assert "Kranich · CV · v1" in html and "Draft" in html
    assert f'href="/cvs/{cv.id}?application={job.id}"' in html and "Open full editor" in html
    assert f'href="/cvs/{cv.id}/pdf"' in html and "Download PDF" in html
    assert "<form" not in html and "<textarea" not in html  # read-only (D-166)


def test_the_letter_preview_shows_its_text_escaped(auth_client, job, cv, letter):
    html = drawer(auth_client, job, "cover_letter").text

    assert "I build patient apps." in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert f'href="/cover-letters/{letter.id}/pdf"' in html


def test_tabs_only_when_both_documents_are_there(auth_client, session, user, job, cv, letter):
    both = drawer(auth_client, job, "cv").text
    assert f'data-dialog-url="/applications/{job.id}/drawer?doc=cover_letter"' in both

    apps.unlink_letter(session, user, fresh(session, job))
    only_cv = drawer(auth_client, job, "cv").text
    assert "drawer?doc=cover_letter" not in only_cv


def test_a_sent_document_says_so(auth_client, job, cv):
    move(auth_client, job, "applied")

    html = drawer(auth_client, job, "cv").text

    assert "Sent · locked" in html and "This is what you sent." in html


# --- Errors and access ------------------------------------------------------------------------


def test_no_such_document_for_the_job_is_not_found(auth_client, job, cv):
    assert drawer(auth_client, job, "cover_letter").status_code == 404


@pytest.mark.parametrize("doc", ["", "letter", "CV", "<b>"])
def test_an_unknown_document_kind_is_refused(auth_client, job, cv, doc):
    assert drawer(auth_client, job, doc).status_code == 422


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**70)])
def test_odd_ids_are_not_found(auth_client, cv, bad_id):
    assert auth_client.get(f"/applications/{bad_id}/drawer?doc=cv").status_code == 404


def test_another_users_job_is_not_found(auth_client, session, cv):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    theirs = apps.create(session, other, company="Their Co", job_title="PM")
    master = docs.create_cv(session, other, "Their master", "en", "classic")
    copy = docs.copy_for_application(session, other, master, theirs, "Their CV")
    apps.link_cv(session, other, theirs, copy)

    response = drawer(auth_client, theirs, "cv")

    assert response.status_code == 404
    assert "Their CV" not in response.text


def test_logged_out_goes_to_login(client, job, cv):
    response = client.get(f"/applications/{job.id}/drawer?doc=cv", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_at_most_60_a_minute(auth_client, job, cv):
    answers = [drawer(auth_client, job, "cv").status_code for _ in range(61)]

    assert 429 not in answers[:60] and answers[60] == 429


# --- On the page --------------------------------------------------------------------------------


def test_prepare_opens_the_drawer_and_the_page_has_it(auth_client, job, cv, letter):
    html = auth_client.get(f"/applications/{job.id}").text

    prepare = re.search(r'<details id="prepare".*?</details>', html, re.S).group(0)
    assert f'data-dialog-url="/applications/{job.id}/drawer?doc=cv"' in prepare
    assert f'data-dialog-url="/applications/{job.id}/drawer?doc=cover_letter"' in prepare
    assert "Preview &amp; edit" in prepare
    # The drawer is outside the part that reloads (hx-select="#application-page"), so
    # a change on the page doesn't close it; the preview styles are loaded.
    assert 'id="document-drawer-body"' in html
    reloaded = auth_client.get(f"/applications/{job.id}").text
    page_part = reloaded.split('<div id="application-page"', 1)[1]
    assert page_part.index('id="document-drawer"') > page_part.index("</aside>")
    assert "css/cv_base" in html and "css/cover_letter" in html
