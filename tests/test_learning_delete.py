"""Reschedule a task, delete a task or goal, and Undo of the deletes (D-095, D-086)."""

import datetime as dt
import json

import pytest
from sqlmodel import select

from app.models import (
    Application,
    DeletedItem,
    LearningGoal,
    LearningResource,
    LearningTask,
    StudyLog,
)
from app.services import clock, undo_service
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


def post(client, url, **fields):
    return client.post(
        url, data=fields, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


def events(response) -> dict:
    return json.loads(response.headers.get("HX-Trigger", "{}"))


def undo(client, response):
    return post(client, "/undo/delete", token=events(response)["toast"]["undo_token"])


@pytest.fixture
def goal(session, user) -> LearningGoal:
    """Python with CS50P (3 weeks) and three weekly tasks; Week 1 is done (120 min logged)."""
    goal = learning.create_goal(
        session,
        user,
        title="Python",
        target_date=dt.date(2027, 2, 28),
        weekly_hours=2.0,
        resource={"name": "CS50P", "url": "https://cs50.harvard.edu/", "units_total": 3},
        plan_weekly=True,
    )
    learning.set_done(session, user, learning.tasks(session, user)[0], True)
    return goal


def tasks_of(session, user):
    return learning.tasks(session, user)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


# --- Reschedule ---------------------------------------------------------------------------


def test_reschedule_an_open_task(auth_client, session, user, goal):
    second = tasks_of(session, user)[1]

    response = post(auth_client, f"/learning/tasks/{second.id}/reschedule", due_date="2026-11-20")

    assert response.status_code == 204
    session.refresh(second)
    assert second.due_date == dt.date(2026, 11, 20)
    trigger = events(response)
    assert trigger["toast"]["message"] == "Moved to Fri, 20 Nov."
    assert trigger["learning-changed"] is True


def test_a_done_task_keeps_its_date(auth_client, session, user, goal):
    first = tasks_of(session, user)[0]

    response = post(auth_client, f"/learning/tasks/{first.id}/reschedule", due_date="2026-11-20")

    assert events(response)["toast"]["message"] == "A done task keeps its date."
    session.refresh(first)
    assert first.due_date == WED


@pytest.mark.parametrize(
    ("due_date", "message"),
    [
        ("", "Pick a date."),
        ("tomorrow", "Use the format YYYY-MM-DD."),
        ("2026-10-18", "Pick a date from 30 days ago to 2 years from now."),
        ("2028-11-19", "Pick a date from 30 days ago to 2 years from now."),
    ],
)
def test_reschedule_checks_the_date(auth_client, session, user, goal, due_date, message):
    second = tasks_of(session, user)[1]
    before = second.due_date

    response = post(auth_client, f"/learning/tasks/{second.id}/reschedule", due_date=due_date)

    assert events(response)["toast"]["message"] == message
    session.refresh(second)
    assert second.due_date == before


def test_reschedule_another_users_task_is_404(auth_client, session, other):
    their_goal = LearningGoal(user_id=other.id, title="Theirs")
    session.add(their_goal)
    session.commit()
    theirs = LearningTask(user_id=other.id, goal_id=their_goal.id, title="T", due_date=WED)
    session.add(theirs)
    session.commit()

    response = post(auth_client, f"/learning/tasks/{theirs.id}/reschedule", due_date="2026-11-20")

    assert response.status_code == 404
    session.refresh(theirs)
    assert theirs.due_date == WED


def test_reschedule_needs_the_csrf_token(auth_client, session, user, goal):
    second = tasks_of(session, user)[1]

    response = auth_client.post(
        f"/learning/tasks/{second.id}/reschedule", data={"due_date": "2026-11-20"}
    )

    assert response.status_code == 403


def test_task_rows_have_the_menu(auth_client, session, user, goal):
    html = auth_client.get("/learning?view=all").text
    second = tasks_of(session, user)[1]

    assert f'hx-post="/learning/tasks/{second.id}/reschedule"' in html
    assert f'hx-post="/learning/tasks/{second.id}/delete"' in html
    # The done task (in the Done tab, D-187) can be deleted but not rescheduled.
    first = tasks_of(session, user)[0]
    assert f'data-task="{first.id}"' not in html
    html = auth_client.get("/learning?view=done").text
    assert f'hx-post="/learning/tasks/{first.id}/delete"' in html
    assert f'hx-post="/learning/tasks/{first.id}/reschedule"' not in html


# --- Delete a task ------------------------------------------------------------------------


def test_delete_a_task_keeps_its_hours(auth_client, session, user, goal):
    first = tasks_of(session, user)[0]

    response = post(auth_client, f"/learning/tasks/{first.id}/delete")

    assert response.status_code == 204
    trigger = events(response)
    assert trigger["toast"]["message"] == "Task deleted."
    assert trigger["toast"]["undo_url"] == "/undo/delete"
    assert len(tasks_of(session, user)) == 2
    [log] = session.exec(select(StudyLog)).all()
    assert (log.minutes, log.task_id, log.goal_id) == (120, None, goal.id)
    assert learning.this_week(session, user).minutes == 120
    assert learning.streak(session, user) == 1


def test_undo_brings_the_task_back_with_its_log(auth_client, session, user, goal):
    first = tasks_of(session, user)[0]
    resource = session.exec(select(LearningResource)).one()
    deleted = post(auth_client, f"/learning/tasks/{first.id}/delete")

    response = undo(auth_client, deleted)

    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"
    restored = [t for t in tasks_of(session, user) if t.title == "Week 1"]
    assert len(restored) == 1
    back = restored[0]
    assert back.id != first.id
    assert (back.done, back.due_date, back.goal_id, back.resource_id) == (
        True,
        WED,
        goal.id,
        resource.id,
    )
    [log] = session.exec(select(StudyLog)).all()
    assert (log.task_id, log.goal_id) == (back.id, goal.id)
    assert session.exec(select(DeletedItem)).all() == []


def test_a_task_of_a_deleted_goal_cannot_come_back(auth_client, session, user, goal):
    second = tasks_of(session, user)[1]
    deleted = post(auth_client, f"/learning/tasks/{second.id}/delete")
    post(auth_client, f"/learning/goals/{goal.id}/delete")

    undo(auth_client, deleted)

    assert tasks_of(session, user) == []
    assert "Nothing to undo." in auth_client.get("/learning").text


def test_delete_another_users_task_is_404(auth_client, session, other):
    their_goal = LearningGoal(user_id=other.id, title="Theirs")
    session.add(their_goal)
    session.commit()
    theirs = LearningTask(user_id=other.id, goal_id=their_goal.id, title="T", due_date=WED)
    session.add(theirs)
    session.commit()

    response = post(auth_client, f"/learning/tasks/{theirs.id}/delete")

    assert response.status_code == 404
    assert session.get(LearningTask, theirs.id) is not None


def test_delete_task_needs_the_csrf_token(auth_client, session, user, goal):
    first = tasks_of(session, user)[0]

    assert auth_client.post(f"/learning/tasks/{first.id}/delete").status_code == 403
    assert len(tasks_of(session, user)) == 3


# --- Delete a goal ------------------------------------------------------------------------


def test_goal_card_has_delete(auth_client, goal):
    html = auth_client.get("/learning").text

    assert f'data-dialog-url="/learning/goals/{goal.id}/delete"' in html
    assert '<dialog id="delete-goal"' in html


def test_delete_confirmation(auth_client, goal):
    html = auth_client.get(f"/learning/goals/{goal.id}/delete").text

    assert "Python" in html
    assert "This also deletes its tasks (3)." in html
    assert f'hx-post="/learning/goals/{goal.id}/delete"' in html


def test_delete_confirmation_of_another_users_goal_is_404(auth_client, session, other):
    theirs = LearningGoal(user_id=other.id, title="Theirs")
    session.add(theirs)
    session.commit()

    assert auth_client.get(f"/learning/goals/{theirs.id}/delete").status_code == 404


def test_delete_a_goal_with_its_resource_and_tasks(auth_client, session, user, goal):
    response = post(auth_client, f"/learning/goals/{goal.id}/delete")

    assert response.status_code == 204
    trigger = events(response)
    assert trigger["toast"]["message"] == "Goal deleted."
    assert trigger["close-dialog"] == "delete-goal"
    assert learning.goals(session, user) == []
    assert tasks_of(session, user) == []
    assert session.exec(select(LearningResource)).all() == []
    [log] = session.exec(select(StudyLog)).all()
    assert (log.goal_id, log.task_id, log.minutes) == (None, None, 120)
    assert learning.this_week(session, user).minutes == 120


def test_undo_brings_the_goal_back_whole(auth_client, session, user, goal):
    deleted = post(auth_client, f"/learning/goals/{goal.id}/delete")

    undo(auth_client, deleted)

    [back] = learning.goals(session, user)
    assert (back.title, back.target_date, back.weekly_hours_target) == (
        "Python",
        dt.date(2027, 2, 28),
        2.0,
    )
    [resource] = session.exec(select(LearningResource)).all()
    assert (resource.goal_id, resource.units_done, resource.user_id) == (back.id, 1, user.id)
    restored = tasks_of(session, user)
    assert [(t.title, t.done) for t in restored] == [
        ("Week 1", True),
        ("Week 2", False),
        ("Week 3", False),
    ]
    assert all(t.goal_id == back.id and t.resource_id == resource.id for t in restored)
    [log] = session.exec(select(StudyLog)).all()
    assert (log.goal_id, log.task_id) == (back.id, restored[0].id)


def test_undo_keeps_a_fit_gap_link_only_to_the_users_application(auth_client, session, user, other):
    mine = Application(user_id=user.id, company="Acme", job_title="PM")
    theirs = Application(user_id=other.id, company="Other", job_title="PM")
    session.add_all([mine, theirs])
    session.commit()
    goal_a = LearningGoal(user_id=user.id, title="A", source_application_id=mine.id)
    session.add(goal_a)
    session.commit()

    undo(auth_client, post(auth_client, f"/learning/goals/{goal_a.id}/delete"))
    [back] = learning.goals(session, user)
    assert back.source_application_id == mine.id

    # A copy that points at someone else's application (it can't happen through the
    # app) comes back without the link.
    copy_id = learning.delete_goal(session, user, back)
    item = session.get(DeletedItem, copy_id)
    item.payload = item.payload | {
        "goal": item.payload["goal"] | {"source_application_id": theirs.id}
    }
    session.add(item)
    session.commit()
    post(auth_client, "/undo/delete", token=undo_service.make_delete_token(user, copy_id))

    [back] = learning.goals(session, user)
    assert back.source_application_id is None


def test_undo_a_goal_respects_the_limits(auth_client, session, user, goal, monkeypatch):
    deleted = post(auth_client, f"/learning/goals/{goal.id}/delete")
    monkeypatch.setattr(learning, "MAX_GOALS", 1)
    session.expunge(goal)  # the deleted goal's object; the new goal may get its id
    learning.create_goal(
        session,
        user,
        title="New one",
        target_date=None,
        weekly_hours=None,
        resource=None,
        plan_weekly=False,
    )

    undo(auth_client, deleted)

    assert [g.title for g in learning.goals(session, user)] == ["New one"]
    assert "Nothing to undo." in auth_client.get("/learning").text


def test_another_users_undo_token_does_nothing(auth_client, session, user, goal, other):
    deleted = post(auth_client, f"/learning/goals/{goal.id}/delete")
    copy_id = session.exec(select(DeletedItem)).one().id

    # A token made for the other user, for this user's copy: refused (wrong owner).
    response = post(
        auth_client, "/undo/delete", token=undo_service.make_delete_token(other, copy_id)
    )

    assert response.status_code == 204
    assert learning.goals(session, user) == []
    assert deleted.status_code == 204


def test_delete_another_users_goal_is_404(auth_client, session, other):
    theirs = LearningGoal(user_id=other.id, title="Theirs")
    session.add(theirs)
    session.commit()

    response = post(auth_client, f"/learning/goals/{theirs.id}/delete")

    assert response.status_code == 404
    assert session.get(LearningGoal, theirs.id) is not None


def test_delete_goal_needs_the_csrf_token(auth_client, session, user, goal):
    assert auth_client.post(f"/learning/goals/{goal.id}/delete").status_code == 403
    assert len(learning.goals(session, user)) == 1


def test_delete_goal_needs_a_login(client, session, goal):
    response = client.post(
        f"/learning/goals/{goal.id}/delete",
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.get(LearningGoal, goal.id) is not None
