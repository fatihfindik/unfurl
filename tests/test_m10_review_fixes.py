"""Fixes from the M10 security review: Undo of a delete used once even at the same
moment (L1), and the rest days row created once (L2)."""

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import DeletedItem, LearningGoal, NotificationPreference
from app.services import learning_service as learning
from app.services import trash_service


def test_a_copy_can_be_claimed_only_once(engine, session, user):
    goal = learning.create_goal(
        session,
        user,
        title="Python",
        target_date=None,
        weekly_hours=None,
        resource=None,
        plan_weekly=False,
    )
    copy_id = learning.delete_goal(session, user, goal)
    first = trash_service.take(session, user, copy_id)
    with Session(engine) as other_request:  # a second request at the same moment
        second = trash_service.take(other_request, user, copy_id)
        assert second is not None

        assert trash_service.claim(session, first) is True
        session.commit()
        assert trash_service.claim(other_request, second) is False

    assert session.exec(select(DeletedItem)).all() == []


def test_undo_restores_a_goal_once(session, user):
    goal = learning.create_goal(
        session,
        user,
        title="Python",
        target_date=dt.date(2027, 1, 1),
        weekly_hours=None,
        resource=None,
        plan_weekly=False,
    )
    copy_id = learning.delete_goal(session, user, goal)
    item = trash_service.take(session, user, copy_id)
    assert trash_service.claim(session, item)
    learning.restore_goal(session, user, item.payload)
    session.commit()

    # The second request finds no copy left.
    assert trash_service.take(session, user, copy_id) is None
    assert len(session.exec(select(LearningGoal)).all()) == 1


def test_preferences_created_by_another_request_at_the_same_moment(session, user, monkeypatch):
    # The other request's row arrives between our "not there" and our insert.
    real_commit = session.commit
    calls = {"n": 0}

    def commit_after_other_request():
        calls["n"] += 1
        if calls["n"] == 1:
            session.rollback()
            session.add(NotificationPreference(user_id=user.id, rest_days=["fri"]))
            real_commit()
            raise IntegrityError("insert", {}, Exception("unique"))
        real_commit()

    monkeypatch.setattr(session, "commit", commit_after_other_request)

    prefs = learning.preferences(session, user)

    assert prefs.rest_days == ["fri"]
    assert len(session.exec(select(NotificationPreference)).all()) == 1
