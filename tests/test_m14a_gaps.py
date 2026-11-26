"""M14a: the authorization and error cases the Interview prep tests don't cover yet:
requests without a session, HEAD, rate limits, too many form fields, a question of
another of my applications, Undo tokens and copies that can't come back, odd tab
values and escaping (SPEC §6.7; D-176…D-180, D-171)."""

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.main import app
from app.models import Application, DeletedItem, InterviewQuestion
from app.models.interview import MAX_QUESTIONS
from app.services import application_service as apps
from app.services import interview_prep_service as prep
from app.services import trash_service, undo_service
from app.services.auth_service import create_user
from tests.helpers import csrf_token, log_in
from tests.test_application_documents import fresh, headers

SCRIPT = "<script>alert(1)</script>"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "another long password"


@pytest.fixture
def job(session, user) -> Application:
    job = apps.create(session, user, company="Kranich Health", job_title="Senior PM")
    job.status = "applied"
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@pytest.fixture
def question(session, user, job) -> InterviewQuestion:
    return prep.add(session, user, job, "to_ask", None, "Team size?")


@pytest.fixture
def other_client(session, client) -> TestClient:
    """A second browser, logged in as another user (its own cookies)."""
    create_user(session, email=OTHER_EMAIL, password=OTHER_PASSWORD, full_name="Other")
    other = TestClient(app)  # the database override from `client` still applies
    assert log_in(other, OTHER_EMAIL, OTHER_PASSWORD).status_code == 303
    return other


def post(client, job, path, data=None):
    return client.post(
        f"/applications/{job.id}/prep/{path}", data=data or {}, headers=headers(client)
    )


