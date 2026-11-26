"""M14a interview prep (D-175…D-180): the model and interview_prep_service."""

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import select

from app.models import Application, DeletedItem, InterviewQuestion
from app.models.interview import MAX_ANSWER, MAX_QUESTION_TEXT, MAX_QUESTIONS
from app.services import interview_prep_service as prep
from app.services import trash_service
from app.services.auth_service import create_user


def add_job(session, user, **fields) -> Application:
    values = {"company": "Nordbau", "job_title": "PM", "status": "applied"} | fields
    job = Application(user_id=user.id, **values)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def test_a_question_has_defaults(session, user):
    job = add_job(session, user)
    question = InterviewQuestion(
        user_id=user.id, application_id=job.id, kind="to_ask", text="What are the next steps?"
    )
    session.add(question)
    session.commit()
    session.refresh(question)

    assert question.category is None
    assert question.answer is None
    assert question.asked is False
    assert question.position == 0
    assert question.created_at is not None


def test_a_new_application_has_no_prep(session, user):
    job = add_job(session, user)

    assert job.interview_notes is None
    assert job.interview_prep_started_at is None


def test_deleting_the_application_deletes_its_questions(session, user):
    job = add_job(session, user)
    session.add(
        InterviewQuestion(user_id=user.id, application_id=job.id, kind="likely", text="Why us?")
    )
    session.commit()

    session.exec(delete(Application).where(Application.id == job.id))
    session.commit()

    assert session.exec(select(InterviewQuestion)).all() == []


# --- Tab ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "shown"),
    [("saved", False), ("applied", True), ("interview", True), ("offer", True),
     ("rejected", True), ("withdrawn", False)],
)  # fmt: skip
def test_the_tab_is_shown_from_applied_on(status, shown):
    assert prep.has_tab(Application(user_id=1, company="A", job_title="B", status=status)) is shown


# --- Start (D-178) ------------------------------------------------------------------


def test_start_adds_the_starter_set_with_the_company(session, user):
    job = add_job(session, user)

    assert prep.start(session, user, job, with_starters=True) is True

    questions = prep.questions_of(session, user, job)
    assert len(questions["likely"]) == len(prep.STARTER_QUESTIONS) == 12
    assert len(questions["to_ask"]) == len(prep.STARTER_TO_ASK) == 4
    texts = [question.text for question in questions["likely"]]
    assert "Why do you want to work at Nordbau?" in texts
    assert not any("{company}" in text for text in texts)
    assert job.interview_prep_started_at is not None


def test_start_runs_only_once(session, user):
    job = add_job(session, user)
    prep.start(session, user, job, with_starters=True)

    assert prep.start(session, user, job, with_starters=True) is False
    assert len(session.exec(select(InterviewQuestion)).all()) == 16


def test_start_with_an_empty_list_adds_nothing_and_blocks_the_set(session, user):
    job = add_job(session, user)

    assert prep.start(session, user, job, with_starters=False) is True
    assert prep.start(session, user, job, with_starters=True) is False
    assert session.exec(select(InterviewQuestion)).all() == []


def test_start_on_an_archived_job_is_refused(session, user):
    job = add_job(session, user, archived=True)

    with pytest.raises(prep.Archived):
        prep.start(session, user, job, with_starters=True)


def test_a_long_company_name_still_fits(session, user):
    job = add_job(session, user, company="X" * 200)
    prep.start(session, user, job, with_starters=True)

    assert all(
        len(q.text) <= MAX_QUESTION_TEXT for q in session.exec(select(InterviewQuestion)).all()
    )


# --- Reading ------------------------------------------------------------------------


def test_likely_questions_are_in_category_order(session, user):
    job = add_job(session, user)
    prep.add(session, user, job, "likely", "company", "Why us?")
    prep.add(session, user, job, "likely", "general", "Who are you?")
    prep.add(session, user, job, "likely", "company", "What do we do?")

    texts = [q.text for q in prep.questions_of(session, user, job)["likely"]]

    assert texts == ["Who are you?", "Why us?", "What do we do?"]


