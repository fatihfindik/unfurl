"""The local MCP server (D-191, D-194, #107).

Tool logic is tested by calling the plain functions; one in-process client checks the
registered tools, and one subprocess test talks real stdio to `python -m app.mcp_server`.
"""

import asyncio
import datetime as dt
import json
import os
import sys

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app import mcp_server as m
from app.models import LearningGoal, LearningTask, StudyLog
from app.models.base import utcnow
from app.services import clock
from app.services.auth_service import create_user
from tests.conftest import USER_EMAIL


def day(offset: int) -> str:
    return (clock.today() + dt.timedelta(days=offset)).isoformat()


def add_goal(session, user, title="Python") -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def add_task(session, goal, title, offset=0, **fields) -> LearningTask:
    task = LearningTask(
        user_id=goal.user_id,
        goal_id=goal.id,
        title=title,
        due_date=clock.today() + dt.timedelta(days=offset),
        estimated_minutes=60,
        **fields,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@pytest.fixture
def goal(session, user):
    return add_goal(session, user)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- Reading -------------------------------------------------------------------------------


def test_learning_plan(session, user, goal):
    add_task(session, goal, "Overdue", -2)
    add_task(session, goal, "Today", 0)
    add_task(session, goal, "Soon", 3)
    add_task(session, goal, "Done", 0, done=True, done_at=utcnow())

    plan = m.get_learning_plan(session, user)

    assert plan["today"] == day(0)
    assert [t["title"] for t in plan["overdue"]] == ["Overdue"]
    assert [t["title"] for t in plan["today_tasks"]] == ["Today"]
    assert [t["title"] for t in plan["next_7_days"]] == ["Soon"]
    (info,) = plan["goals"]
    assert (info["title"], info["open_tasks"], info["done_tasks"]) == ("Python", 3, 1)


def test_list_tasks_filters_and_pages(session, user, goal):
    german = add_goal(session, user, "German")
    for n in range(5):
        add_task(session, goal, f"Py {n}", n)
    add_task(session, german, "De 0", 0)
    add_task(session, goal, "Old done", -1, done=True, done_at=utcnow())

    page = m.list_tasks(session, user, goal_id=goal.id, limit=2, offset=1)
    assert page["total"] == 5 and [t["title"] for t in page["tasks"]] == ["Py 1", "Py 2"]
    assert m.list_tasks(session, user, status="done")["tasks"][0]["title"] == "Old done"
    assert m.list_tasks(session, user, text="de ")["total"] == 1
    assert m.list_tasks(session, user, from_date=day(3))["total"] == 2
    assert m.list_tasks(session, user, status="all")["total"] == 7


@pytest.mark.parametrize(
    "kwargs",
    [{"status": "later"}, {"limit": 0}, {"limit": 201}, {"offset": -1}, {"from_date": "1/12"}],
)
def test_list_tasks_bad_input(session, user, goal, kwargs):
    with pytest.raises(m.Failed):
        m.list_tasks(session, user, **kwargs)


def test_another_users_data_is_never_reached(session, user, goal, other):
    theirs = add_goal(session, other, "Their goal")
    their_task = add_task(session, theirs, "Their task", 1)

    assert "Their" not in str(m.get_learning_plan(session, user))
    assert m.list_tasks(session, user, status="all")["total"] == 0
    calls = [
        lambda: m.list_tasks(session, user, goal_id=theirs.id),
        lambda: m.add_task(session, user, theirs.id, "X", day(1)),
        lambda: m.update_task(session, user, their_task.id, title="Mine"),
        lambda: m.set_task_done(session, user, their_task.id),
        lambda: m.log_study_time(session, user, theirs.id, 30),
        lambda: m.propose_learning_changes(
            session, user, moves=[m.Move(task_id=their_task.id, due_date=day(2))]
        ),
    ]
    for call in calls:
        with pytest.raises(m.Failed, match="not found"):
            call()
    session.refresh(their_task)
    assert (their_task.title, their_task.done) == ("Their task", False)


# --- One record at a time ------------------------------------------------------------------


def test_create_goal_and_add_task(session, user):
    goal = m.create_learning_goal(session, user, " Toolbox ", target_date=day(60), weekly_hours=4)
    task = m.add_task(session, user, goal["id"], "Zapier", day(2), 120, "https://zapier.com")

    assert goal["title"] == "Toolbox"
    assert (task["goal"], task["minutes"], task["url"]) == ("Toolbox", 120, "https://zapier.com")
    with pytest.raises(m.Failed, match="already have"):
        m.create_learning_goal(session, user, "toolbox")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"title": ""}, "empty"),
        ({"due_date": "2026-13-01"}, "YYYY-MM-DD|date"),
        ({"due_date": "2001-01-01"}, "days ago"),
        ({"minutes": 4}, "minutes"),
        ({"url": "javascript:alert(1)"}, "https"),
    ],
)
def test_add_task_checks_like_the_form(session, user, goal, kwargs, message):
    args = {"title": "A", "due_date": day(1), "minutes": 60, "url": None} | kwargs
    with pytest.raises(m.Failed, match=message):
        m.add_task(session, user, goal.id, **args)
    assert session.exec(select(LearningTask)).all() == []


def test_update_task(session, user, goal):
    task = add_task(session, goal, "A", 1, url="https://old.dev")
    info = m.update_task(session, user, task.id, title="B", due_date=day(4), minutes=30, url="")
    assert (info["title"], info["due_date"], info["minutes"], info["url"]) == (
        "B",
        day(4),
        30,
        None,
    )

    done = add_task(session, goal, "Done", 0, done=True, done_at=utcnow())
    with pytest.raises(m.Failed, match="only its title"):
        m.update_task(session, user, done.id, due_date=day(2))
    assert m.update_task(session, user, done.id, title="Renamed")["title"] == "Renamed"


