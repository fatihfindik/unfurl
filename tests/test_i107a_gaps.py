"""Gaps in the tests of the local MCP server and change sets (#107, D-191…D-195).

Ownership with odd ids, changes made between preview and apply/undo, limits at both
steps, other users' sets, export and account deletion, and "nothing is deleted".
"""

import asyncio
import datetime as dt
import json

import pytest
from sqlmodel import func, select

from app import mcp_server as m
from app.models import LearningGoal, LearningResource, LearningTask, StudyLog
from app.models.base import utcnow
from app.models.change_set import PendingChangeSet
from app.services import account_service, clock, export_service
from app.services import change_set_service as cs
from app.services import learning_service as learning
from app.services.auth_service import create_user
from app.services.change_set_service import ChangeSetError


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


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


def count(session, model, user=None) -> int:
    query = select(func.count()).select_from(model)
    if user is not None:
        query = query.where(model.user_id == user.id)
    return session.exec(query).one()


def snapshot(session, user) -> list[tuple]:
    """Everything of a user's plan that a tool could change."""
    session.expire_all()
    tasks = session.exec(
        select(LearningTask).where(LearningTask.user_id == user.id).order_by(LearningTask.id)
    )
    goals = session.exec(
        select(LearningGoal).where(LearningGoal.user_id == user.id).order_by(LearningGoal.id)
    )
    return [
        (t.id, t.goal_id, t.title, t.due_date, t.estimated_minutes, t.url, t.done) for t in tasks
    ] + [(g.id, g.title, g.description, g.target_date) for g in goals]


@pytest.fixture
def goal(session, user):
    return add_goal(session, user)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


@pytest.fixture
def theirs(session, other):
    """Another user's goal with one open task."""
    their_goal = add_goal(session, other, "Their secret goal")
    add_task(session, their_goal, "Their secret task", 1)
    return their_goal


def their_task(session, other) -> LearningTask:
    return session.exec(select(LearningTask).where(LearningTask.user_id == other.id)).one()


# --- Ownership with odd ids ------------------------------------------------------------------

# Ids that are not plain ints, or are outside what the database can hold.
ODD_IDS = ["1", True, False, 1.0, None, -1, 0, 2**63, 10**30]


@pytest.mark.parametrize("bad_id", ODD_IDS)
def test_propose_with_odd_task_ids_is_not_found(session, user, goal, bad_id):
    add_task(session, goal, "Mine", 1)  # id 1: "1" or True must not reach it
    for kwargs in (
        {"moves": [{"task_id": bad_id, "due_date": day(3)}]},
        {"updates": [{"task_id": bad_id, "title": "X"}]},
        {"completes": [{"task_id": bad_id}]},
    ):
        with pytest.raises(ChangeSetError, match="not found"):
            cs.propose(session, user, **kwargs)
    assert count(session, PendingChangeSet) == 0


@pytest.mark.parametrize("bad_id", ODD_IDS)
def test_propose_with_odd_goal_ids_is_not_found(session, user, goal, bad_id):
    add_task(session, goal, "Mine", 1)
    if bad_id is None:
        pytest.skip("goal_id None means 'no goal filter' / 'use goal_title'")
    with pytest.raises(ChangeSetError, match="not found"):
        cs.propose(session, user, shift={"days": 1, "goal_id": bad_id})
    with pytest.raises(ChangeSetError, match="not found"):
        cs.propose(session, user, adds=[{"goal_id": bad_id, "title": "X", "due_date": day(1)}])
    assert count(session, PendingChangeSet) == 0


@pytest.mark.parametrize("bad_id", ODD_IDS)
@pytest.mark.parametrize("action", [cs.apply, cs.undo, cs.discard])
def test_odd_set_ids_are_unknown(session, user, goal, action, bad_id):
    task = add_task(session, goal, "Mine", 1)
    cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(2)}])
    with pytest.raises(ChangeSetError, match="Unknown change set"):
        action(session, user, bad_id)


