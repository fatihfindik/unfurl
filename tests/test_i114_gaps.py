"""Gaps in the tests of #114 (D-200…D-204): other users' rows in crafted sets for the
new ops, odd ids, rows changed between apply and undo, and sets that are no longer
waiting. The happy paths are in test_i114_*.py."""

import asyncio
import datetime as dt
import json

import pytest
from sqlmodel import func, select

from app import mcp_server as m
from app.models import LearningGoal, LearningTask, User
from app.models.base import utcnow
from app.models.change_set import PendingChangeSet
from app.services import account_service, clock
from app.services import change_set_service as cs
from app.services import learning_service as learning
from app.services.auth_service import create_user
from app.services.change_set_service import ChangeSetError
from tests.helpers import csrf_token


def day(offset: int) -> dt.date:
    return clock.today() + dt.timedelta(days=offset)


def add_goal(session, user, title, **fields) -> LearningGoal:
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


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


def snapshot(session, user) -> list[tuple]:
    """A user's tasks and goals, with the #114 columns (deleted_at, archived_at)."""
    session.expire_all()
    tasks = session.exec(
        select(LearningTask).where(LearningTask.user_id == user.id).order_by(LearningTask.id)
    )
    goals = session.exec(
        select(LearningGoal).where(LearningGoal.user_id == user.id).order_by(LearningGoal.id)
    )
    return [(t.id, t.goal_id, t.title, t.done, t.deleted_at) for t in tasks] + [
        (g.id, g.title, g.status, g.weekly_hours_target, g.archived_at) for g in goals
    ]


def set_count(session) -> int:
    return session.exec(select(func.count()).select_from(PendingChangeSet)).one()


def post(client, url, **fields):
    return client.post(
        url,
        data=fields,
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )


@pytest.fixture
def python(session, user):
    return add_goal(session, user, "Python")  # id 1


@pytest.fixture
def german(session, user, python):
    return add_goal(session, user, "German")  # id 2


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- Crafted sets naming another user's rows ------------------------------------------------


def test_apply_of_a_crafted_set_skips_another_users_rows_for_the_new_ops(
    session, user, python, other
):
    """Defence in depth: propose never builds these, but a stored set naming another
    user's task or goal must be skipped at apply."""
    mine = add_task(session, python, "Mine")
    their_goal = add_goal(session, other, "Their secret goal")
    their_task = add_task(session, their_goal, "Their secret task")
    before_theirs = snapshot(session, other)
    before_mine = snapshot(session, user)
    move = {"goal_id": their_goal.id, "resource_id": None}
    change_set = PendingChangeSet(
        user_id=user.id,
        changes=[
            {
                "op": "delete",
                "target_id": their_task.id,
                "goal_id": their_goal.id,
                "title": "x",
                "before": {"deleted": False},
                "after": {"deleted": True},
            },
            {  # their task into my goal
                "op": "update",
                "target_id": their_task.id,
                "goal_id": their_goal.id,
                "title": "x",
                "before": move,
                "after": {"goal_id": python.id, "resource_id": None},
            },
            {  # my task into their goal
                "op": "update",
                "target_id": mine.id,
                "goal_id": python.id,
                "title": "x",
                "before": {"goal_id": python.id, "resource_id": None},
                "after": move,
            },
            {
                "op": "update_goal",
                "target_id": their_goal.id,
                "title": "x",
                "before": {"title": their_goal.title, "archived": False},
                "after": {"title": "Pwned", "archived": True},
            },
        ],
    )
    session.add(change_set)
    session.commit()

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 4)
    assert snapshot(session, other) == before_theirs
    assert snapshot(session, user) == before_mine


