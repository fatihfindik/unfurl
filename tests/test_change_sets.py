"""Preview, apply and undo of Learning changes (D-193, #107)."""

import datetime as dt

import pytest
from sqlmodel import select

from app.models import LearningGoal, LearningTask, StudyLog
from app.models.base import utcnow
from app.models.change_set import PendingChangeSet
from app.services import change_set_service as cs
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user
from app.services.change_set_service import ChangeSetError


def today() -> dt.date:
    return clock.today()


def day(offset: int) -> str:
    return (today() + dt.timedelta(days=offset)).isoformat()


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
        due_date=today() + dt.timedelta(days=offset),
        estimated_minutes=fields.pop("minutes", 60),
        **fields,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def fresh(session, row):
    session.expire_all()
    return session.get(type(row), row.id)


@pytest.fixture
def goal(session, user):
    return add_goal(session, user)


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- Propose -----------------------------------------------------------------------------


def test_propose_changes_nothing_and_saves_a_preview(session, user, goal):
    task = add_task(session, goal, "A", 1)

    change_set = cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(5)}])

    assert fresh(session, task).due_date == today() + dt.timedelta(days=1)
    assert change_set.status == "pending" and change_set.source == "mcp"
    assert change_set.changes[0]["before"] == {"due_date": day(1)}
    assert "1 × move" in change_set.summary


def test_shift_moves_open_tasks_only_and_lists_skipped(session, user, goal):
    open_task = add_task(session, goal, "Open", 2)
    done_task = add_task(session, goal, "Done", 2, done=True, done_at=utcnow())
    edge = add_task(session, goal, "Edge", -25)  # -25 - 10 = -35 days: outside the rule

    change_set = cs.propose(session, user, shift={"days": -10})

    moved = {c["target_id"] for c in change_set.changes}
    assert moved == {open_task.id}
    assert done_task.id not in moved and edge.id not in moved
    assert "Skipped (1)" in change_set.summary and "Edge" in change_set.summary


def test_shift_by_goal_and_date_range(session, user, goal):
    other_goal = add_goal(session, user, "German")
    a = add_task(session, goal, "A", 1)
    add_task(session, goal, "B", 20)
    add_task(session, other_goal, "C", 1)

    change_set = cs.propose(
        session, user, shift={"days": 3, "goal_id": goal.id, "to_date": day(10)}
    )

    assert [c["target_id"] for c in change_set.changes] == [a.id]


@pytest.mark.parametrize("days", [0, -31, 731, "3", None, 2.5])
def test_bad_shift_days(session, user, goal, days):
    add_task(session, goal, "A", 1)
    with pytest.raises(ChangeSetError, match="days must be"):
        cs.propose(session, user, shift={"days": days})


def test_another_users_task_or_goal_is_not_found(session, user, goal, other):
    theirs = add_goal(session, other, "Theirs")
    their_task = add_task(session, theirs, "Secret", 1)

    with pytest.raises(ChangeSetError, match="not found"):
        cs.propose(session, user, moves=[{"task_id": their_task.id, "due_date": day(2)}])
    with pytest.raises(ChangeSetError, match="not found"):
        cs.propose(session, user, shift={"days": 1, "goal_id": theirs.id})
    with pytest.raises(ChangeSetError, match="not found"):
        cs.propose(
            session,
            user,
            adds=[{"goal_id": theirs.id, "title": "X", "due_date": day(1), "minutes": 60}],
        )
    assert session.exec(select(PendingChangeSet)).all() == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"adds": [{"goal_title": "Python", "title": " ", "due_date": "D1"}]}, "title is empty"),
        ({"adds": [{"goal_title": "Python", "title": "x" * 201, "due_date": "D1"}]}, "longer"),
        ({"adds": [{"goal_title": "Python", "title": "A", "due_date": "D-40"}]}, "days ago"),
        (
            {"adds": [{"goal_title": "Python", "title": "A", "due_date": "D1", "minutes": 4}]},
            "minutes",
        ),
        (
            {
                "adds": [
                    {"goal_title": "Python", "title": "A", "due_date": "D1", "url": "javascript:x"}
                ]
            },
            "https",
        ),
        ({"adds": [{"goal_title": "Nope", "title": "A", "due_date": "D1"}]}, "no goal"),
        ({"adds": [{"title": "A", "due_date": "D1"}]}, "goal_id or goal_title"),
        ({"new_goals": ["python"]}, "already have"),
    ],
)
def test_bad_values_are_refused(session, user, goal, kwargs, message):
    for item in kwargs.get("adds", []):
        offset = int(item["due_date"][1:])
        item["due_date"] = day(offset)
    with pytest.raises(ChangeSetError, match=message):
        cs.propose(session, user, **kwargs)