def toast_of(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


def snapshot(session):
    """Every question and every application's prep fields, to check nothing changed."""
    session.expire_all()
    rows = session.exec(select(InterviewQuestion).order_by(InterviewQuestion.id)).all()
    jobs = session.exec(select(Application).order_by(Application.id)).all()
    return (
        [(q.id, q.application_id, q.text, q.category, q.answer, q.asked) for q in rows],
        [(a.id, a.interview_notes, a.interview_prep_started_at) for a in jobs],
    )


def write_requests(question_id):
    """Every POST of the prep, with a valid body (path after /prep/)."""
    q = f"questions/{question_id}"
    return [
        ("start", {"starters": "1"}),
        ("questions", {"kind": "to_ask", "text": "New?"}),
        (q, {"text": "Changed?"}),
        (f"{q}/answer", {"answer": "A"}),
        (f"{q}/asked", {"asked": "1"}),
        (f"{q}/delete", {}),
        ("notes", {"interview_notes": "N"}),
    ]


def question_urls(job, question_id):
    """The two GETs of one question (row, edit form)."""
    base = f"/applications/{job.id}/prep/questions/{question_id}"
    return [base, f"{base}/edit"]


# --- Logged out ---------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["", "/edit"])
def test_logged_out_question_pages_go_to_login(client, session, user, job, question, suffix):
    url = f"/applications/{job.id}/prep/questions/{question.id}{suffix}"

    response = client.get(url, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_logged_out_tab_goes_to_login_and_back_to_the_tab(client, job):
    response = client.get(f"/applications/{job.id}?tab=interview", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert "tab%3Dinterview" in response.headers["location"]


@pytest.mark.parametrize("index", range(7))
def test_logged_out_writes_go_to_login_and_change_nothing(
    client, session, user, job, question, index
):
    path, data = write_requests(question.id)[index]
    before = snapshot(session)

    response = client.post(
        f"/applications/{job.id}/prep/{path}",
        data=data,
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert snapshot(session) == before


@pytest.mark.parametrize("index", range(7))
def test_logged_out_htmx_writes_ask_for_a_full_page_login(
    client, session, user, job, question, index
):
    path, data = write_requests(question.id)[index]

    response = client.post(
        f"/applications/{job.id}/prep/{path}",
        data=data,
        headers={
            "HX-Request": "true",
            "HX-Current-URL": f"http://testserver/applications/{job.id}?tab=interview",
            "X-CSRF-Token": csrf_token(client),
        },
        follow_redirects=False,
    )

    # After login: back to the tab, not to the piece of HTML.
    assert response.headers["HX-Redirect"] == (
        f"/login?next=/applications/{job.id}%3Ftab%3Dinterview"
    )


def test_logged_out_undo_goes_to_login_and_restores_nothing(client, session, user, job, question):
    copy_id = prep.delete(session, user, job, question)
    token = undo_service.make_delete_token(user, copy_id)

    response = client.post(
        "/undo/delete",
        data={"token": token},
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.exec(select(InterviewQuestion)).all() == []


# --- HEAD on the GETs ---------------------------------------------------------------


def test_head_on_the_question_gets_answers_like_get_without_a_body(
    auth_client, session, job, question
):
    before = snapshot(session)

    for url in question_urls(job, question.id):
        response = auth_client.head(url)
        assert response.status_code == 200, url
        assert response.content == b""
    assert snapshot(session) == before


def test_head_on_another_users_question_is_404(auth_client, session):
    other = create_user(session, email=OTHER_EMAIL, password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    question = prep.add(session, other, theirs, "to_ask", None, "Theirs?")

    for url in question_urls(theirs, question.id):
        assert auth_client.head(url).status_code == 404, url


def test_head_on_an_archived_jobs_edit_form_is_409(auth_client, session, job, question):
    job = fresh(session, job)
    job.archived = True
    session.add(job)
    session.commit()

    base = f"/applications/{job.id}/prep/questions/{question.id}"
    assert auth_client.head(f"{base}/edit").status_code == 409
    assert auth_client.head(base).status_code == 200  # reading stays allowed


def test_head_on_the_tab_of_another_user_is_404(auth_client, session):
    other = create_user(session, email=OTHER_EMAIL, password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")

    assert auth_client.head(f"/applications/{theirs.id}?tab=interview").status_code == 404


# --- Rate limits --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index", "limit"), [(0, 10), (1, 30), (2, 60), (3, 120), (4, 60), (6, 120)]
)
def test_writes_are_rate_limited(auth_client, session, user, job, question, index, limit):
    path, data = write_requests(question.id)[index]
    if index == 1:
        data = {"kind": "to_ask", "category": "role", "text": "Q?"}  # 422: saves nothing

    codes = [post(auth_client, job, path, data).status_code for _ in range(limit + 1)]

    assert 429 not in codes[:limit] and codes[limit] == 429


def test_delete_is_rate_limited(auth_client, session, user, job):
    # Every delete after the first is a 404 (the question is gone); it still counts.
    question = prep.add(session, user, job, "to_ask", None, "Q?")
    path = f"questions/{question.id}/delete"

    codes = [post(auth_client, job, path).status_code for _ in range(31)]

    assert 429 not in codes[:30] and codes[30] == 429


@pytest.mark.parametrize("suffix", ["", "/edit"])
def test_question_gets_are_rate_limited(auth_client, job, question, suffix):
    url = f"/applications/{job.id}/prep/questions/{question.id}{suffix}"

    codes = [auth_client.get(url).status_code for _ in range(61)]

    assert 429 not in codes[:60] and codes[60] == 429


def test_a_rate_limited_add_saves_nothing(auth_client, session, job):
    for i in range(30):
        post(auth_client, job, "questions", {"kind": "to_ask", "text": f"Q{i}?"})

    response = post(auth_client, job, "questions", {"kind": "to_ask", "text": "One more?"})

    assert response.status_code == 429
    assert len(session.exec(select(InterviewQuestion)).all()) == 30


# --- Too many form fields (D-171) ---------------------------------------------------


# The delete takes no form: it reads no fields, so it has nothing to count.
@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 6])
def test_too_many_fields_are_400_and_change_nothing(
    auth_client, session, user, job, question, index
):
    path, data = write_requests(question.id)[index]
    data = data | {f"field{i}": "x" for i in range(11)}
    before = snapshot(session)

    response = post(auth_client, job, path, data)

    assert response.status_code == 400
    assert snapshot(session) == before


def test_ten_fields_are_still_fine(auth_client, session, job, question):
    data = {"answer": "A"} | {f"field{i}": "x" for i in range(9)}

    assert post(auth_client, job, f"questions/{question.id}/answer", data).status_code == 200
    assert fresh(session, question).answer == "A"


def test_repeated_fields_count_too(auth_client, session, job, question):
    response = auth_client.post(
        f"/applications/{job.id}/prep/questions/{question.id}/asked",
        data={"asked": ["0"] * 10 + ["1"]},
        headers=headers(auth_client),
    )

    assert response.status_code == 400
    assert fresh(session, question).asked is False


# --- A question of another of my applications ---------------------------------------


@pytest.fixture
def second_job(session, user) -> Application:
    second = apps.create(session, user, company="Second Co", job_title="PM")
    second.status = "interview"
    session.add(second)
    session.commit()
    return fresh(session, second)


@pytest.mark.parametrize("index", [2, 3, 4, 5])
def test_my_question_through_my_other_application_is_404(
    auth_client, session, job, second_job, question, index
):
    path, data = write_requests(question.id)[index]
    before = snapshot(session)

    response = post(auth_client, second_job, path, data)

    assert response.status_code == 404
    assert snapshot(session) == before
    assert session.exec(select(DeletedItem)).all() == []


def test_my_question_through_my_other_application_is_not_shown(
    auth_client, job, second_job, question
):
    for url in question_urls(second_job, question.id):
        response = auth_client.get(url)
        assert response.status_code == 404, url
        assert "Team size?" not in response.text


def test_another_users_question_through_my_application_is_not_shown(auth_client, session, job):
    other = create_user(session, email=OTHER_EMAIL, password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    question = prep.add(session, other, theirs, "to_ask", None, "Their secret?")

    for url in question_urls(job, question.id):
        response = auth_client.get(url)
        assert response.status_code == 404, url
        assert "Their secret?" not in response.text


@pytest.mark.parametrize("bad_id", ["abc", "-1", "1.0", "0x1", str(2**70)])
@pytest.mark.parametrize("suffix", ["", "/edit"])
def test_odd_question_ids_on_the_gets_are_404(auth_client, job, question, bad_id, suffix):
    response = auth_client.get(f"/applications/{job.id}/prep/questions/{bad_id}{suffix}")

    assert response.status_code == 404


@pytest.mark.parametrize("bad_id", ["abc", "-1", "0", str(2**70)])
@pytest.mark.parametrize("index", [0, 6])
def test_odd_application_ids_are_404(auth_client, session, job, bad_id, index):
    path, data = write_requests(1)[index]
    before = snapshot(session)

    response = auth_client.post(
        f"/applications/{bad_id}/prep/{path}", data=data, headers=headers(auth_client)
    )

    assert response.status_code == 404
    assert snapshot(session) == before


# --- Undo of a delete ---------------------------------------------------------------


def delete_token(client, job, question) -> str:
    return toast_of(post(client, job, f"questions/{question.id}/delete"))["undo_token"]


def undo(client, token):
    return client.post("/undo/delete", data={"token": token}, headers=headers(client))


def test_my_undo_token_does_nothing_for_another_user(
    auth_client, other_client, session, job, question
):
    token = delete_token(auth_client, job, question)

    response = undo(other_client, token)

    assert response.status_code == 204
    assert session.exec(select(InterviewQuestion)).all() == []
    assert "Nothing to undo." in other_client.get("/").text
    # The copy is still mine: my own Undo still works.
    assert undo(auth_client, token).status_code == 204
    assert [q.text for q in session.exec(select(InterviewQuestion)).all()] == ["Team size?"]


def test_my_token_for_their_copy_does_nothing(auth_client, session, user):
    other = create_user(session, email=OTHER_EMAIL, password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    question = prep.add(session, other, theirs, "to_ask", None, "Theirs?")
    copy_id = prep.delete(session, other, theirs, question)

    response = undo(auth_client, undo_service.make_delete_token(user, copy_id))

    assert response.status_code == 204
    assert session.exec(select(InterviewQuestion)).all() == []
    assert [item.id for item in session.exec(select(DeletedItem)).all()] == [copy_id]


def test_undo_after_the_application_was_deleted_restores_nothing(
    auth_client, session, job, question
):
    token = delete_token(auth_client, job, question)
    session.delete(fresh(session, job))
    session.commit()

    response = undo(auth_client, token)

    assert response.status_code == 204
    assert session.exec(select(InterviewQuestion)).all() == []
    assert "Nothing to undo." in auth_client.get("/").text


def test_undo_into_a_full_application_restores_nothing(auth_client, session, user, job, question):
    user_id, job_id = user.id, job.id
    token = delete_token(auth_client, job, question)
    session.expunge(question)  # SQLite hands the deleted question's id to a new row
    session.add_all(
        InterviewQuestion(user_id=user_id, application_id=job_id, kind="to_ask", text="Q?")
        for _ in range(MAX_QUESTIONS)
    )
    session.commit()

    undo(auth_client, token)

    texts = [q.text for q in session.exec(select(InterviewQuestion)).all()]
    assert len(texts) == MAX_QUESTIONS and "Team size?" not in texts


def test_a_copy_that_points_at_another_users_application_restores_nothing(
    auth_client, session, user, job
):
    other = create_user(session, email=OTHER_EMAIL, password="x" * 12, full_name="O")
    theirs = apps.create(session, other, company="Other", job_title="PM")
    payload = {"application_id": theirs.id, "kind": "to_ask", "category": None, "text": "Planted?"}
    item = trash_service.remember(session, user, "interview_question", payload)
    session.commit()

    response = undo(auth_client, undo_service.make_delete_token(user, item.id))

    assert response.status_code == 204
    assert session.exec(select(InterviewQuestion)).all() == []


def test_an_undo_token_works_once(auth_client, session, job, question):
    token = delete_token(auth_client, job, question)

    undo(auth_client, token)
    undo(auth_client, token)

    assert len(session.exec(select(InterviewQuestion)).all()) == 1


def test_a_restored_question_keeps_its_application_and_kind(
    auth_client, session, user, job, second_job
):
    question = prep.add(session, user, job, "likely", "role", "Why us?")
    prep.add(session, user, second_job, "likely", "role", "Other job?")

    undo(auth_client, delete_token(auth_client, job, question))

    back = session.exec(select(InterviewQuestion).where(InterviewQuestion.text == "Why us?")).one()
    assert (back.application_id, back.kind, back.category) == (job.id, "likely", "role")


# --- Odd tab values -----------------------------------------------------------------


def shows_prep(html: str) -> bool:
    return 'id="interview-prep"' in html


@pytest.mark.parametrize(
    "tab",
    [
        "INTERVIEW",
        "interview ",
        "interview%00",
        "prep",
        "interview%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E",
        "x" * 8000,
    ],
)
def test_an_odd_tab_shows_progress_and_is_not_reflected(auth_client, job, tab):
    response = auth_client.get(f"/applications/{job.id}?tab={tab}")

    assert response.status_code == 200
    html = response.text
    assert not shows_prep(html) and 'id="progress"' in html
    assert SCRIPT not in html and "x" * 100 not in html
    assert html.count('aria-current="page"') == 1


@pytest.mark.parametrize("status", ["applied", "interview"])
def test_tab_apply_on_an_applied_job_shows_progress(auth_client, session, job, status):
    job = fresh(session, job)
    job.status = status
    session.add(job)
    session.commit()

    html = auth_client.get(f"/applications/{job.id}?tab=apply").text

    assert 'id="progress"' in html and not shows_prep(html)


def test_a_repeated_tab_param_never_fails(auth_client, job):
    response = auth_client.get(f"/applications/{job.id}?tab=interview&tab=%3Cb%3E")

    assert response.status_code == 200 and "<b>" not in response.text


def test_the_interview_tab_of_an_archived_saved_job_shows_the_first_tab(
    auth_client, session, user, job
):
    job = fresh(session, job)
    job.status = "saved"
    job.archived = True
    session.add(job)
    session.commit()

    html = auth_client.get(f"/applications/{job.id}?tab=interview").text

    assert not shows_prep(html) and 'id="apply"' in html


# --- Escaping -----------------------------------------------------------------------


def test_question_text_is_escaped_everywhere(auth_client, session, user, job):
    likely = prep.add(session, user, job, "likely", "general", SCRIPT)
    to_ask = prep.add(session, user, job, "to_ask", None, f'"><img src=x onerror=1>{SCRIPT}')

    pages = [auth_client.get(f"/applications/{job.id}?tab=interview").text]
    for q in (likely, to_ask):
        pages += [auth_client.get(url).text for url in question_urls(job, q.id)]
    pages.append(post(auth_client, job, "questions", {"kind": "to_ask", "text": SCRIPT}).text)

    for html in pages:
        assert SCRIPT not in html and "<img src=x" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_the_edit_form_value_cannot_break_out_of_its_attribute(auth_client, session, user, job):
    question = prep.add(session, user, job, "to_ask", None, '" autofocus onfocus="alert(1)')

    html = auth_client.get(f"/applications/{job.id}/prep/questions/{question.id}/edit").text

    assert 'onfocus="alert(1)' not in html
    assert re.search(r'value="&#34; autofocus onfocus=&#34;alert\(1\)"', html) or (
        'value="&quot; autofocus onfocus=&quot;alert(1)"' in html
    )


def test_notes_cannot_close_their_textarea(auth_client, session, job):
    post(auth_client, job, "start", {"starters": "0"})
    post(auth_client, job, "notes", {"interview_notes": f"</textarea>{SCRIPT}"})

    html = auth_client.get(f"/applications/{job.id}?tab=interview").text

    assert SCRIPT not in html
    assert "&lt;/textarea&gt;&lt;script&gt;" in html


def test_the_company_name_is_escaped_on_the_tab(auth_client, session, user):
    job = apps.create(session, user, company=SCRIPT, job_title="PM")
    job.status = "applied"
    session.add(job)
    session.commit()

    before = auth_client.get(f"/applications/{job.id}?tab=interview").text
    post(auth_client, job, "start", {"starters": "1"})
    after = auth_client.get(f"/applications/{job.id}?tab=interview").text

    for html in (before, after):
        assert SCRIPT not in html
    assert "Why do you want to work at &lt;script&gt;" in after