@pytest.mark.parametrize("as_text", [False, True])
def test_another_users_ids_never_reach_their_rows_through_the_tools(
    session, user, goal, other, theirs, as_text
):
    task = their_task(session, other)
    # The tool functions get ids from JSON; text ids are tried too.
    goal_id = str(theirs.id) if as_text else theirs.id
    task_id = str(task.id) if as_text else task.id
    before = snapshot(session, other)
    calls = [
        lambda: m.list_tasks(session, user, goal_id=goal_id),
        lambda: m.add_task(session, user, goal_id, "X", day(1)),
        lambda: m.update_task(session, user, task_id, title="Mine"),
        lambda: m.update_task(session, user, task_id, due_date=day(5)),
        lambda: m.set_task_done(session, user, task_id),
        lambda: m.log_study_time(session, user, goal_id, 30),
    ]
    for call in calls:
        with pytest.raises(m.Failed, match="not found") as error:
            call()
        assert "secret" not in str(error.value)
    assert snapshot(session, other) == before
    assert count(session, StudyLog) == 0


@pytest.mark.parametrize("bad_id", [2**63, 10**30, -5, 0])
def test_tools_with_huge_or_negative_ids_say_not_found(session, user, goal, bad_id):
    for call in (
        lambda: m.update_task(session, user, bad_id, title="X"),
        lambda: m.set_task_done(session, user, bad_id),
        lambda: m.add_task(session, user, bad_id, "X", day(1)),
        lambda: m.log_study_time(session, user, bad_id, 30),
        lambda: m.list_tasks(session, user, goal_id=bad_id),
    ):
        with pytest.raises(m.Failed, match="not found"):
            call()


@pytest.mark.parametrize("bad_id", [2**63, 10**30])
def test_change_set_tools_with_huge_ids_say_not_found(session, user, goal, bad_id):
    # pydantic accepts any int for task_id/goal_id/change_set_id; SQLite holds only 64 bits.
    with pytest.raises(m.Failed, match="not found"):
        m.propose_learning_changes(session, user, moves=[m.Move(task_id=bad_id, due_date=day(2))])
    with pytest.raises(m.Failed, match="not found"):
        m.propose_learning_changes(session, user, shift=m.Shift(days=1, goal_id=bad_id))
    for call in (m.apply_changes, m.undo_changes, m.discard_changes):
        with pytest.raises(m.Failed, match="Unknown change set"):
            call(session, user, bad_id)


def test_shift_of_another_users_goal_through_the_tool(session, user, goal, other, theirs):
    with pytest.raises(m.Failed, match="not found") as error:
        m.propose_learning_changes(session, user, shift=m.Shift(days=2, goal_id=theirs.id))
    assert "secret" not in str(error.value)
    assert count(session, PendingChangeSet) == 0


def test_shift_and_preview_only_see_the_users_own_tasks(session, user, goal, other, theirs):
    mine = add_task(session, goal, "Mine", 1)

    preview = m.propose_learning_changes(session, user, shift=m.Shift(days=2))

    change_set = session.get(PendingChangeSet, preview["change_set_id"])
    assert [c["target_id"] for c in change_set.changes] == [mine.id]
    assert "secret" not in preview["preview"]


def test_adds_by_title_of_another_users_goal_find_nothing(session, user, goal, other, theirs):
    with pytest.raises(ChangeSetError, match="no goal"):
        cs.propose(
            session,
            user,
            adds=[{"goal_title": "Their secret goal", "title": "X", "due_date": day(1)}],
        )
    # new_goals may use the same title: only the user's own goals count as taken.
    change_set = cs.propose(session, user, new_goals=["Their secret goal"])
    assert change_set.changes[0]["after"]["title"] == "Their secret goal"