def test_a_done_task_is_not_moved_and_only_its_title_changes(session, user, goal):
    task = add_task(session, goal, "Done", 0, done=True, done_at=utcnow())

    with pytest.raises(ChangeSetError, match="keep their date"):
        cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(3)}])
    with pytest.raises(ChangeSetError, match="only its title"):
        cs.propose(session, user, updates=[{"task_id": task.id, "minutes": 30}])
    change_set = cs.propose(session, user, updates=[{"task_id": task.id, "title": "Renamed"}])
    assert change_set.changes[0]["after"] == {"title": "Renamed"}


def test_the_same_task_twice_is_refused(session, user, goal):
    task = add_task(session, goal, "A", 1)
    with pytest.raises(ChangeSetError, match="more than once"):
        cs.propose(
            session,
            user,
            moves=[{"task_id": task.id, "due_date": day(2)}],
            updates=[{"task_id": task.id, "title": "B"}],
        )


def test_nothing_to_change(session, user, goal):
    task = add_task(session, goal, "A", 1)
    with pytest.raises(ChangeSetError, match="Nothing would change"):
        cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(1)}])


def test_limits(session, user, goal, monkeypatch):
    monkeypatch.setattr(cs, "MAX_CHANGES", 2)
    tasks = [add_task(session, goal, f"T{n}", 1) for n in range(3)]
    with pytest.raises(ChangeSetError, match="Too many changes"):
        cs.propose(session, user, shift={"days": 1})
    monkeypatch.setattr(cs, "MAX_CHANGES", 1000)
    monkeypatch.setattr(learning, "MAX_TASKS", 4)
    with pytest.raises(ChangeSetError, match="limit of 4 tasks"):
        cs.propose(
            session,
            user,
            adds=[{"goal_id": goal.id, "title": f"N{n}", "due_date": day(1)} for n in range(2)],
        )
    assert len(tasks) == 3


def test_at_most_ten_waiting_previews(session, user, goal):
    task = add_task(session, goal, "A", 1)
    for n in range(cs.MAX_PENDING):
        cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(2 + n)}])
    with pytest.raises(ChangeSetError, match="Too many previews"):
        cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(30)}])


def test_old_sets_are_cleaned_up(session, user, goal):
    task = add_task(session, goal, "A", 1)
    old = cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(2)}])
    old.created_at = utcnow() - dt.timedelta(hours=2)
    session.add(old)
    session.commit()

    new = cs.propose(session, user, moves=[{"task_id": task.id, "due_date": day(3)}])

    session.expire_all()
    rows = session.exec(select(PendingChangeSet)).all()
    assert len(rows) == 1  # the expired preview is gone (SQLite may reuse its id)
    assert rows[0].changes[0]["after"] == {"due_date": day(3)} and rows[0].id == new.id


# --- Apply ---------------------------------------------------------------------------------


def test_apply_makes_every_change(session, user, goal):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 2)
    c = add_task(session, goal, "C", 0)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(4)}],
        updates=[{"task_id": b.id, "title": "B2", "minutes": 90, "url": "https://x.dev"}],
        completes=[{"task_id": c.id}],
        new_goals=["Toolbox"],
        adds=[{"goal_title": "Toolbox", "title": "Zapier", "due_date": day(5), "minutes": 120}],
    )

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (5, 0)
    assert fresh(session, a).due_date.isoformat() == day(4)
    b = fresh(session, b)
    assert (b.title, b.estimated_minutes, b.url) == ("B2", 90, "https://x.dev")
    assert fresh(session, c).done is True
    assert len(session.exec(select(StudyLog)).all()) == 1  # completing logs time (D-092)
    toolbox = session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).one()
    added = session.exec(select(LearningTask).where(LearningTask.goal_id == toolbox.id)).one()
    assert (added.title, added.estimated_minutes) == ("Zapier", 120)
    assert result.change_set.status == "applied" and result.change_set.applied_at


def test_apply_skips_what_changed_in_between(session, user, goal):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 1)
    change_set = cs.propose(session, user, shift={"days": 2})
    learning.reschedule(session, user, a, today() + dt.timedelta(days=7))  # the user moved it

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (1, 1)
    assert fresh(session, a).due_date.isoformat() == day(7)
    assert fresh(session, b).due_date.isoformat() == day(3)