def test_set_done_and_log_time(session, user, goal):
    task = add_task(session, goal, "A", 0)

    assert m.set_task_done(session, user, task.id)["done"] is True
    assert len(session.exec(select(StudyLog)).all()) == 1
    # Reopening would remove the logged time: only the app does it (security review F1).
    import inspect

    assert "done" not in inspect.signature(m.set_task_done).parameters

    logged = m.log_study_time(session, user, goal.id, 45, day(-1))
    assert logged == {"goal_id": goal.id, "date": day(-1), "minutes": 45}
    for bad in ({"minutes": 0}, {"minutes": 601}, {"minutes": 30, "date": day(1)}):
        with pytest.raises(m.Failed):
            m.log_study_time(session, user, goal.id, **bad)


# --- Preview, apply, undo ------------------------------------------------------------------


def test_propose_apply_undo_through_the_tools(session, user, goal):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 2)

    preview = m.propose_learning_changes(session, user, shift=m.Shift(days=3))
    assert preview["changes"] == 2 and "2 × move" in preview["preview"]
    assert "apply_changes" in preview["next_step"]
    session.refresh(a)
    assert a.due_date.isoformat() == day(1)  # nothing changed yet

    applied = m.apply_changes(session, user, preview["change_set_id"])
    assert applied["applied"] == 2
    session.refresh(b)
    assert b.due_date.isoformat() == day(5)

    undone = m.undo_changes(session, user, preview["change_set_id"])
    assert undone["undone"] == 2
    session.refresh(b)
    assert b.due_date.isoformat() == day(2)


def test_propose_adds_a_block_with_a_new_goal(session, user, goal):
    preview = m.propose_learning_changes(
        session,
        user,
        new_goals=["Toolbox"],
        adds=[
            m.NewTask(title=f"Toolbox ({n}/3)", due_date=day(n), minutes=120, goal_title="Toolbox")
            for n in range(1, 4)
        ],
    )
    m.apply_changes(session, user, preview["change_set_id"])

    titles = m.list_tasks(session, user, text="Toolbox")
    assert titles["total"] == 3


def test_tool_errors_are_messages(session, user, goal):
    with pytest.raises(m.Failed, match="Nothing would change"):
        m.propose_learning_changes(session, user)
    with pytest.raises(m.Failed, match="Unknown change set"):
        m.apply_changes(session, user, 999)
    with pytest.raises(m.Failed, match="Unknown change set"):
        m.discard_changes(session, user, 999)


# --- The registered server ------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def payload(result) -> dict:
    """A tool's answer: JSON text in the first content block."""
    return json.loads(result.content[0].text)


def test_tools_are_registered_with_hints(engine, user):
    from mcp import Client

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in run(main())}

    assert set(tools) == {fn.__name__ for fn, _ in m.TOOLS}
    assert tools["get_learning_plan"].annotations.read_only_hint is True
    assert tools["apply_changes"].annotations.destructive_hint is True
    assert tools["undo_changes"].annotations.destructive_hint is True
    assert "session" not in tools["add_task"].input_schema["properties"]
    assert "user" not in tools["add_task"].input_schema["properties"]


def test_a_tool_call_runs_for_the_servers_user_in_their_time_zone(engine, session, user):
    from mcp import Client

    user.timezone = "Pacific/Kiritimati"  # UTC+14: "today" there is often tomorrow in UTC
    session.add(user)
    session.commit()
    add_goal(session, user)

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return await client.call_tool("get_learning_plan", {})

    result = run(main())

    assert not result.is_error
    with clock.use_timezone("Pacific/Kiritimati"):
        expected = clock.today().isoformat()
    assert payload(result)["today"] == expected


def test_a_failed_tool_call_shows_the_message(engine, user):
    from mcp import Client

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return await client.call_tool("set_task_done", {"task_id": 12345})

    result = run(main())

    assert result.is_error and "Task 12345 not found." in result.content[0].text


def test_a_deleted_account_gets_an_error(engine, session, user):
    from mcp import Client

    server = m.build_server(engine, user.id)
    session.delete(user)
    session.commit()

    async def main():
        async with Client(server) as client:
            return await client.call_tool("get_learning_plan", {})

    result = run(main())

    assert result.is_error and "no longer exists" in result.content[0].text


# --- Real stdio ----------------------------------------------------------------------------


def test_stdio_subprocess_speaks_only_mcp_on_stdout(tmp_path):
    """python -m app.mcp_server over real stdin/stdout: the handshake and a tool call
    work, so nothing else was written to stdout."""
    from mcp import Client, StdioServerParameters

    url = f"sqlite:///{tmp_path / 'mcp.db'}"
    disk = create_engine(url)
    SQLModel.metadata.create_all(disk)
    with Session(disk) as session:
        create_user(session, email=USER_EMAIL, password="x" * 12, full_name="Jana")
    disk.dispose()

    env = os.environ | {"DATABASE_URL": url}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "app.mcp_server", "--email", USER_EMAIL], env=env
    )

    async def main():
        async with Client(params, read_timeout_seconds=30) as client:
            tools = await client.list_tools()
            plan = await client.call_tool("get_learning_plan", {})
            return tools, plan

    tools, plan = run(main())

    assert len(tools.tools) == len(m.TOOLS)
    assert payload(plan)["goals"] == []


def test_unknown_email_stops_the_server(engine, session):
    with pytest.raises(SystemExit, match="No Unfurl user"):
        m.find_user(session, "nobody@example.com")