def test_a_crafted_set_never_touches_another_users_rows(session, user, goal, other, theirs):
    """Defence in depth: even a stored set naming another user's rows (it can't be
    made through propose) is skipped at apply and at undo."""
    task = their_task(session, other)
    before = snapshot(session, other)
    change_set = PendingChangeSet(
        user_id=user.id,
        changes=[
            {
                "op": "move",
                "target_id": task.id,
                "title": "x",
                "before": {"due_date": task.due_date.isoformat()},
                "after": {"due_date": day(9)},
            },
            {
                "op": "complete",
                "target_id": task.id,
                "title": "x",
                "before": {"done": False},
                "after": {"done": True},
            },
            {
                "op": "add",
                "target_id": None,
                "before": None,
                "after": {
                    "title": "Planted",
                    "due_date": day(1),
                    "minutes": 60,
                    "url": None,
                    "goal_id": theirs.id,
                },
            },
        ],
    )
    session.add(change_set)
    session.commit()

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 3)
    assert snapshot(session, other) == before

    # An applied set whose created_id points at their task: undo must not delete it.
    applied = PendingChangeSet(
        user_id=user.id,
        status="applied",
        applied_at=utcnow(),
        changes=[
            {
                "op": "add",
                "target_id": None,
                "before": None,
                "applied": True,
                "created_id": task.id,
                "after": {
                    "title": task.title,
                    "due_date": task.due_date.isoformat(),
                    "minutes": task.estimated_minutes,
                    "url": task.url,
                    "goal_id": theirs.id,
                },
            },
            {
                "op": "add_goal",
                "ref": 0,
                "before": None,
                "applied": True,
                "created_id": theirs.id,
                "after": {"title": theirs.title},
            },
        ],
    )
    session.add(applied)
    session.commit()

    result = cs.undo(session, user, applied.id)

    assert result.done == 0
    assert snapshot(session, other) == before


def test_another_users_sets_are_untouched_by_apply_undo_discard(session, user, goal, other):
    their_goal = add_goal(session, other, "Theirs")
    t = add_task(session, their_goal, "T", 1)
    pending = cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(2)}])
    applied = cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(3)}])
    cs.apply(session, other, applied.id)

    for action in (cs.apply, cs.undo, cs.discard):
        for set_id in (pending.id, applied.id):
            with pytest.raises(ChangeSetError, match="Unknown change set") as error:
                action(session, user, set_id)
            assert "Theirs" not in str(error.value)
    for call in (m.apply_changes, m.undo_changes, m.discard_changes):
        with pytest.raises(m.Failed, match="Unknown change set"):
            call(session, user, applied.id)

    assert fresh(session, pending).status == "pending"
    assert fresh(session, applied).status == "applied"
    assert fresh(session, t).due_date.isoformat() == day(3)


# --- Changes between preview and apply / undo ------------------------------------------------


def test_apply_skips_a_task_deleted_in_between(session, user, goal):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 1)
    c = add_task(session, goal, "C", 0)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(4)}],
        updates=[{"task_id": b.id, "title": "B2"}],
        completes=[{"task_id": c.id}],
    )
    for task in (a, b, c):
        session.delete(fresh(session, task))
    session.commit()

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 3)
    assert count(session, LearningTask) == 0 and count(session, StudyLog) == 0


def test_apply_after_the_task_moved_to_another_goal(session, user, goal):
    german = add_goal(session, user, "German")
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(4)}])
    a = fresh(session, a)
    a.goal_id = german.id  # the user moved it in the app
    session.add(a)
    session.commit()

    result = cs.apply(session, user, change_set.id)

    # A task moved to another goal since the preview is skipped (D-193): the preview
    # was about the old goal, e.g. "shift Python by 3 days".
    assert (result.done, result.skipped) == (0, 1)
    a = fresh(session, a)
    assert (a.goal_id, a.due_date.isoformat()) == (german.id, day(1))


def test_apply_skips_adds_whose_goal_was_deleted_in_between(session, user, goal):
    doomed = add_goal(session, user, "Doomed")
    change_set = cs.propose(
        session,
        user,
        adds=[
            {"goal_id": doomed.id, "title": "X", "due_date": day(1)},
            {"goal_title": "doomed", "title": "Y", "due_date": day(2)},
            {"goal_id": goal.id, "title": "Z", "due_date": day(3)},
        ],
    )
    session.delete(fresh(session, doomed))
    session.commit()

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (1, 2)
    titles = [t.title for t in session.exec(select(LearningTask))]
    assert titles == ["Z"]


def test_undo_keeps_an_added_goal_that_got_a_resource(session, user, goal):
    change_set = cs.propose(session, user, new_goals=["Toolbox"])
    cs.apply(session, user, change_set.id)
    toolbox = session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).one()
    session.add(LearningResource(user_id=user.id, goal_id=toolbox.id, name="Zapier course"))
    session.commit()

    result = cs.undo(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 1)
    assert fresh(session, toolbox) is not None
    assert count(session, LearningResource) == 1