def test_apply_twice_or_after_an_hour_is_refused(session, user, goal):
    a = add_task(session, goal, "A", 1)
    first = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    cs.apply(session, user, first.id)
    with pytest.raises(ChangeSetError, match="applied, not waiting"):
        cs.apply(session, user, first.id)

    late = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(3)}])
    late.created_at = utcnow() - dt.timedelta(minutes=61)
    session.add(late)
    session.commit()
    with pytest.raises(ChangeSetError, match="older than an hour"):
        cs.apply(session, user, late.id)


def test_another_users_set_is_unknown(session, user, goal, other):
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])

    for action in (cs.apply, cs.undo, cs.discard):
        with pytest.raises(ChangeSetError, match="Unknown change set"):
            action(session, other, change_set.id)
    with pytest.raises(ChangeSetError, match="Unknown change set"):
        cs.apply(session, user, "1")


def test_a_failure_halfway_writes_nothing(session, user, goal, monkeypatch):
    a = add_task(session, goal, "A", 1)
    b = add_task(session, goal, "B", 0)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(4)}],
        completes=[{"task_id": b.id}],
    )

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(learning, "apply_done", broken)
    with pytest.raises(RuntimeError):
        cs.apply(session, user, change_set.id)

    assert fresh(session, a).due_date.isoformat() == day(1)
    assert fresh(session, change_set).status == "pending"


def test_apply_rechecks_the_date_rule(session, user, goal, monkeypatch):
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(-29)}])
    # Two days later, -29 is outside the 30-day rule.
    later = today() + dt.timedelta(days=2)
    monkeypatch.setattr(clock, "today", lambda: later)
    monkeypatch.setattr("app.schemas.learning.clock.today", lambda: later)

    result = cs.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 1)


# --- Undo and discard ------------------------------------------------------------------------


def test_undo_restores_everything(session, user, goal):
    a = add_task(session, goal, "A", 1)
    c = add_task(session, goal, "C", 0)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(4)}],
        completes=[{"task_id": c.id}],
        new_goals=["Toolbox"],
        adds=[{"goal_title": "Toolbox", "title": "Zapier", "due_date": day(5)}],
    )
    cs.apply(session, user, change_set.id)

    result = cs.undo(session, user, change_set.id)

    assert (result.done, result.skipped) == (4, 0)
    assert fresh(session, a).due_date.isoformat() == day(1)
    assert fresh(session, c).done is False
    assert session.exec(select(StudyLog)).all() == []
    assert session.exec(select(LearningGoal).where(LearningGoal.title == "Toolbox")).first() is None
    assert session.exec(select(LearningTask).where(LearningTask.title == "Zapier")).first() is None
    assert result.change_set.status == "undone"


def test_undo_leaves_what_the_user_changed(session, user, goal):
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(
        session,
        user,
        moves=[{"task_id": a.id, "due_date": day(4)}],
        adds=[{"goal_id": goal.id, "title": "New", "due_date": day(5)}],
    )
    cs.apply(session, user, change_set.id)
    learning.reschedule(session, user, fresh(session, a), today() + dt.timedelta(days=9))
    added = session.exec(select(LearningTask).where(LearningTask.title == "New")).one()
    learning.set_done(session, user, added, True)  # the user worked on the new task

    result = cs.undo(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 2)
    assert fresh(session, a).due_date.isoformat() == day(9)
    assert session.exec(select(LearningTask).where(LearningTask.title == "New")).first()


def test_undo_only_within_a_day_and_only_once(session, user, goal):
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])
    cs.apply(session, user, change_set.id)
    cs.undo(session, user, change_set.id)
    with pytest.raises(ChangeSetError, match="only applied ones"):
        cs.undo(session, user, change_set.id)

    second = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(3)}])
    cs.apply(session, user, second.id)
    second = fresh(session, second)
    second.applied_at = utcnow() - dt.timedelta(hours=25)
    session.add(second)
    session.commit()
    with pytest.raises(ChangeSetError, match="24 hours"):
        cs.undo(session, user, second.id)


def test_discard(session, user, goal):
    a = add_task(session, goal, "A", 1)
    change_set = cs.propose(session, user, moves=[{"task_id": a.id, "due_date": day(2)}])

    cs.discard(session, user, change_set.id)

    assert fresh(session, change_set).status == "discarded"
    with pytest.raises(ChangeSetError, match="discarded"):
        cs.apply(session, user, change_set.id)
    assert fresh(session, a).due_date.isoformat() == day(1)
