"""The Interview prep tab and its requests (SPEC §6.7, §12 M14a; D-176…D-180)."""

import datetime as dt
import json
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


# --- Writing (D-179) ----------------------------------------------------------------


def post(client, job, path, data=None):
    return client.post(
        f"/applications/{job.id}/prep/{path}", data=data or {}, headers=headers(client)
    )


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def test_add_a_likely_question_draws_the_card(auth_client, session, job):
    response = post(
        auth_client, job, "questions", {"kind": "likely", "category": "role", "text": " Why PM? "}
    )

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="prep-likely"')
    assert "About the role" in response.text and "Why PM?" in response.text
    assert "HX-Trigger" not in response.headers  # no page reload (D-179)
    assert fresh(session, job).interview_prep_started_at is not None


def test_add_a_question_to_ask_draws_its_card(auth_client, job):
    response = post(auth_client, job, "questions", {"kind": "to_ask", "text": "Team size?"})

    assert response.text.lstrip().startswith('<section id="prep-to-ask"')
    assert "Team size?" in response.text


@pytest.mark.parametrize(
    "data",
    [{"kind": "likely", "text": "Q?"}, {"kind": "to_ask", "category": "role", "text": "Q?"},
     {"kind": "nope", "text": "Q?"}, {"kind": "to_ask", "text": "  "},
     {"kind": "to_ask", "text": "x" * 301}],
)  # fmt: skip
def test_add_refuses_bad_values_with_a_toast(auth_client, session, job, data):
    response = post(auth_client, job, "questions", data)

    assert response.status_code == 422
    assert toast_of(response)["message"] == "This can't be saved. Check the text and try again."
    assert session.exec(select(InterviewQuestion)).all() == []


def test_add_stops_at_100_questions(auth_client, session, user, job):
    session.add_all(
        InterviewQuestion(user_id=user.id, application_id=job.id, kind="to_ask", text="Q?")
        for _ in range(100)
    )
    session.commit()

    response = post(auth_client, job, "questions", {"kind": "to_ask", "text": "One more?"})

    assert response.status_code == 409
    assert toast_of(response)["message"] == "An application can have up to 100 questions."


def test_edit_form_cancel_and_save(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "general", "Old?")
    base = f"/applications/{job.id}/prep/questions/{question.id}"

    form = auth_client.get(f"{base}/edit")
    assert form.status_code == 200 and 'value="Old?"' in form.text
    assert 'name="category"' in form.text

    row = auth_client.get(base)
    assert row.text.lstrip().startswith(f'<li id="prep-question-{question.id}"')

    data = {"text": "New?", "category": "company"}
    saved = post(auth_client, job, f"questions/{question.id}", data)
    assert saved.status_code == 200 and "About the company" in saved.text
    assert (fresh(session, question).text, fresh(session, question).category) == (
        "New?",
        "company",
    )


