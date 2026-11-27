"""Soft-deleted tasks and archived goals are hidden and read-only (D-200, D-201, #114).

History is not: the streak and the logged hours stay as they were."""

import datetime as dt

import pytest

from app import mcp_server as m
from app.models import LearningGoal, LearningTask, StudyLog
from app.services import change_set_service as changes
from app.services import clock, notification_service, undo_service
from app.services import learning_service as learning
from app.services.auth_service import create_user
from app.services.change_set_service import ChangeSetError
from tests.helpers import csrf_token

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


@pytest.fixture(autouse=True)
def vienna():
    with clock.use_timezone("Europe/Vienna"):
        yield


def day(offset: int) -> dt.date:
    return WED + dt.timedelta(days=offset)


def add_goal(session, user, title="Python", **fields) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, **fields)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def add_task(session, goal, title, offset=0, **fields) -> LearningTask:
    task = LearningTask(
        user_id=goal.user_id,
        goal_id=goal.id,
        title=title,
        due_date=day(offset),
        estimated_minutes=60,
        **fields,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def soft_delete(session, task):
    task.deleted_at = NOW
    session.add(task)
    session.commit()


def archive(session, goal):
    goal.archived_at = NOW
    session.add(goal)
    session.commit()


def post(client, url, **fields):
    return client.post(
        url, data=fields, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


@pytest.fixture
def live(session, user):
    return add_goal(session, user, "Python", weekly_hours_target=2.0)


@pytest.fixture
def old(session, user):
    """An archived goal with an open and a done task."""
    goal = add_goal(session, user, "German", weekly_hours_target=3.0)
    add_task(session, goal, "German open", 0)
    add_task(session, goal, "German done", 0, done=True, done_at=NOW)
    archive(session, goal)
    return goal


@pytest.fixture
def gone(session, live):
    """A soft-deleted open task of the live goal."""
    task = add_task(session, live, "Deleted task", 0)
    soft_delete(session, task)
    return task


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- Reading -------------------------------------------------------------------------------


def test_lists_leave_out_deleted_tasks_and_archived_goals(session, user, live, old, gone):
    add_task(session, live, "Kept", 0)

    assert [g.title for g in learning.goals(session, user)] == ["Python"]
    assert {g.title for g in learning.all_goals(session, user)} == {"Python", "German"}
    assert [g.title for g in learning.archived_goals(session, user)] == ["German"]
    assert [t.title for t in learning.tasks(session, user)] == ["Kept"]


def test_page_hides_them(auth_client, session, live, old, gone):
    add_task(session, live, "Kept", 0)

    page = auth_client.get("/learning?view=all").text

    assert "Kept" in page
    assert "Deleted task" not in page
    assert "German open" not in page and "German done" not in page
    assert f'data-goal="{old.id}"' not in page  # only under "Show archived" (D-201)


def test_this_week_skips_them(session, user, live, old, gone):
    add_task(session, live, "Kept", 0)

    week = learning.this_week(session, user)

    assert (week.tasks_done, week.tasks_total) == (0, 1)
    assert week.target_hours == 2.0  # the archived goal's 3 h are not counted


def test_streak_and_hours_stay(session, user, live):
    task = add_task(session, live, "Done", 0, done=True, done_at=NOW)
    session.add(StudyLog(user_id=user.id, goal_id=live.id, task_id=task.id, date=WED, minutes=60))
    session.commit()
    before = (learning.streak(session, user), learning.minutes_by_goal(session, user))

    soft_delete(session, task)
    archive(session, live)

    assert (learning.streak(session, user), learning.minutes_by_goal(session, user)) == before


def test_reminder_ignores_them(session, user, live, gone):
    old = add_goal(session, user, "German")
    add_task(session, old, "German open", 0)
    archive(session, old)
    prefs = learning.preferences(session, user)
    assert notification_service.study_task(session, user, prefs, WED) is None

    kept = add_task(session, live, "Kept", 0)
    assert notification_service.study_task(session, user, prefs, WED).id == kept.id


# --- Changing them on the web -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["done", "edit", "delete", "reschedule"])
def test_web_task_routes_404_for_deleted_and_archived(auth_client, session, user, old, gone, path):
    archived_task = m.list_tasks(session, user, include_archived=True)["tasks"][0]
    for task_id in (gone.id, archived_task["id"]):
        url = f"/learning/tasks/{task_id}" + ("" if path == "edit" else f"/{path}")
        if path == "edit":
            response = auth_client.get(url + "/edit")
        else:
            response = post(auth_client, url, done="yes", due_date=day(1).isoformat())
        assert response.status_code == 404, (path, task_id)


def test_web_goal_routes_404_for_archived(auth_client, old):
    assert auth_client.get(f"/learning/goals/{old.id}/edit").status_code == 404
    assert auth_client.get(f"/learning/goals/{old.id}/delete").status_code == 404
    assert post(auth_client, f"/learning/goals/{old.id}/delete").status_code == 404


def test_tick_undo_leaves_a_task_deleted_since(auth_client, session, user, live):
    task = add_task(session, live, "T", 0)
    post(auth_client, f"/learning/tasks/{task.id}/done", done="yes")
    token = undo_service.make_learning_token(user, task.id, True)
    soft_delete(session, task)

    response = post(auth_client, "/learning/undo", token=token)

    session.refresh(task)
    assert task.done is True
    assert "Nothing to undo." in response.headers["HX-Trigger"]


def test_goal_delete_confirm_counts_live_tasks(auth_client, session, live, gone):
    add_task(session, live, "Kept", 0)

    page = auth_client.get(f"/learning/goals/{live.id}/delete").text

    assert "its tasks (1)" in page


# --- Limits ----------------------------------------------------------------------------------


def test_deleted_tasks_dont_count_archived_goals_do(session, user, live, old, gone, monkeypatch):
    # 2 tasks of the archived goal count, the deleted one doesn't.
    assert learning.count_rows(session, user, LearningTask) == 2
    assert learning.count_rows(session, user, LearningGoal) == 2
    monkeypatch.setattr(learning, "MAX_GOALS", 2)
    with pytest.raises(learning.TooMany):
        learning.create_goal(
            session,
            user,
            title="Third",
            target_date=None,
            weekly_hours=None,
            resource=None,
            plan_weekly=False,
        )


# --- MCP tools ---------------------------------------------------------------------------------


def test_mcp_plan_leaves_them_out(session, user, live, old, gone):
    plan = m.get_learning_plan(session, user)

    assert [g["title"] for g in plan["goals"]] == ["Python"]
    assert plan["today_tasks"] == []


def test_mcp_list_tasks_include_archived(session, user, live, old, gone):
    assert m.list_tasks(session, user, status="all")["total"] == 0

    found = m.list_tasks(session, user, status="all", include_archived=True)

    assert {t["title"] for t in found["tasks"]} == {"German open", "German done"}
    assert {t["goal"] for t in found["tasks"]} == {"German"}  # the title, not ""
    with pytest.raises(m.Failed, match="archived"):
        m.list_tasks(session, user, goal_id=old.id)
    assert m.list_tasks(session, user, goal_id=old.id, include_archived=True)["total"] == 1


def test_mcp_single_tools_refuse_them(session, user, old, gone):
    archived_task = m.list_tasks(session, user, include_archived=True)["tasks"][0]["id"]

    with pytest.raises(m.Failed, match="was deleted"):
        m.update_task(session, user, gone.id, title="X")
    with pytest.raises(m.Failed, match="archived goal"):
        m.set_task_done(session, user, archived_task)
    with pytest.raises(m.Failed, match="archived goal"):
        m.add_task(session, user, old.id, "New", day(1).isoformat())
    with pytest.raises(m.Failed, match="archived goal"):
        m.log_study_time(session, user, old.id, 30)


def test_mcp_goal_title_must_not_match_an_archived_one(session, user, old):
    with pytest.raises(m.Failed, match="archived goal is named"):
        m.create_learning_goal(session, user, "german")


def test_mcp_other_users_rows_stay_not_found(session, user, other):
    theirs = add_goal(session, other, "Theirs")
    task = add_task(session, theirs, "Their task", 0)
    soft_delete(session, task)

    with pytest.raises(m.Failed, match="not found"):
        m.update_task(session, user, task.id, title="X")
    with pytest.raises(m.Failed, match="not found"):
        m.list_tasks(session, user, goal_id=theirs.id, include_archived=True)


# --- Change sets ------------------------------------------------------------------------------


def test_shift_skips_them(session, user, live, old, gone):
    kept = add_task(session, live, "Kept", 0)

    change_set = changes.propose(session, user, shift={"days": 1})

    assert [c["target_id"] for c in change_set.changes] == [kept.id]


def test_propose_refuses_archived_and_deleted(session, user, live, old, gone):
    archived_task = m.list_tasks(session, user, include_archived=True)["tasks"][0]["id"]

    with pytest.raises(ChangeSetError, match="archived goal"):
        changes.propose(session, user, moves=[{"task_id": archived_task, "due_date": "2026-11-20"}])
    with pytest.raises(ChangeSetError, match="not found"):
        changes.propose(session, user, moves=[{"task_id": gone.id, "due_date": "2026-11-20"}])
    with pytest.raises(ChangeSetError, match="archived"):
        changes.propose(
            session, user, adds=[{"title": "A", "due_date": "2026-11-20", "goal_id": old.id}]
        )
    with pytest.raises(ChangeSetError, match="archived"):
        changes.propose(
            session, user, adds=[{"title": "A", "due_date": "2026-11-20", "goal_title": "German"}]
        )
    with pytest.raises(ChangeSetError, match="archived goal is named"):
        changes.propose(session, user, new_goals=["German"])
    with pytest.raises(ChangeSetError, match="archived"):
        changes.propose(session, user, shift={"days": 1, "goal_id": old.id})


def test_apply_skips_a_task_archived_or_deleted_after_the_preview(session, user, live):
    first = add_task(session, live, "First", 0)
    second = add_task(session, live, "Second", 0)
    other_goal = add_goal(session, user, "Other")
    third = add_task(session, other_goal, "Third", 0)
    change_set = changes.propose(session, user, shift={"days": 1})

    soft_delete(session, first)
    archive(session, other_goal)
    result = changes.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (1, 2)
    for task, due in ((first, day(0)), (second, day(1)), (third, day(0))):
        session.refresh(task)
        assert task.due_date == due


def test_undo_leaves_a_task_deleted_or_archived_since(session, user, live):
    first = add_task(session, live, "First", 0)
    other_goal = add_goal(session, user, "Other")
    second = add_task(session, other_goal, "Second", 0)
    change_set = changes.propose(session, user, shift={"days": 1})
    changes.apply(session, user, change_set.id)

    soft_delete(session, first)
    archive(session, other_goal)
    result = changes.undo(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 2)
    for task in (first, second):
        session.refresh(task)
        assert task.due_date == day(1)


def test_undo_keeps_an_added_goal_archived_since(session, user):
    change_set = changes.propose(session, user, new_goals=["Fresh"])
    changes.apply(session, user, change_set.id)
    goal = learning.all_goals(session, user)[0]
    archive(session, goal)

    result = changes.undo(session, user, change_set.id)

    assert result.skipped == 1
    assert learning.archived_goals(session, user)[0].title == "Fresh"
