"""The Interview prep tab and its requests (SPEC §6.7, §12 M14a; D-176…D-180)."""

import datetime as dt
import re

import pytest
from sqlmodel import select

from app.models import Application, InterviewQuestion
from app.services import application_service as apps
from app.services import interview_prep_service as prep
from app.services.auth_service import create_user
from tests.test_application_documents import fresh, headers


@pytest.fixture
def job(session, user) -> Application:
    job = apps.create(session, user, company="Kranich Health", job_title="Senior PM")
    job.status = "applied"
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def tab(client, job, query="?tab=interview"):
    response = client.get(f"/applications/{job.id}{query}")
    assert response.status_code == 200
    return response.text


def tab_links(html: str) -> list[str]:
    nav = re.search(r'<nav aria-label="Application sections".*?</nav>', html, re.S).group(0)
    links = re.findall(r"<a .*?</a>", nav, re.S)
    return [re.sub(r"<[^>]+>|\s+\(interview stage\)", "", link).strip() for link in links]


def start(client, job, starters="1"):
    return client.post(
        f"/applications/{job.id}/prep/start", data={"starters": starters}, headers=headers(client)
    )


def set_status(session, job, status, **fields):
    job.status = status
    for name, value in fields.items():
        setattr(job, name, value)
    session.add(job)
    session.commit()


# --- The tab (D-176) ----------------------------------------------------------------


@pytest.mark.parametrize("status", ["applied", "interview", "offer", "rejected"])
def test_the_tab_is_there_from_applied_on(auth_client, session, job, status):
    set_status(session, job, status)

    html = tab(auth_client, job, "")

    assert tab_links(html) == ["Progress", "Interview prep", "Job &amp; details"]
    assert f'href="/applications/{job.id}?tab=interview"' in html


def test_a_saved_job_has_no_tab_and_the_address_shows_apply(auth_client, session, job):
    set_status(session, job, "saved")

    html = tab(auth_client, job)

    assert tab_links(html) == ["Apply", "Job &amp; details"]
    assert 'id="interview-prep"' not in html
    assert 'id="prepare"' in html  # the Apply tab


def test_the_dot_shows_only_in_the_interview_stage(auth_client, session, job):
    assert "(interview stage)" not in tab(auth_client, job)

    set_status(session, job, "interview")
    html = tab(auth_client, job)

    assert '<span class="sr-only"> (interview stage)</span>' in html


def test_the_tab_is_selected_and_the_page_reloads_itself_on_it(auth_client, job):
    html = tab(auth_client, job)

    assert re.search(r'<a href="[^"]*\?tab=interview"\s+aria-current="page"', html)
    assert f'hx-get="/applications/{job.id}?tab=interview"' in html


def test_before_starting_it_offers_the_two_buttons(auth_client, job):
    html = tab(auth_client, job)

    assert "Start interview prep" in html and "Start with an empty list" in html
    assert 'id="prep-likely"' not in html


def test_opening_the_tab_writes_nothing(auth_client, session, job):
    for method in (auth_client.get, auth_client.head):
        assert method(f"/applications/{job.id}?tab=interview").status_code == 200

    assert session.exec(select(InterviewQuestion)).all() == []
    assert fresh(session, job).interview_prep_started_at is None


def test_the_interview_line(auth_client, session, job):
    assert "No interview date yet." in tab(auth_client, job)

    set_status(
        session,
        job,
        "interview",
        interview_at=dt.datetime(2026, 12, 2, 13, 0, tzinfo=dt.UTC),  # 14:00 in Vienna
        interview_type="video",
        interview_with="Mira Huber",
    )
    html = tab(auth_client, job)

    assert "Wed, 2 Dec 2026, 14:00 · Video call · with Mira Huber" in html
    assert f'data-interview-open="{job.id}"' in html


def test_an_archived_job_shows_its_prep_but_no_buttons(auth_client, session, job):
    set_status(session, job, "applied", archived=True)

    html = tab(auth_client, job)

    assert "Restore it to edit your prep." in html
    assert "Start interview prep" not in html


# --- Start (D-178) ------------------------------------------------------------------


def test_start_adds_the_starter_set_and_reloads_the_page(auth_client, session, job):
    response = start(auth_client, job)

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "application-changed"
    html = tab(auth_client, job)
    assert "Why do you want to work at Kranich Health?" in html
    assert "0 of 12 answered" in html
    assert "What are the next steps in the process?" in html
    assert "Start interview prep" not in html


def test_start_twice_adds_the_set_once(auth_client, session, job):
    start(auth_client, job)
    assert start(auth_client, job).status_code == 204

    assert len(session.exec(select(InterviewQuestion)).all()) == 16


def test_start_with_an_empty_list(auth_client, session, job):
    start(auth_client, job, starters="0")

    html = tab(auth_client, job)
    assert session.exec(select(InterviewQuestion)).all() == []
    assert 'id="prep-likely"' in html and "No questions yet." in html


def test_start_on_an_archived_job_is_409(auth_client, session, job):
    set_status(session, job, "applied", archived=True)

    assert start(auth_client, job).status_code == 409
    assert fresh(session, job).interview_prep_started_at is None


def test_start_on_another_users_job_is_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")

    assert start(auth_client, theirs).status_code == 404
    assert fresh(session, theirs).interview_prep_started_at is None


def test_start_needs_the_csrf_token(auth_client, session, job):
    response = auth_client.post(f"/applications/{job.id}/prep/start", data={"starters": "1"})

    assert response.status_code == 403
    assert fresh(session, job).interview_prep_started_at is None


def test_start_with_too_many_fields_is_400(auth_client, job):
    data = {f"field{i}": "x" for i in range(20)}

    response = auth_client.post(
        f"/applications/{job.id}/prep/start", data=data, headers=headers(auth_client)
    )

    assert response.status_code == 400


def test_the_prep_shows_saved_answers_and_notes(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "role", "Why this role?")
    prep.save_answer(session, job, question, "Because <b>growth</b>")
    prep.save_notes(session, job, "Meet Mira at 10")

    html = tab(auth_client, job)

    assert "About the role" in html and "1 of 1 answered" in html
    assert "Because &lt;b&gt;growth&lt;/b&gt;" in html  # escaped, never HTML
    assert "Meet Mira at 10" in html