def test_undo_of_a_crafted_set_skips_another_users_rows_for_the_new_ops(
    session, user, python, other
):
    mine = add_task(session, python, "Mine")
    their_goal = add_goal(session, other, "Their secret goal")
    their_task = add_task(session, their_goal, "Their secret task")
    their_old = add_goal(session, other, "Their old goal")
    # They delete their task and archive a goal themselves.
    theirs_set = cs.propose(
        session,
        other,
        deletes=[{"task_id": their_task.id}],
        goal_updates=[{"goal_id": their_old.id, "archived": True}],
    )
    cs.apply(session, other, theirs_set.id)
    deleted_at = fresh(session, theirs_set).changes[0]["deleted_at"]
    before_theirs = snapshot(session, other)
    before_mine = snapshot(session, user)
    applied = PendingChangeSet(
        user_id=user.id,
        status="applied",
        applied_at=utcnow(),
        changes=[
            {  # would bring their deleted task back
                "op": "delete",
                "target_id": their_task.id,
                "goal_id": their_goal.id,
                "title": "x",
                "before": {"deleted": False},
                "after": {"deleted": True},
                "deleted_at": deleted_at,
                "applied": True,
            },
            {  # would move my task "back" into their goal
                "op": "update",
                "target_id": mine.id,
                "goal_id": their_goal.id,
                "title": "x",
                "before": {"goal_id": their_goal.id, "resource_id": None},
                "after": {"goal_id": python.id, "resource_id": None},
                "applied": True,
            },
            {
                "op": "update_goal",
                "target_id": their_goal.id,
                "title": "x",
                "before": {"title": "Pwned"},
                "after": {"title": their_goal.title},
                "applied": True,
            },
            {  # would unarchive their goal
                "op": "update_goal",
                "target_id": their_old.id,
                "title": "x",
                "before": {"archived": False},
                "after": {"archived": True},
                "applied": True,
            },
        ],
    )
    session.add(applied)
    session.commit()

    result = cs.undo(session, user, applied.id)

    assert (result.done, result.skipped) == (0, 4)
    assert snapshot(session, other) == before_theirs
    assert snapshot(session, user) == before_mine


# --- Odd ids ---------------------------------------------------------------------------------

# Ids that are not plain ints, or are outside what the database can hold. "1", True and
# 1.0 must never reach the row with id 1.
ODD_IDS = ["1", True, False, 1.0, None, -1, 0, 2**63, 10**30]


@pytest.mark.parametrize("bad_id", ODD_IDS)
def test_propose_with_odd_ids_in_the_new_ops_is_refused(session, user, python, german, bad_id):
    task = add_task(session, german, "Loops")  # id 1, in goal 2
    before = snapshot(session, user)

    for request in (
        {"deletes": [{"task_id": bad_id}]},
        {"goal_updates": [{"goal_id": bad_id, "title": "Renamed"}]},
        {"goal_updates": [{"goal_id": bad_id, "archived": True}]},
    ):
        with pytest.raises(ChangeSetError, match="not found"):
            cs.propose(session, user, **request)
    # goal_id None in an update means "leave the goal as it is".
    message = "Nothing would change" if bad_id is None else "not found"
    with pytest.raises(ChangeSetError, match=message):
        cs.propose(session, user, updates=[{"task_id": task.id, "goal_id": bad_id}])

    assert snapshot(session, user) == before
    assert set_count(session) == 0


def test_an_id_equal_to_another_in_the_request_is_not_a_second_task(session, user, python):
    # True == 1 in Python: a delete of True must not delete task 1, which an update
    # in the same request names (it is refused as a whole).
    task = add_task(session, python, "Loops")
    assert task.id == 1

    with pytest.raises(ChangeSetError):
        cs.propose(
            session,
            user,
            updates=[{"task_id": 1, "title": "Renamed"}],
            deletes=[{"task_id": True}],
        )

    assert fresh(session, task).deleted_at is None
    assert set_count(session) == 0