def test_another_users_question_is_a_404(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    mine, theirs = add_job(session, user), add_job(session, other)
    question = prep.add(session, other, theirs, "likely", "general", "Hi?")

    for application_id in (mine, theirs):
        with pytest.raises(HTTPException) as error:
            prep.get_owned_question(session, user, application_id, question.id)
        assert error.value.status_code == 404


def test_a_question_of_another_application_is_a_404(session, user):
    first, second = add_job(session, user), add_job(session, user)
    question = prep.add(session, user, first, "likely", "general", "Hi?")

    with pytest.raises(HTTPException):
        prep.get_owned_question(session, user, second, question.id)


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", str(2**31), ""])
def test_a_bad_question_id_is_a_404(session, user, bad_id):
    with pytest.raises(HTTPException):
        prep.get_owned_question(session, user, add_job(session, user), bad_id)


def test_answered_count(session, user):
    job = add_job(session, user)
    first = prep.add(session, user, job, "likely", "general", "A?")
    prep.add(session, user, job, "likely", "general", "B?")
    prep.save_answer(session, job, first, "My answer")

    assert prep.answered_count(prep.questions_of(session, user, job)["likely"]) == 1


# --- Writing ------------------------------------------------------------------------


def test_add_puts_the_question_last_and_starts_the_prep(session, user):
    job = add_job(session, user)
    first = prep.add(session, user, job, "to_ask", None, "  First?  ")
    second = prep.add(session, user, job, "to_ask", "", "Second?")

    assert (first.text, first.position, second.position) == ("First?", 0, 1)
    assert job.interview_prep_started_at is not None


@pytest.mark.parametrize(
    ("kind", "category", "text"),
    [("likely", None, "Q?"), ("likely", "nope", "Q?"), ("to_ask", "general", "Q?"),
     ("other", None, "Q?"), ("likely", "general", "   "),
     ("likely", "general", "x" * (MAX_QUESTION_TEXT + 1))],
)  # fmt: skip
def test_add_refuses_bad_values(session, user, kind, category, text):
    with pytest.raises(prep.InvalidInput):
        prep.add(session, user, add_job(session, user), kind, category, text)


def test_add_stops_at_the_limit(session, user):
    job = add_job(session, user)
    session.add_all(
        InterviewQuestion(user_id=user.id, application_id=job.id, kind="to_ask", text="Q?")
        for _ in range(MAX_QUESTIONS)
    )
    session.commit()

    with pytest.raises(prep.TooManyQuestions):
        prep.add(session, user, job, "to_ask", None, "One more?")


def test_update_answer_asked_and_notes(session, user):
    job = add_job(session, user)
    likely = prep.add(session, user, job, "likely", "general", "Old?")
    to_ask = prep.add(session, user, job, "to_ask", None, "Ask?")

    prep.update_question(session, job, likely, "New?", "role")
    prep.save_answer(session, job, likely, "Line one\nline two")
    prep.set_asked(session, job, to_ask, True)
    prep.save_notes(session, job, "Meet Mira")

    assert (likely.text, likely.category, likely.answer) == ("New?", "role", "Line one\nline two")
    assert to_ask.asked is True
    assert job.interview_notes == "Meet Mira"


def test_an_empty_answer_is_stored_as_none_and_a_long_one_refused(session, user):
    job = add_job(session, user)
    question = prep.add(session, user, job, "likely", "general", "Q?")
    prep.save_answer(session, job, question, "Draft")

    prep.save_answer(session, job, question, "  ")
    assert question.answer is None
    with pytest.raises(prep.InvalidInput):
        prep.save_answer(session, job, question, "x" * (MAX_ANSWER + 1))


def test_a_likely_question_has_no_tick(session, user):
    job = add_job(session, user)
    question = prep.add(session, user, job, "likely", "general", "Q?")

    with pytest.raises(prep.InvalidInput):
        prep.set_asked(session, job, question, True)


def test_every_write_on_an_archived_job_is_refused(session, user):
    job = add_job(session, user)
    question = prep.add(session, user, job, "to_ask", None, "Q?")
    job.archived = True
    session.add(job)
    session.commit()

    for write in (
        lambda: prep.add(session, user, job, "to_ask", None, "Q2?"),
        lambda: prep.update_question(session, job, question, "Q3?", None),
        lambda: prep.save_answer(session, job, question, "A"),
        lambda: prep.set_asked(session, job, question, True),
        lambda: prep.save_notes(session, job, "N"),
        lambda: prep.delete(session, user, job, question),
    ):
        with pytest.raises(prep.Archived):
            write()


# --- Delete and Undo ----------------------------------------------------------------


def _undo(session, user, copy_id):
    item = session.get(DeletedItem, copy_id)
    prep.restore(session, user, item.payload)
    session.delete(item)
    session.commit()


def test_delete_and_undo_bring_the_question_back(session, user):
    job = add_job(session, user)
    question = prep.add(session, user, job, "likely", "behavioural", "Q?")
    prep.save_answer(session, job, question, "My story")

    copy_id = prep.delete(session, user, job, question)
    assert prep.questions_of(session, user, job)["likely"] == []

    _undo(session, user, copy_id)
    back = prep.questions_of(session, user, job)["likely"]
    assert [(q.text, q.category, q.answer) for q in back] == [("Q?", "behavioural", "My story")]


def test_undo_is_refused_on_an_archived_job(session, user):
    job = add_job(session, user)
    copy_id = prep.delete(session, user, job, prep.add(session, user, job, "to_ask", None, "Q?"))
    job.archived = True
    session.add(job)
    session.commit()

    with pytest.raises(trash_service.CannotRestore):
        prep.restore(session, user, session.get(DeletedItem, copy_id).payload)


def test_undo_is_refused_at_the_limit(session, user):
    job = add_job(session, user)
    copy_id = prep.delete(session, user, job, prep.add(session, user, job, "to_ask", None, "Q?"))
    session.add_all(
        InterviewQuestion(user_id=user.id, application_id=job.id, kind="to_ask", text="Q?")
        for _ in range(MAX_QUESTIONS)
    )
    session.commit()

    with pytest.raises(trash_service.CannotRestore):
        prep.restore(session, user, session.get(DeletedItem, copy_id).payload)


def test_undo_never_restores_into_another_users_application(session, user):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add_job(session, other)
    payload = {"application_id": theirs.id, "kind": "to_ask", "text": "Sneaky?"}

    with pytest.raises(trash_service.CannotRestore):
        prep.restore(session, user, payload)


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"application_id": "1"}, {"application_id": 2**40}],
)
def test_a_broken_copy_cannot_be_restored(session, user, payload):
    with pytest.raises(trash_service.CannotRestore):
        prep.restore(session, user, payload)


def test_a_copy_with_bad_values_cannot_be_restored(session, user):
    job = add_job(session, user)
    payload = {"application_id": job.id, "kind": "to_ask", "category": "general", "text": "Q?"}

    with pytest.raises(trash_service.CannotRestore):
        prep.restore(session, user, payload)