def test_undo_keeps_an_added_goal_with_study_time(session, user, goal):
    change_set = cs.propose(
        session,
        user,
        new_goals=["Toolbox"],
        adds=[{"goal_title": "Toolbox", "title": "Zapier", "due_date": day(1)}],
    )
    cs.apply(session, user, change_set.id)
    toolbox = session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).one()
    m.log_study_time(session, user, toolbox.id, 30)

    result = cs.undo(session, user, change_set.id)

    # The untouched task goes, the goal with logged time stays.
    assert (result.done, result.skipped) == (1, 1)
    assert fresh(session, toolbox) is not None
    assert session.exec(select(LearningTask).where(LearningTask.title == "Zapier")).first() is None
    assert count(session, StudyLog) == 1


def test_undo_keeps_an_added_goal_the_user_edited(session, user, goal):
    change_set = cs.propose(session, user, new_goals=["Toolbox"])
    cs.apply(session, user, change_set.id)
    toolbox = session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).one()
    toolbox.description = "My notes on automation tools"
    toolbox.target_date = clock.today() + dt.timedelta(days=60)
    session.add(toolbox)
    session.commit()

    result = cs.undo(session, user, change_set.id)

    # "Removed only if unchanged" (D-193): the user's description must not be lost.
    assert result.skipped == 1
    assert fresh(session, toolbox) is not None


def test_undo_keeps_an_added_task_the_user_moved_to_another_goal(session, user, goal):
    german = add_goal(session, user, "German")
    change_set = cs.propose(
        session, user, adds=[{"goal_id": goal.id, "title": "New", "due_date": day(2)}]
    )
    cs.apply(session, user, change_set.id)
    added = session.exec(select(LearningTask).where(LearningTask.title == "New")).one()
    added.goal_id = german.id
    session.add(added)
    session.commit()

    result = cs.undo(session, user, change_set.id)

    # Moving it to another goal is a change by the user: "removed only if untouched".
    assert result.skipped == 1
    assert fresh(session, added) is not None


def test_undo_keeps_an_existing_goal_used_by_adds(session, user, goal):
    change_set = cs.propose(
        session,
        user,
        adds=[{"goal_id": goal.id, "title": f"N{n}", "due_date": day(n)} for n in range(1, 4)],
    )
    cs.apply(session, user, change_set.id)

    result = cs.undo(session, user, change_set.id)

    assert result.done == 3
    assert fresh(session, goal) is not None and count(session, LearningTask) == 0


# --- Bad values ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["garbage", "2026-02-30", "12/01/2026", "2026-1-1", 12345, "x" * 10_000]
)
@pytest.mark.parametrize("field", ["from_date", "to_date"])
def test_shift_with_garbage_dates_is_refused(session, user, goal, field, bad):
    add_task(session, goal, "A", 1)
    with pytest.raises(ChangeSetError, match="use a date like") as error:
        cs.propose(session, user, shift={"days": 1, field: bad})
    assert len(str(error.value)) < 200  # the garbage is not echoed back
    assert count(session, PendingChangeSet) == 0


def test_shift_with_garbage_dates_through_the_tool(session, user, goal):
    add_task(session, goal, "A", 1)
    with pytest.raises(m.Failed, match="use a date like"):
        m.propose_learning_changes(session, user, shift=m.Shift(days=1, from_date="soon"))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"new_goals": ["x" * 201]}, "longer than 200"),
        ({"adds": [{"goal_id": "G", "title": "x" * 201, "due_date": day(1)}]}, "longer"),
        (
            {
                "adds": [
                    {
                        "goal_id": "G",
                        "title": "A",
                        "due_date": day(1),
                        "url": "https://" + "a" * 500,
                    }
                ]
            },
            "at most 500",
        ),
        ({"updates": [{"task_id": "T", "title": "x" * 10_000}]}, "longer"),
        ({"updates": [{"task_id": "T", "url": "https://x.dev/" + "a" * 600}]}, "at most 500"),
        ({"updates": [{"task_id": "T", "url": 123}]}, "link must be text"),
        ({"updates": [{"task_id": "T", "minutes": True}]}, "minutes"),
        ({"updates": [{"task_id": "T", "minutes": 60.0}]}, "minutes"),
        ({"updates": [{"task_id": "T", "due_date": 20261201}]}, "use a date"),
        ({"completes": [{"task_id": "T", "done": "yes"}]}, "true or false"),
        ({"new_goals": [None]}, "title is empty"),
    ],
)
def test_long_or_wrong_values_are_refused(session, user, goal, kwargs, message):
    task = add_task(session, goal, "A", 1)
    for item in [*kwargs.get("adds", []), *kwargs.get("updates", []), *kwargs.get("completes", [])]:
        if item.get("goal_id") == "G":
            item["goal_id"] = goal.id
        if item.get("task_id") == "T":
            item["task_id"] = task.id
    with pytest.raises(ChangeSetError, match=message):
        cs.propose(session, user, **kwargs)
    assert count(session, PendingChangeSet) == 0
    assert fresh(session, task).title == "A"