def test_a_question_to_ask_has_no_category_in_the_edit_form(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Ask?")

    form = auth_client.get(f"/applications/{job.id}/prep/questions/{question.id}/edit")

    assert 'name="category"' not in form.text


def test_saving_an_answer_says_saved_and_updates_the_count(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "general", "Q?")
    prep.add(session, user, job, "likely", "general", "Q2?")

    response = post(auth_client, job, f"questions/{question.id}/answer", {"answer": "My answer"})

    assert response.status_code == 200
    assert response.text.strip().startswith("Saved")
    assert 'hx-swap-oob="true">1 of 2 answered' in response.text
    assert "HX-Trigger" not in response.headers
    assert fresh(session, question).answer == "My answer"


def test_saving_their_answer_has_no_count(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Ask?")

    response = post(auth_client, job, f"questions/{question.id}/answer", {"answer": "Ten people"})

    assert "prep-answered" not in response.text
    assert fresh(session, question).answer == "Ten people"


def test_an_answer_too_long_is_refused(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "general", "Q?")

    response = post(auth_client, job, f"questions/{question.id}/answer", {"answer": "x" * 5001})

    assert response.status_code == 422
    assert fresh(session, question).answer is None


def test_the_asked_tick(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Ask?")
    path = f"questions/{question.id}/asked"

    # Like the browser: the hidden "0" always, the checkbox's "1" when ticked.
    assert (
        auth_client.post(
            f"/applications/{job.id}/prep/{path}",
            data={"asked": ["0", "1"]},
            headers=headers(auth_client),
        ).status_code
        == 204
    )
    assert fresh(session, question).asked is True

    post(auth_client, job, path, {"asked": "0"})
    assert fresh(session, question).asked is False


def test_a_likely_question_has_no_tick(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "general", "Q?")

    response = post(auth_client, job, f"questions/{question.id}/asked", {"asked": "1"})

    assert response.status_code == 422


def test_saving_the_notes(auth_client, session, job):
    start(auth_client, job, starters="0")  # the notes field is shown once started
    response = post(auth_client, job, "notes", {"interview_notes": "Meet Mira\nBring a laptop"})

    assert response.status_code == 200 and response.text.strip() == "Saved"
    assert fresh(session, job).interview_notes == "Meet Mira\nBring a laptop"
    assert "Meet Mira\nBring a laptop</textarea>" in tab(auth_client, job)


def test_notes_too_long_are_refused(auth_client, session, job):
    assert post(auth_client, job, "notes", {"interview_notes": "x" * 10_001}).status_code == 422
    assert fresh(session, job).interview_notes is None


def test_delete_draws_the_card_and_undo_brings_it_back(auth_client, session, user, job):
    question = prep.add(session, user, job, "likely", "behavioural", "Conflict?")
    prep.save_answer(session, job, question, "The story")

    response = post(auth_client, job, f"questions/{question.id}/delete")

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="prep-likely"')
    assert "Conflict?" not in response.text
    toast = toast_of(response)
    assert toast["message"] == "Question deleted." and toast["undo_url"] == "/undo/delete"
    assert session.exec(select(InterviewQuestion)).all() == []

    undo = auth_client.post(
        "/undo/delete", data={"token": toast["undo_token"]}, headers=headers(auth_client)
    )
    assert undo.status_code == 204
    back = session.exec(select(InterviewQuestion)).all()
    assert [(q.text, q.answer) for q in back] == [("Conflict?", "The story")]


def test_undo_on_an_archived_job_restores_nothing(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Q?")
    token = toast_of(post(auth_client, job, f"questions/{question.id}/delete"))["undo_token"]
    set_status(session, job, "applied", archived=True)

    auth_client.post("/undo/delete", data={"token": token}, headers=headers(auth_client))

    assert session.exec(select(InterviewQuestion)).all() == []


# --- Every write: archived, other users, CSRF (D-176, D-179) ------------------------


def write_requests(job, question):
    """Every request that changes the prep, with a valid body."""
    q = f"questions/{question.id}"
    return [
        ("start", {"starters": "1"}),
        ("questions", {"kind": "to_ask", "text": "New?"}),
        (q, {"text": "Changed?"}),
        (f"{q}/answer", {"answer": "A"}),
        (f"{q}/asked", {"asked": "1"}),
        (f"{q}/delete", {}),
        ("notes", {"interview_notes": "N"}),
    ]


def snapshot(session, job):
    session.expire_all()
    rows = session.exec(select(InterviewQuestion).order_by(InterviewQuestion.id)).all()
    application = session.get(Application, job.id)
    return (
        [(q.text, q.answer, q.asked) for q in rows],
        application.interview_notes,
        application.interview_prep_started_at,
    )


def test_every_write_on_an_archived_job_is_409(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Q?")
    job.interview_prep_started_at = None  # so Start would do something too
    set_status(session, job, "applied", archived=True)
    before = snapshot(session, job)

    for path, data in write_requests(job, question):
        assert post(auth_client, job, path, data).status_code == 409, path
    edit_url = f"/applications/{job.id}/prep/questions/{question.id}/edit"
    assert auth_client.get(edit_url).status_code == 409
    assert snapshot(session, job) == before


def test_every_request_on_another_users_prep_is_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    question = prep.add(session, other, theirs, "to_ask", None, "Theirs?")
    before = snapshot(session, theirs)

    for path, data in write_requests(theirs, question):
        assert post(auth_client, theirs, path, data).status_code == 404, path
    for suffix in ("", "/edit"):
        url = f"/applications/{theirs.id}/prep/questions/{question.id}{suffix}"
        assert auth_client.get(url).status_code == 404
    assert snapshot(session, theirs) == before


def test_another_users_question_through_my_application_is_404(auth_client, session, job):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    question = prep.add(session, other, theirs, "to_ask", None, "Theirs?")

    for path, data in write_requests(job, question)[2:6]:
        assert post(auth_client, job, path, data).status_code == 404, path
    assert fresh(session, question).text == "Theirs?"


@pytest.mark.parametrize("bad_id", ["abc", "0", str(2**31)])
def test_a_bad_question_id_is_404(auth_client, job, bad_id):
    assert post(auth_client, job, f"questions/{bad_id}/answer", {"answer": "A"}).status_code == 404


def test_every_write_needs_the_csrf_token(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Q?")
    before = snapshot(session, job)

    for path, data in write_requests(job, question):
        response = auth_client.post(f"/applications/{job.id}/prep/{path}", data=data)
        assert response.status_code == 403, path
    assert snapshot(session, job) == before


def test_no_write_but_start_reloads_the_page(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, "Q?")

    for path, data in write_requests(job, question)[1:-2]:
        response = post(auth_client, job, path, data)
        assert "application-changed" not in response.headers.get("HX-Trigger", ""), path
