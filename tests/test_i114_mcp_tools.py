"""MCP tools of #114: propose_learning_changes' new arguments and the single tools
move_task_to_goal, delete_task, update_goal and archive_goal (D-202, D-203)."""

import asyncio
import datetime as dt
import json

import pytest

from app import mcp_server as m
from app.models import LearningGoal, LearningTask
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user


def day(offset: int) -> str:
    return (clock.today() + dt.timedelta(days=offset)).isoformat()


def add_goal(session, user, title, **fields) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, **fields)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def add_task(session, goal, title, **fields) -> LearningTask:
    task = LearningTask(
        user_id=goal.user_id,
        goal_id=goal.id,
        title=title,
        due_date=clock.today(),
        estimated_minutes=60,
        **fields,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@pytest.fixture
def python(session, user):
    return add_goal(session, user, "Python")


@pytest.fixture
def german(session, user):
    return add_goal(session, user, "German", weekly_hours_target=2.0)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- propose_learning_changes -----------------------------------------------------------------


def test_propose_takes_the_new_arguments(session, user, python, german):
    loops = add_task(session, python, "Loops")
    lists = add_task(session, python, "Lists")

    result = m.propose_learning_changes(
        session,
        user,
        updates=[m.Update(task_id=loops.id, goal_id=german.id)],
        deletes=[m.Delete(task_id=lists.id)],
        goal_updates=[m.GoalUpdate(goal_id=german.id, weekly_hours=None, title="Deutsch")],
        new_goals=["Rust", m.NewGoal(title="Go", weekly_hours=1.5, target_date=day(40))],
    )

    preview = result["preview"]
    assert "- Loops [Python → German]" in preview
    assert "- Lists [Python]: delete" in preview
    m.apply_changes(session, user, result["change_set_id"])
    session.refresh(german)
    assert (german.title, german.weekly_hours_target) == ("Deutsch", None)
    assert {g.title for g in learning.goals(session, user)} == {"Python", "Deutsch", "Rust", "Go"}
    assert [t.title for t in learning.tasks(session, user)] == ["Loops"]

    undone = m.undo_changes(session, user, result["change_set_id"])

    assert undone["undone"] == 5
    assert {t.title for t in learning.tasks(session, user)} == {"Loops", "Lists"}
    assert [g.title for g in learning.goals(session, user)] == ["Python", "German"]


def test_a_goal_update_leaves_out_fields_not_given(session, user, german):
    m.apply_changes(
        session,
        user,
        m.propose_learning_changes(
            session, user, goal_updates=[m.GoalUpdate(goal_id=german.id, status="paused")]
        )["change_set_id"],
    )

    session.refresh(german)
    assert (german.status, german.weekly_hours_target) == ("paused", 2.0)


def test_propose_errors_are_messages(session, user, python, other):
    theirs = add_goal(session, other, "Theirs")

    with pytest.raises(m.Failed, match="not found"):
        m.propose_learning_changes(
            session, user, goal_updates=[m.GoalUpdate(goal_id=theirs.id, archived=True)]
        )


# --- Single tools -----------------------------------------------------------------------------


def test_move_task_to_goal(session, user, python, german):
    task = add_task(session, python, "Loops", done=True, done_at=clock.utcnow())

    result = m.move_task_to_goal(session, user, task.id, german.id)

    session.refresh(task)
    assert (task.goal_id, task.done) == (german.id, True)
    assert "[Python → German]" in result["changed"]
    m.undo_changes(session, user, result["change_set_id"])
    session.refresh(task)
    assert task.goal_id == python.id


def test_move_to_the_same_goal_says_nothing_would_change(session, user, python):
    task = add_task(session, python, "Loops")

    with pytest.raises(m.Failed, match="Nothing would change"):
        m.move_task_to_goal(session, user, task.id, python.id)


def test_delete_task_and_undo(session, user, python):
    task = add_task(session, python, "Loops")

    result = m.delete_task(session, user, task.id)

    assert learning.tasks(session, user) == []
    with pytest.raises(m.Failed, match="was deleted"):
        m.set_task_done(session, user, task.id)
    m.undo_changes(session, user, result["change_set_id"])
    assert [t.id for t in learning.tasks(session, user)] == [task.id]


def test_update_goal_and_clear(session, user, german):
    m.update_goal(session, user, german.id, title="Deutsch", target_date=day(30))
    m.update_goal(session, user, german.id, clear=["weekly_hours"])

    session.refresh(german)
    assert (german.title, german.target_date.isoformat(), german.weekly_hours_target) == (
        "Deutsch",
        day(30),
        None,
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "at least one"),
        ({"clear": ["title"]}, "clear takes"),
        ({"weekly_hours": 3, "clear": ["weekly_hours"]}, "both given and cleared"),
        ({"status": "archived"}, "status"),
    ],
)
def test_update_goal_checks(session, user, german, kwargs, message):
    with pytest.raises(m.Failed, match=message):
        m.update_goal(session, user, german.id, **kwargs)


def test_archive_goal_and_unarchive(session, user, python):
    add_task(session, python, "Loops")

    result = m.archive_goal(session, user, python.id)

    assert m.get_learning_plan(session, user)["goals"] == []
    with pytest.raises(m.Failed, match="archived"):
        m.update_goal(session, user, python.id, title="X")
    m.undo_changes(session, user, result["change_set_id"])
    assert [g["title"] for g in m.get_learning_plan(session, user)["goals"]] == ["Python"]

    m.archive_goal(session, user, python.id)
    m.archive_goal(session, user, python.id, archived=False)
    assert len(m.get_learning_plan(session, user)["today_tasks"]) == 1


def test_single_tools_only_see_the_users_rows(session, user, python, other):
    theirs = add_goal(session, other, "Theirs")
    their_task = add_task(session, theirs, "Their task")

    for call in (
        lambda: m.move_task_to_goal(session, user, their_task.id, python.id),
        lambda: m.move_task_to_goal(session, user, 999999, theirs.id),
        lambda: m.delete_task(session, user, their_task.id),
        lambda: m.update_goal(session, user, theirs.id, title="Mine"),
        lambda: m.archive_goal(session, user, theirs.id),
    ):
        with pytest.raises(m.Failed, match="not found"):
            call()
    session.refresh(theirs)
    session.refresh(their_task)
    assert (theirs.title, theirs.archived_at, their_task.deleted_at) == ("Theirs", None, None)


# --- Registration -----------------------------------------------------------------------------


def test_new_tools_are_registered_with_hints(engine, user):
    from mcp import Client

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in asyncio.run(main())}

    for name in ("delete_task", "archive_goal"):
        assert tools[name].annotations.destructive_hint is True, name
    for name in ("move_task_to_goal", "update_goal"):
        assert tools[name].annotations.destructive_hint is False, name
    schema = tools["propose_learning_changes"].input_schema["properties"]
    assert {"deletes", "goal_updates"} <= set(schema)


def test_delete_task_through_the_client(engine, session, user, python):
    from mcp import Client

    task = add_task(session, python, "Loops")

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return await client.call_tool("delete_task", {"task_id": task.id})

    result = asyncio.run(main())

    assert not result.is_error
    assert "change_set_id" in json.loads(result.content[0].text)
    session.refresh(task)
    assert task.deleted_at is not None