def test_title_of_exactly_200_characters_is_accepted(session, user, goal):
    change_set = cs.propose(
        session, user, adds=[{"goal_id": goal.id, "title": "é" * 200, "due_date": day(1)}]
    )
    cs.apply(session, user, change_set.id)
    assert session.exec(select(LearningTask)).one().title == "é" * 200


def test_long_text_filter_in_list_tasks_is_cut_not_refused(session, user, goal):
    add_task(session, goal, "A", 1)
    assert m.list_tasks(session, user, text="a" * 10_000)["total"] == 0


@pytest.mark.parametrize(
    "bad", [{"title": "x" * 201}, {"url": "https://x.dev/" + "a" * 600}, {"minutes": 601}]
)
def test_single_record_tools_refuse_long_values(session, user, goal, bad):
    task = add_task(session, goal, "A", 1)
    with pytest.raises(m.Failed):
        m.update_task(session, user, task.id, **bad)
    args = {"title": "N", "due_date": day(1), "minutes": 60, "url": None} | bad
    with pytest.raises(m.Failed):
        m.add_task(session, user, goal.id, **args)
    assert count(session, LearningTask) == 1 and fresh(session, task).title == "A"


def test_unicode_titles_round_trip(session, user, goal):
    title = "Lernen 🚀 日本語 עברית"
    change_set = cs.propose(
        session,
        user,
        new_goals=["Straße"],
        adds=[{"goal_title": "STRASSE", "title": title, "due_date": day(1)}],
    )
    cs.apply(session, user, change_set.id)

    added = session.exec(select(LearningTask).where(LearningTask.title == title)).one()
    straße = session.exec(select(LearningGoal).where(LearningGoal.title == "Straße")).one()
    assert added.goal_id == straße.id
    assert m.list_tasks(session, user, text="日本語")["total"] == 1


@pytest.mark.parametrize(
    "names", [["Toolbox", "Toolbox"], ["Toolbox", "toolbox"], ["Toolbox", " TOOLBOX "]]
)
def test_duplicate_new_goals_in_one_request_are_refused(session, user, goal, names):
    with pytest.raises(ChangeSetError, match="already have"):
        cs.propose(session, user, new_goals=names)
    assert count(session, PendingChangeSet) == 0


@pytest.mark.parametrize("ref", ["toolbox", "TOOLBOX", "  Toolbox  ", "ToolBox"])
def test_adds_find_a_new_goal_by_any_case(session, user, goal, ref):
    change_set = cs.propose(
        session,
        user,
        new_goals=["Toolbox"],
        adds=[{"goal_title": ref, "title": "Zapier", "due_date": day(1)}],
    )
    result = cs.apply(session, user, change_set.id)

    assert result.skipped == 0
    toolbox = session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).one()
    assert session.exec(select(LearningTask)).one().goal_id == toolbox.id
    assert count(session, LearningGoal) == 2  # no second "toolbox" goal


# --- Limits ----------------------------------------------------------------------------------


def test_max_goals_at_propose(session, user, goal, monkeypatch):
    monkeypatch.setattr(learning, "MAX_GOALS", 2)
    with pytest.raises(ChangeSetError, match="limit of 2 goals"):
        cs.propose(session, user, new_goals=["A", "B"])
    assert cs.propose(session, user, new_goals=["A"])


