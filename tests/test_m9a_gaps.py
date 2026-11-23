"""M9a: the authorization and error cases the feature tests don't cover yet: odd tab and
doc values, broken rows that point at another user's document, archived jobs, and
requests without a session or without CSRF (SPEC §6.7, §10.1; D-164…D-167)."""

import pytest
from sqlmodel import select

from app.models import Application, ApplicationDocument, CoverLetter, CVDocument
from app.services import application_service as apps
from app.services import cover_letter_service as letters
from app.services import document_service as docs
from app.services.auth_service import create_user
from app.services.profile_service import get_or_create_profile
from tests.test_application_documents import fresh, headers, move, use_master

SCRIPT = "<script>alert(1)</script>"


@pytest.fixture
def master(session, user) -> CVDocument:
    get_or_create_profile(session, user)
    return docs.create_cv(session, user, "Master CV", "en", "classic")


@pytest.fixture
def job(session, user) -> Application:
    return apps.create(session, user, company="Kranich Health", job_title="Senior PM")


@pytest.fixture
def theirs(session) -> tuple[CVDocument, CoverLetter]:
    """Another user with a CV and a cover letter, both linked to their own job."""
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    get_or_create_profile(session, other)
    their_job = apps.create(session, other, company="Their Co", job_title="PM")
    cv_master = docs.create_cv(session, other, "Their master", "en", "classic")
    cv = docs.copy_for_application(session, other, cv_master, their_job, "Otto secret CV")
    letter_master = letters.create_letter(session, other, "Their master letter", "en")
    letter = letters.copy_for_application(
        session, other, letter_master, their_job, "Otto secret letter"
    )
    letter.body = "Otto's private words."
    letter.recipient_company = "Otto Secret GmbH"
    session.add(letter)
    session.commit()
    return fresh(session, cv), fresh(session, letter)


def point_at(session, job, cv=None, letter=None) -> Application:
    """A broken row: the job's cv_id / cover_letter_id set straight in the database,
    the way no route allows."""
    job = fresh(session, job)
    if cv is not None:
        job.cv_id = cv.id
    if letter is not None:
        job.cover_letter_id = letter.id
    session.add(job)
    session.commit()
    return fresh(session, job)


def shown_tab(html: str) -> str:
    if 'id="apply"' in html:
        return "apply"
    if 'id="progress"' in html:
        return "progress"
    return "details"


def assert_none_of_theirs(html, cv, letter):
    for text in ("Otto secret CV", "Otto secret letter", "Otto's private words", "Otto Secret"):
        assert text not in html, text
    assert f"/cvs/{cv.id}" not in html and f"/cover-letters/{letter.id}" not in html


# --- Odd tab values (D-164) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "tab",
    [
        "DETAILS",
        "details ",
        "details%00",
        "progress",  # not the first tab of a saved job
        "%3Cscript%3Ealert(1)%3C%2Fscript%3E",
        "details%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E",
        "x" * 8000,
    ],
)
def test_an_odd_tab_shows_the_first_tab_and_is_not_reflected(auth_client, job, tab):
    response = auth_client.get(f"/applications/{job.id}?tab={tab}")

    assert response.status_code == 200
    html = response.text
    assert shown_tab(html) == "apply"
    assert SCRIPT not in html and "alert(1)" not in html
    assert "x" * 100 not in html
    assert html.count('aria-current="page"') == 1


def test_tab_apply_cannot_bring_back_mark_as_applied_on_a_sent_job(
    auth_client, session, job, master
):
    use_master(auth_client, job, master)
    move(auth_client, job, "applied")

    html = auth_client.get(f"/applications/{job.id}?tab=apply").text

    assert shown_tab(html) == "progress" and "I have applied" not in html


def test_a_repeated_tab_param_is_one_of_the_tabs(auth_client, job):
    response = auth_client.get(f"/applications/{job.id}?tab=details&tab=%3Cb%3E")

    assert response.status_code == 200
    assert shown_tab(response.text) in ("apply", "details") and "<b>" not in response.text


# --- Odd doc values in the drawer (D-166) -------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "doc=" + "c" * 8000,
        "doc=cv%00",
        "doc=cv%20",
        "doc=%3Cscript%3Ealert(1)%3C%2Fscript%3E",
        "doc[]=cv",
        "",
    ],
)
def test_an_odd_doc_is_refused_and_not_reflected(auth_client, session, job, master, query):
    use_master(auth_client, job, master)

    response = auth_client.get(f"/applications/{job.id}/drawer?{query}")

    assert response.status_code == 422
    assert SCRIPT not in response.text and "c" * 100 not in response.text


@pytest.mark.parametrize(
    "query", ["doc=cv&doc=%3Cscript%3E", "doc=%3Cscript%3E&doc=cv", "doc=cv&doc=cover_letter"]
)
def test_a_repeated_doc_never_fails_or_reflects(auth_client, session, job, master, query):
    use_master(auth_client, job, master)

    response = auth_client.get(f"/applications/{job.id}/drawer?{query}")

    # Which value wins is up to the framework; it must be a clean answer either way.
    assert response.status_code in (200, 404, 422)
    assert "<script>" not in response.text


# --- Broken rows: a job that points at another user's document ------------------------------


def test_the_drawer_never_shows_another_users_cv(auth_client, session, job, theirs):
    cv, letter = theirs
    job = point_at(session, job, cv=cv)

    response = auth_client.get(f"/applications/{job.id}/drawer?doc=cv")

    assert response.status_code == 404
    assert_none_of_theirs(response.text, cv, letter)


