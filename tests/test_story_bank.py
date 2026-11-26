"""M14b STAR story bank (D-181…D-185): the models. Section and tab come in later steps."""

import datetime as dt

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.models import Application, Experience, InterviewStory, Story
from app.services.profile_service import get_or_create_profile


@pytest.fixture
def profile(session, user):
    return get_or_create_profile(session, user)


def add(session, row):
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_a_story_has_defaults(session, profile):
    story = add(session, Story(profile_id=profile.id, title="Conflict in a team"))

    assert (story.situation, story.task, story.action, story.result) == (None,) * 4
    assert story.tags is None and story.experience_id is None
    assert story.sort_order == 0 and story.created_at is not None


def test_deleting_the_experience_clears_the_link(session, profile):
    job = add(
        session,
        Experience(
            profile_id=profile.id, title="PM", company="Acme", start_date=dt.date(2024, 1, 1)
        ),
    )
    story = add(session, Story(profile_id=profile.id, title="Launch", experience_id=job.id))

    session.exec(delete(Experience).where(Experience.id == job.id))
    session.commit()
    session.refresh(story)

    assert story.experience_id is None


def test_a_story_is_picked_once_per_interview(session, user, profile):
    job = add(session, Application(user_id=user.id, company="Nordbau", job_title="PM"))
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))

    session.add(InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("deleted", [Story, Application])
def test_deleting_the_story_or_the_application_deletes_the_link(session, user, profile, deleted):
    job = add(session, Application(user_id=user.id, company="Nordbau", job_title="PM"))
    story = add(session, Story(profile_id=profile.id, title="Launch"))
    add(session, InterviewStory(user_id=user.id, application_id=job.id, story_id=story.id))

    row_id = story.id if deleted is Story else job.id
    session.exec(delete(deleted).where(deleted.id == row_id))
    session.commit()

    assert session.exec(select(InterviewStory)).all() == []