def test_max_goals_at_apply(session, user, goal, monkeypatch):
    monkeypatch.setattr(learning, "MAX_GOALS", 3)
    change_set = cs.propose(session, user, new_goals=["A", "B"])
    add_goal(session, user, "Made in the app meanwhile")

    # 2 goals now + 2 new = 4 > 3: apply must check the limit again, like it does for
    # tasks, and change nothing.
    with pytest.raises(ChangeSetError, match="limit of 3 goals"):
        cs.apply(session, user, change_set.id)
    assert count(session, LearningGoal) == 2


def test_max_tasks_at_apply(session, user, goal, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 3)
    change_set = cs.propose(
        session,
        user,
        adds=[{"goal_id": goal.id, "title": f"N{n}", "due_date": day(1)} for n in (1, 2)],
    )
    add_task(session, goal, "Meanwhile", 1)
    add_task(session, goal, "Meanwhile 2", 1)

    with pytest.raises(ChangeSetError, match="limit of 3 tasks"):
        cs.apply(session, user, change_set.id)
    assert count(session, LearningTask) == 2
    assert fresh(session, change_set).status == "pending"


def test_max_changes_counts_shift_and_explicit_changes(session, user, goal, monkeypatch):
    monkeypatch.setattr(cs, "MAX_CHANGES", 2)
    add_task(session, goal, "A", 1)
    add_task(session, goal, "B", 1)
    with pytest.raises(ChangeSetError, match="Too many changes"):
        cs.propose(session, user, shift={"days": 1}, new_goals=["X"])


def test_max_pending_counts_only_the_users_own_previews(session, user, goal, other):
    their_goal = add_goal(session, other, "Theirs")
    t = add_task(session, their_goal, "T", 1)
    for n in range(cs.MAX_PENDING):
        cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(2 + n)}])
    a = add_task(session, goal, "A", 1)

    assert cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(5)}])


def test_max_pending_ignores_applied_and_discarded(session, user, goal):
    a = add_task(session, goal, "A", 1)
    for n in range(cs.MAX_PENDING):
        change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(30 + n)}])
        cs.discard(session, user, change_set.id)
    applied = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    cs.apply(session, user, applied.id)

    assert cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(3)}])


def test_a_limit_error_saves_no_preview(session, user, goal, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 0)
    with pytest.raises(ChangeSetError, match="limit"):
        cs.propose(session, user, adds=[{"goal_id": goal.id, "title": "X", "due_date": day(1)}])
    assert count(session, PendingChangeSet) == 0


# --- Clean-up, export, account deletion ------------------------------------------------------


def test_clean_up_never_touches_other_users_sets(session, user, goal, other):
    their_goal = add_goal(session, other, "Theirs")
    t = add_task(session, their_goal, "T", 1)
    old_pending = cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(2)}])
    old_applied = cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(3)}])
    cs.apply(session, other, old_applied.id)
    for change_set, age in (
        (old_pending, dt.timedelta(hours=2)),
        (old_applied, dt.timedelta(days=31)),
    ):
        change_set = fresh(session, change_set)
        change_set.created_at = utcnow() - age
        session.add(change_set)
    session.commit()
    a = add_task(session, goal, "A", 1)

    cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(4)}])

    assert count(session, PendingChangeSet, other) == 2


def test_clean_up_keeps_recent_applied_sets_for_undo(session, user, goal):
    a = add_task(session, goal, "A", 1)
    applied = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    cs.apply(session, user, applied.id)
    applied = fresh(session, applied)
    applied.created_at = utcnow() - dt.timedelta(hours=3)  # older than a preview may be
    session.add(applied)
    session.commit()

    cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(6)}])

    assert cs.undo(session, user, applied.id).done == 1


def test_export_has_only_the_users_own_change_sets(engine, session, user, goal, other):
    a = add_task(session, goal, "A", 1)
    mine = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    their_goal = add_goal(session, other, "Theirs")
    t = add_task(session, their_goal, "Their secret task", 1)
    cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(2)}])

    data = json.loads("".join(export_service.stream(engine, user.id)))

    assert [row["id"] for row in data["pending_change_sets"]] == [mine.id]
    assert data["pending_change_sets"][0]["changes"][0]["target_id"] == a.id
    assert "Their secret task" not in json.dumps(data)