def test_the_drawer_never_shows_another_users_letter(auth_client, session, job, master, theirs):
    cv, letter = theirs
    use_master(auth_client, job, master)
    job = point_at(session, job, letter=letter)

    response = auth_client.get(f"/applications/{job.id}/drawer?doc=cover_letter")
    assert response.status_code == 404
    assert_none_of_theirs(response.text, cv, letter)

    # The own CV still opens, without a tab to the letter that isn't theirs to see.
    own = auth_client.get(f"/applications/{job.id}/drawer?doc=cv")
    assert own.status_code == 200
    assert "drawer?doc=cover_letter" not in own.text
    assert_none_of_theirs(own.text, cv, letter)


def test_the_final_check_never_shows_another_users_letter(
    auth_client, session, job, master, theirs
):
    cv, letter = theirs
    use_master(auth_client, job, master)
    job = point_at(session, job, letter=letter)

    html = auth_client.get(f"/applications/{job.id}").text

    assert 'data-check="letter_linked" data-status="warn"' in html
    assert 'data-check="letter_recipient"' not in html
    assert "Cover letter (PDF)" not in html
    assert_none_of_theirs(html, cv, letter)


def test_the_final_check_waits_when_the_cv_is_another_users(auth_client, session, job, theirs):
    cv, letter = theirs
    job = point_at(session, job, cv=cv, letter=letter)

    html = auth_client.get(f"/applications/{job.id}").text

    assert "Prepare your CV first." in html and "I have applied" not in html
    assert_none_of_theirs(html, cv, letter)


@pytest.mark.parametrize("tab", ["", "?tab=details"])
def test_progress_and_details_never_show_another_users_documents(
    auth_client, session, job, theirs, tab
):
    cv, letter = theirs
    move(auth_client, job, "applied")
    job = point_at(session, job, cv=cv, letter=letter)

    response = auth_client.get(f"/applications/{job.id}{tab}")

    assert response.status_code == 200
    assert_none_of_theirs(response.text, cv, letter)


def test_mark_as_applied_never_locks_another_users_documents(auth_client, session, job, theirs):
    cv, letter = theirs
    job = point_at(session, job, cv=cv, letter=letter)

    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": "applied"}, headers=headers(auth_client)
    )

    assert response.status_code == 204
    assert not fresh(session, cv).is_locked and not fresh(session, letter).is_locked
    assert session.exec(select(ApplicationDocument)).all() == []


# --- Archived jobs --------------------------------------------------------------------------------


def test_the_drawer_still_previews_an_archived_jobs_cv(auth_client, session, user, job, master):
    use_master(auth_client, job, master)
    apps.set_status(session, user, fresh(session, job), "archived")

    response = auth_client.get(f"/applications/{job.id}/drawer?doc=cv")

    assert response.status_code == 200
    assert "<form" not in response.text  # still read-only


@pytest.mark.parametrize(
    ("status", "first"), [("saved", "apply"), ("applied", "progress"), ("rejected", "progress")]
)
def test_an_archived_job_keeps_its_tabs(auth_client, session, user, job, status, first):
    if status != "saved":
        move(auth_client, job, status)
    apps.set_status(session, user, fresh(session, job), "archived")

    assert shown_tab(auth_client.get(f"/applications/{job.id}").text) == first
    assert shown_tab(auth_client.get(f"/applications/{job.id}?tab=details").text) == "details"
    assert shown_tab(auth_client.get(f"/applications/{job.id}?tab=nope").text) == first


def test_an_archived_job_of_another_user_is_not_found(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    their_job = apps.create(session, other, company="Their Co", job_title="PM")
    apps.set_status(session, other, their_job, "archived")

    for path in ("", "?tab=details", "/drawer?doc=cv"):
        response = auth_client.get(f"/applications/{their_job.id}{path}")
        assert response.status_code == 404, path
        assert "Their Co" not in response.text


# --- No session, no CSRF ------------------------------------------------------------------------


def test_logged_out_htmx_drawer_asks_for_a_full_page_login(client, job):
    response = client.get(
        f"/applications/{job.id}/drawer?doc=cv",
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/applications/{job.id}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 200 and response.text == ""
    # After login: back to the page, not to the drawer's piece of HTML.
    assert response.headers["HX-Redirect"] == f"/login?next=/applications/{job.id}"


@pytest.mark.parametrize(
    "current", ["https://evil.example/steal", "//evil.example/x", "javascript:alert(1)"]
)
def test_logged_out_htmx_drawer_never_sends_back_to_another_site(client, job, current):
    response = client.get(
        f"/applications/{job.id}/drawer?doc=cv",
        headers={"HX-Request": "true", "HX-Current-URL": current},
        follow_redirects=False,
    )

    location = response.headers["HX-Redirect"]
    assert location.startswith("/login") and "evil" not in location and "javascript" not in location


def test_logged_out_tab_goes_to_login(client, job):
    response = client.get(f"/applications/{job.id}?tab=details", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_mark_as_applied_without_csrf_changes_nothing(auth_client, session, user, job, master):
    use_master(auth_client, job, master)
    events_before = len(apps.events_of(session, fresh(session, job)))

    response = auth_client.post(
        f"/applications/{job.id}/status", data={"status": "applied"}, headers={"HX-Request": "true"}
    )

    assert response.status_code == 403
    job = fresh(session, job)
    assert (job.status, job.applied_at) == ("saved", None)
    assert not session.get(CVDocument, job.cv_id).is_locked
    assert apps.sent_documents(session, user, job) == []
    assert len(apps.events_of(session, job)) == events_before


def test_i_got_an_interview_without_csrf_changes_nothing(auth_client, session, job):
    move(auth_client, job, "applied")

    response = auth_client.post(
        f"/applications/{job.id}/status",
        data={"status": "interview"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 403
    assert fresh(session, job).status == "applied"