@pytest.mark.parametrize("bad_id", ["1", True, 1.0, -1, 0, 2**63, 10**30])
def test_single_tools_with_odd_ids_say_not_found(session, user, python, german, bad_id):
    task = add_task(session, german, "Loops")  # id 1, in goal 2
    before = snapshot(session, user)

    for call in (
        lambda: m.delete_task(session, user, bad_id),
        lambda: m.move_task_to_goal(session, user, bad_id, python.id),
        lambda: m.move_task_to_goal(session, user, task.id, bad_id),
        lambda: m.update_goal(session, user, bad_id, title="Renamed"),
        lambda: m.archive_goal(session, user, bad_id),
        lambda: m.archive_goal(session, user, bad_id, archived=False),
    ):
        with pytest.raises(m.Failed, match="not found"):
            call()

    assert snapshot(session, user) == before
    assert set_count(session) == 0


def test_server_errors_for_huge_ids_in_the_new_tools_are_plain_messages(engine, user, python):
    from mcp import Client

    calls = [
        ("delete_task", {"task_id": 2**63}),
        ("move_task_to_goal", {"task_id": 2**63, "goal_id": python.id}),
        ("update_goal", {"goal_id": 2**63, "title": "X"}),
        ("archive_goal", {"goal_id": 2**63}),
        ("propose_learning_changes", {"deletes": [{"task_id": 2**63}]}),
        ("propose_learning_changes", {"goal_updates": [{"goal_id": 2**63, "archived": True}]}),
    ]

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    for result in asyncio.run(main()):
        text = result.content[0].text
        assert result.is_error
        assert "not found" in text
        assert "SELECT" not in text


# --- Another user's archived goal ------------------------------------------------------------


def test_another_users_archived_goal_cant_be_unarchived_or_changed(session, user, python, other):
    theirs = add_goal(session, other, "Their secret goal", archived_at=utcnow())
    before = snapshot(session, other)

    for call in (
        lambda: m.archive_goal(session, user, theirs.id, archived=False),
        lambda: m.update_goal(session, user, theirs.id, title="Mine"),
        lambda: m.propose_learning_changes(
            session, user, goal_updates=[m.GoalUpdate(goal_id=theirs.id, archived=False)]
        ),
    ):
        with pytest.raises(m.Failed) as error:
            call()
        assert "not found" in str(error.value)
        assert "secret" not in str(error.value)

    assert snapshot(session, other) == before


# --- Rows changed between apply and undo -----------------------------------------------------


def test_undo_of_a_delete_after_the_web_deleted_its_goal(auth_client, session, user, python):
    keep = add_task(session, python, "Keep")
    gone = add_task(session, python, "Gone")
    keep_title, gone_id, goal_id = keep.title, gone.id, python.id  # the rows go below
    result = m.delete_task(session, user, gone_id)

    # The web can't hard-delete a soft-deleted task on its own...
    assert post(auth_client, f"/learning/tasks/{gone_id}/delete").status_code == 404
    # ...but deleting its goal removes it with the goal's other tasks.
    response = post(auth_client, f"/learning/goals/{goal_id}/delete")
    assert response.status_code == 204
    session.expire_all()
    assert session.get(LearningTask, gone_id) is None

    undone = cs.undo(session, user, result["change_set_id"])

    assert (undone.done, undone.skipped) == (0, 1)
    assert fresh(session, undone.change_set).status == "undone"

    # The goal's own Undo brings back its live task; the deleted one stays hidden.
    token = json.loads(response.headers["HX-Trigger"])["toast"]["undo_token"]
    post(auth_client, "/undo/delete", token=token)
    assert [t.title for t in learning.tasks(session, user)] == [keep_title]


def test_undo_of_a_rename_skips_when_the_old_title_is_taken_again(session, user, python):
    result = m.update_goal(session, user, python.id, title="Py")
    m.create_learning_goal(session, user, "python")  # the old title is free, so allowed

    undone = cs.undo(session, user, result["change_set_id"])

    assert (undone.done, undone.skipped) == (0, 1)
    titles = sorted(g.title for g in learning.all_goals(session, user))
    assert titles == ["Py", "python"]


