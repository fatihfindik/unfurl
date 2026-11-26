"""M14a interview prep (D-175…D-180): the model. Service and routes come in later steps."""

from sqlalchemy import delete
from sqlmodel import select

from app.models import Application, InterviewQuestion


def add_job(session, user) -> Application:
    job = Application(user_id=user.id, company="Nordbau", job_title="PM")
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