def test_account_deletion_removes_the_users_change_sets(session, user, goal, other):
    a = add_task(session, goal, "A", 1)
    cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    applied = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(3)}])
    cs.apply(session, user, applied.id)
    their_goal = add_goal(session, other, "Theirs")
    t = add_task(session, their_goal, "T", 1)
    cs.propose(session, other, moves=[{"task_id": t.id, "due_date": day(2)}])
    user_id = user.id

    account_service.delete_account(session, user)

    session.expire_all()
    left = session.exec(select(PendingChangeSet)).all()
    assert [s.user_id for s in left] == [other.id]
    assert user_id not in {s.user_id for s in left}


# --- Nothing is deleted ----------------------------------------------------------------------


def test_no_tool_deletes_a_task_or_goal(session, user, goal):
    tasks = [add_task(session, goal, f"T{n}", n) for n in range(4)]
    german = add_goal(session, user, "German")

    m.get_learning_plan(session, user)
    m.list_tasks(session, user, status="all")
    m.create_learning_goal(session, user, "Toolbox")
    m.add_task(session, user, german.id, "De", day(1))
    m.update_task(session, user, tasks[0].id, title="Renamed", url="")
    m.set_task_done(session, user, tasks[1].id)
    m.log_study_time(session, user, german.id, 20)
    preview = m.propose_learning_changes(
        session,
        user,
        shift=m.Shift(days=1),
        completes=[m.Complete(task_id=tasks[2].id)],
    )
    m.apply_changes(session, user, preview["change_set_id"])
    m.undo_changes(session, user, preview["change_set_id"])
    dropped = m.propose_learning_changes(session, user, shift=m.Shift(days=2))
    m.discard_changes(session, user, dropped["change_set_id"])

    # 4 + 1 tasks, 2 + 1 goals: everything made is still there.
    assert count(session, LearningTask) == 5
    assert count(session, LearningGoal) == 3
    assert all(fresh(session, t) is not None for t in tasks)


def test_undo_never_deletes_rows_it_did_not_add(session, user, goal):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 2)
    c = add_task(session, goal, "C", 0)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(5)}],
        updates=[{"task_id": b.id, "title": "B2"}],
        completes=[{"task_id": c.id}],
    )
    cs.apply(session, user, change_set.id)

    cs.undo(session, user, change_set.id)

    assert count(session, LearningTask) == 3 and count(session, LearningGoal) == 1


def test_no_tool_takes_a_delete_argument(engine, user):
    from mcp import Client

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(main())

    assert not [t.name for t in tools if "delete" in t.name or "remove" in t.name]
    for tool in tools:
        assert not tool.annotations.destructive_hint or tool.name in (
            "apply_changes",
            "undo_changes",
            "schedule_interview",  # may lock documents (D-195)
            "update_application_status",
        )


# --- Errors through the real server ----------------------------------------------------------


def test_server_errors_for_huge_ids_are_plain_messages(engine, session, user, other, theirs):
    from mcp import Client

    calls = [
        ("apply_changes", {"change_set_id": 2**63}),
        ("set_task_done", {"task_id": 2**63}),
        ("propose_learning_changes", {"moves": [{"task_id": 2**63, "due_date": day(2)}]}),
    ]

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return [await client.call_tool(name, args) for name, args in calls]

    for result in asyncio.run(main()):
        text = result.content[0].text
        assert result.is_error
        assert "not found" in text or "Unknown change set" in text
        assert "secret" not in text and "SELECT" not in text


def test_server_error_for_another_users_task_leaks_nothing(engine, session, user, other, theirs):
    from mcp import Client

    task = their_task(session, other)

    async def main():
        async with Client(m.build_server(engine, user.id)) as client:
            return await client.call_tool("update_task", {"task_id": task.id, "title": "Mine"})

    result = asyncio.run(main())

    assert result.is_error and f"Task {task.id} not found." in result.content[0].text
    assert "secret" not in result.content[0].text
    assert fresh(session, task).title == "Their secret task"