def test_apply_of_a_rename_skips_when_the_title_was_taken_after_the_preview(
    session, user, python, german
):
    change_set = cs.propose(
        session,
        user,
        goal_updates=[
            {"goal_id": python.id, "title": "Rust"},
            {"goal_id": german.id, "weekly_hours": 3},
        ],
    )
    m.create_learning_goal(session, user, "RUST")

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (1, 1)
    assert fresh(session, python).title == "Python"
    assert fresh(session, german).weekly_hours_target == 3.0


# --- The unarchive route ---------------------------------------------------------------------


def test_unarchive_after_the_account_was_deleted(auth_client, session, user):
    goal_id = add_goal(session, user, "Old", archived_at=utcnow()).id
    token = csrf_token(auth_client)
    account_service.delete_account(session, session.get(User, user.id))

    response = auth_client.post(
        f"/learning/goals/{goal_id}/unarchive",
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    session.expire_all()
    assert session.get(LearningGoal, goal_id) is None  # gone with the account


@pytest.mark.parametrize("goal_id", ["-1", "1.5", "99999999999999999999", str(2**63)])
def test_unarchive_with_odd_ids_is_404(auth_client, session, user, goal_id):
    goal = add_goal(session, user, "Old", archived_at=utcnow())  # id 1

    assert post(auth_client, f"/learning/goals/{goal_id}/unarchive").status_code == 404
    assert fresh(session, goal).archived_at is not None


# --- Sets that are no longer waiting ---------------------------------------------------------


def test_a_discarded_set_with_new_ops_cant_be_applied(session, user, python, german):
    task = add_task(session, python, "Loops")
    change_set = cs.propose(
        session,
        user,
        deletes=[{"task_id": task.id}],
        goal_updates=[{"goal_id": german.id, "archived": True}],
    )
    cs.discard(session, user, change_set.id)
    before = snapshot(session, user)

    with pytest.raises(ChangeSetError, match="discarded"):
        cs.apply(session, user, change_set.id)
    with pytest.raises(m.Failed, match="discarded"):
        m.apply_changes(session, user, change_set.id)

    assert snapshot(session, user) == before
    assert fresh(session, change_set).status == "discarded"


def test_an_expired_set_with_new_ops_cant_be_applied(session, user, python, german):
    task = add_task(session, python, "Loops")
    change_set = cs.propose(
        session,
        user,
        deletes=[{"task_id": task.id}],
        updates=[{"task_id": add_task(session, python, "Lists").id, "goal_id": german.id}],
        goal_updates=[{"goal_id": german.id, "title": "Deutsch"}],
    )
    change_set.created_at = utcnow() - dt.timedelta(minutes=61)
    session.add(change_set)
    session.commit()
    before = snapshot(session, user)

    with pytest.raises(ChangeSetError, match="older than an hour"):
        cs.apply(session, user, change_set.id)

    assert snapshot(session, user) == before
    assert fresh(session, change_set).status == "pending"


def test_a_single_tool_set_cant_be_applied_again_or_undone_late(session, user, python):
    task = add_task(session, python, "Loops")
    result = m.delete_task(session, user, task.id)
    set_id = result["change_set_id"]

    with pytest.raises(m.Failed, match="applied, not waiting"):
        m.apply_changes(session, user, set_id)

    change_set = session.get(PendingChangeSet, set_id)
    change_set.applied_at = utcnow() - dt.timedelta(hours=25)
    session.add(change_set)
    session.commit()
    with pytest.raises(m.Failed, match="24 hours"):
        m.undo_changes(session, user, set_id)
    assert fresh(session, task).deleted_at is not None  # still deleted


def test_an_undone_delete_cant_be_undone_again(session, user, python):
    task = add_task(session, python, "Loops")
    result = m.delete_task(session, user, task.id)
    m.undo_changes(session, user, result["change_set_id"])

    with pytest.raises(m.Failed, match="only applied ones"):
        m.undo_changes(session, user, result["change_set_id"])
    with pytest.raises(m.Failed, match="not waiting"):
        m.apply_changes(session, user, result["change_set_id"])
    assert fresh(session, task).deleted_at is None
