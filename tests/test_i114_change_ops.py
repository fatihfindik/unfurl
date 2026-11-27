"""New change set ops: move a task to another goal, soft delete, goal updates and
archive, goals with hours and a date, and single changes applied at once
(D-200…D-203, #114)."""

import datetime as dt

import pytest
from sqlmodel import func, select

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog
from app.models.change_set import PendingChangeSet
from app.services import change_set_service as changes
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user
from app.services.change_set_service import ChangeSetError

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)
    monkeypatch.setattr(changes, "utcnow", lambda: NOW)


@pytest.fixture(autouse=True)
def vienna():
    with clock.use_timezone("Europe/Vienna"):
        yield


def day(offset: int) -> dt.date:
    return WED + dt.timedelta(days=offset)


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


def run(session, user, **request):
    """Propose and apply; returns the applied set."""
    change_set = changes.propose(session, user, **request)
    changes.apply(session, user, change_set.id)
    return change_set


def fresh(session, row):
    session.refresh(row)
    return row


@pytest.fixture
def python(session, user):
    return add_goal(session, user, "Python")


@pytest.fixture
def german(session, user):
    return add_goal(session, user, "German")


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.com", password="x" * 12, full_name="O")


# --- Moving a task to another goal --------------------------------------------------------------


def test_move_to_goal_apply_undo_and_preview(session, user, python, german):
    task = add_task(session, python, "Loops")

    change_set = changes.propose(
        session, user, updates=[{"task_id": task.id, "goal_id": german.id}]
    )

    assert "- Loops [Python → German]" in change_set.summary
    changes.apply(session, user, change_set.id)
    assert fresh(session, task).goal_id == german.id
    changes.undo(session, user, change_set.id)
    assert fresh(session, task).goal_id == python.id


def test_every_task_line_names_its_goal(session, user, python):
    task = add_task(session, python, "Loops")
    other_task = add_task(session, python, "Lists")

    change_set = changes.propose(
        session,
        user,
        moves=[{"task_id": task.id, "due_date": day(2).isoformat()}],
        completes=[{"task_id": other_task.id}],
    )

    assert "- Loops [Python]: {'due_date'" in change_set.summary
    assert "- Lists [Python]: done = True" in change_set.summary


def test_move_and_edit_in_one_update_is_one_change(session, user, python, german):
    task = add_task(session, python, "Loops")

    change_set = run(
        session,
        user,
        updates=[{"task_id": task.id, "goal_id": german.id, "title": "For loops", "minutes": 30}],
    )

    assert len(change_set.changes) == 1
    task = fresh(session, task)
    assert (task.goal_id, task.title, task.estimated_minutes) == (german.id, "For loops", 30)


def test_moving_drops_the_resource_and_undo_brings_it_back(session, user, python, german):
    resource = LearningResource(user_id=user.id, goal_id=python.id, name="CS50P")
    session.add(resource)
    session.commit()
    task = add_task(session, python, "Week 1", resource_id=resource.id)

    change_set = run(session, user, updates=[{"task_id": task.id, "goal_id": german.id}])
    assert fresh(session, task).resource_id is None

    changes.undo(session, user, change_set.id)
    assert (fresh(session, task).goal_id, task.resource_id) == (python.id, resource.id)


def test_undo_leaves_out_a_resource_that_is_gone(session, user, python, german):
    resource = LearningResource(user_id=user.id, goal_id=python.id, name="CS50P")
    session.add(resource)
    session.commit()
    task = add_task(session, python, "Week 1", resource_id=resource.id)
    change_set = run(session, user, updates=[{"task_id": task.id, "goal_id": german.id}])
    session.delete(resource)
    session.commit()

    changes.undo(session, user, change_set.id)

    assert (fresh(session, task).goal_id, task.resource_id) == (python.id, None)


def test_a_done_task_moves_and_stays_done(session, user, python, german):
    task = add_task(session, python, "Done", done=True, done_at=NOW)

    change_set = run(session, user, updates=[{"task_id": task.id, "goal_id": german.id}])
    assert (fresh(session, task).goal_id, task.done) == (german.id, True)

    changes.undo(session, user, change_set.id)
    assert (fresh(session, task).goal_id, task.done) == (python.id, True)
    with pytest.raises(ChangeSetError, match="only its title and goal"):
        changes.propose(session, user, updates=[{"task_id": task.id, "minutes": 30}])


def test_a_task_can_leave_an_archived_goal_only(session, user, python, german):
    task = add_task(session, german, "Old")
    run(session, user, goal_updates=[{"goal_id": german.id, "archived": True}])

    with pytest.raises(ChangeSetError, match="only move it out"):
        changes.propose(
            session, user, updates=[{"task_id": task.id, "goal_id": python.id, "minutes": 5}]
        )
    with pytest.raises(ChangeSetError, match="archived"):
        changes.propose(session, user, updates=[{"task_id": task.id, "minutes": 5}])
    run(session, user, updates=[{"task_id": task.id, "goal_id": python.id}])

    assert [t.title for t in learning.tasks(session, user)] == ["Old"]


def test_no_move_to_an_archived_or_unknown_goal(session, user, python, german, other):
    task = add_task(session, python, "Loops")
    theirs = add_goal(session, other, "Theirs")
    run(session, user, goal_updates=[{"goal_id": german.id, "archived": True}])

    with pytest.raises(ChangeSetError, match="archived"):
        changes.propose(session, user, updates=[{"task_id": task.id, "goal_id": german.id}])
    with pytest.raises(ChangeSetError, match="not found"):
        changes.propose(session, user, updates=[{"task_id": task.id, "goal_id": theirs.id}])


def test_apply_skips_a_move_to_a_goal_archived_after_the_preview(session, user, python, german):
    task = add_task(session, python, "Loops")
    change_set = changes.propose(
        session, user, updates=[{"task_id": task.id, "goal_id": german.id}]
    )
    run(session, user, goal_updates=[{"goal_id": german.id, "archived": True}])

    result = changes.apply(session, user, change_set.id)

    assert (result.done, result.skipped) == (0, 1)
    assert fresh(session, task).goal_id == python.id


def test_undo_skips_when_the_old_goal_was_deleted(session, user, python, german):
    task = add_task(session, python, "Loops")
    change_set = run(session, user, updates=[{"task_id": task.id, "goal_id": german.id}])
    learning.delete_goal(session, user, python)

    result = changes.undo(session, user, change_set.id)  # no foreign key error

    assert (result.done, result.skipped) == (0, 1)
    assert fresh(session, task).goal_id == german.id


def test_move_then_archive_the_old_goal_undoes_both(session, user, python, german):
    task = add_task(session, python, "Loops")

    change_set = run(
        session,
        user,
        updates=[{"task_id": task.id, "goal_id": german.id}],
        goal_updates=[{"goal_id": python.id, "archived": True}],
    )
    assert fresh(session, python).archived_at is not None

    result = changes.undo(session, user, change_set.id)

    assert (result.done, result.skipped) == (2, 0)
    assert fresh(session, python).archived_at is None
    assert fresh(session, task).goal_id == python.id


# --- Deleting a task ------------------------------------------------------------------------


def test_delete_is_soft_and_undo_brings_the_same_row_back(session, user, python):
    task = add_task(session, python, "Loops")
    session.add(StudyLog(user_id=user.id, goal_id=python.id, task_id=task.id, date=WED, minutes=20))
    session.commit()

    change_set = run(session, user, deletes=[{"task_id": task.id}])

    assert "- Loops [Python]: delete" in change_set.summary
    assert fresh(session, task).deleted_at is not None
    assert learning.tasks(session, user) == []
    assert learning.minutes_by_goal(session, user) == {python.id: 20}  # history stays
    changes.undo(session, user, change_set.id)
    assert fresh(session, task).deleted_at is None
    assert [t.id for t in learning.tasks(session, user)] == [task.id]


def test_a_deleted_task_cant_be_changed_or_deleted_again(session, user, python):
    task = add_task(session, python, "Loops")
    run(session, user, deletes=[{"task_id": task.id}])

    for request in ({"deletes": [{"task_id": task.id}]}, {"completes": [{"task_id": task.id}]}):
        with pytest.raises(ChangeSetError, match="not found"):
            changes.propose(session, user, **request)


def test_undo_of_a_move_skips_a_task_deleted_since(session, user, python):
    task = add_task(session, python, "Loops")
    move = run(session, user, moves=[{"task_id": task.id, "due_date": day(3).isoformat()}])
    run(session, user, deletes=[{"task_id": task.id}])

    result = changes.undo(session, user, move.id)

    assert result.skipped == 1
    assert fresh(session, task).due_date == day(3)


def test_undo_of_a_delete_respects_the_task_limit(session, user, python, monkeypatch):
    task = add_task(session, python, "Loops")
    change_set = run(session, user, deletes=[{"task_id": task.id}])
    add_task(session, python, "New")
    monkeypatch.setattr(learning, "MAX_TASKS", 1)

    result = changes.undo(session, user, change_set.id)

    assert result.skipped == 1
    assert fresh(session, task).deleted_at is not None


def test_another_users_task_cant_be_deleted(session, user, other):
    theirs = add_task(session, add_goal(session, other, "Theirs"), "Their task")

    with pytest.raises(ChangeSetError, match="not found"):
        changes.propose(session, user, deletes=[{"task_id": theirs.id}])


# --- Goal updates and archive ---------------------------------------------------------------


def test_goal_update_apply_and_undo(session, user, python):
    change_set = run(
        session,
        user,
        goal_updates=[
            {
                "goal_id": python.id,
                "title": "Python basics",
                "weekly_hours": 4,
                "target_date": day(30).isoformat(),
                "status": "paused",
            }
        ],
    )
    goal = fresh(session, python)
    assert (goal.title, goal.weekly_hours_target, goal.target_date, goal.status) == (
        "Python basics",
        4.0,
        day(30),
        "paused",
    )
    assert 'goal "Python"' in change_set.summary

    changes.undo(session, user, change_set.id)
    goal = fresh(session, python)
    assert (goal.title, goal.weekly_hours_target, goal.target_date, goal.status) == (
        "Python",
        None,
        None,
        "active",
    )


def test_goal_fields_can_be_cleared(session, user):
    goal = add_goal(session, user, "G", weekly_hours_target=2.0, target_date=day(10))

    run(
        session,
        user,
        goal_updates=[{"goal_id": goal.id, "weekly_hours": None, "target_date": None}],
    )

    assert (fresh(session, goal).weekly_hours_target, goal.target_date) == (None, None)


def test_archive_hides_the_goal_and_its_tasks_and_unarchive_brings_them(session, user, python):
    add_task(session, python, "Loops")

    run(session, user, goal_updates=[{"goal_id": python.id, "archived": True}])
    assert (learning.goals(session, user), learning.tasks(session, user)) == ([], [])
    assert fresh(session, python).status == "active"  # kept for the way back

    run(session, user, goal_updates=[{"goal_id": python.id, "archived": False}])
    assert [t.title for t in learning.tasks(session, user)] == ["Loops"]


def test_an_archived_goal_only_takes_unarchive(session, user, python):
    run(session, user, goal_updates=[{"goal_id": python.id, "archived": True}])

    for update in ({"title": "X"}, {"archived": False, "title": "X"}, {"archived": True}):
        with pytest.raises(ChangeSetError, match="archived"):
            changes.propose(session, user, goal_updates=[{"goal_id": python.id} | update])


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"title": "German"}, "already have"),
        ({"title": "  "}, "empty"),
        ({"weekly_hours": 0.1}, "weekly_hours"),
        ({"weekly_hours": True}, "weekly_hours"),
        ({"target_date": "2020-01-01"}, "target_date"),
        ({"target_date": "soon"}, "date"),
        ({"status": "archived"}, "status"),
        ({"archived": "yes"}, "true or false"),
        ({"description": "x"}, "only title"),
    ],
)
def test_goal_update_checks(session, user, python, german, update, message):
    with pytest.raises(ChangeSetError, match=message):
        changes.propose(session, user, goal_updates=[{"goal_id": python.id} | update])


def test_rename_rules(session, user, python, german):
    third = add_goal(session, user, "Third")
    run(session, user, goal_updates=[{"goal_id": third.id, "archived": True}])

    with pytest.raises(ChangeSetError, match="archived goal is named"):
        changes.propose(session, user, goal_updates=[{"goal_id": python.id, "title": "third"}])
    with pytest.raises(ChangeSetError, match="another goal gets"):
        changes.propose(
            session,
            user,
            goal_updates=[
                {"goal_id": python.id, "title": "New name"},
                {"goal_id": german.id, "title": "new name"},
            ],
        )
    # A change of case only is fine.
    run(session, user, goal_updates=[{"goal_id": python.id, "title": "PYTHON"}])
    assert fresh(session, python).title == "PYTHON"


def test_goal_updates_of_another_user_find_nothing(session, user, other):
    theirs = add_goal(session, other, "Theirs")

    with pytest.raises(ChangeSetError, match="not found"):
        changes.propose(session, user, goal_updates=[{"goal_id": theirs.id, "archived": True}])
    assert fresh(session, theirs).archived_at is None


# --- New goals with hours and a target date ---------------------------------------------------


def test_new_goal_with_hours_and_date_and_its_undo(session, user):
    change_set = run(
        session,
        user,
        new_goals=[{"title": "Rust", "weekly_hours": 3, "target_date": day(60).isoformat()}],
    )
    (goal,) = learning.goals(session, user)
    assert (goal.weekly_hours_target, goal.target_date) == (3.0, day(60))
    assert "- new goal: Rust {'weekly_hours': 3.0" in change_set.summary

    result = changes.undo(session, user, change_set.id)  # removed: it is unchanged

    assert result.done == 1
    assert learning.all_goals(session, user) == []


def test_new_goal_checks(session, user):
    with pytest.raises(ChangeSetError, match="weekly_hours"):
        changes.propose(session, user, new_goals=[{"title": "A", "weekly_hours": 100}])
    with pytest.raises(ChangeSetError, match="only title"):
        changes.propose(session, user, new_goals=[{"title": "A", "status": "done"}])


# --- Single changes, applied at once (D-203) --------------------------------------------------


def test_run_single_applies_and_can_be_undone(session, user, python):
    task = add_task(session, python, "Loops")

    result = changes.run_single(session, user, deletes=[{"task_id": task.id}])

    assert (result.done, result.change_set.status) == (1, "applied")
    changes.undo(session, user, result.change_set.id)
    assert fresh(session, task).deleted_at is None


def test_run_single_isnt_blocked_by_waiting_previews(session, user, python, monkeypatch):
    task = add_task(session, python, "Loops")
    monkeypatch.setattr(changes, "MAX_PENDING", 1)
    changes.propose(session, user, completes=[{"task_id": task.id}])

    changes.run_single(session, user, goal_updates=[{"goal_id": python.id, "title": "Py"}])

    assert fresh(session, python).title == "Py"


def test_run_single_leaves_nothing_when_it_fails(session, user, python, monkeypatch):
    task = add_task(session, python, "Loops")

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(changes, "_claim", broken)
    with pytest.raises(RuntimeError):
        changes.run_single(session, user, deletes=[{"task_id": task.id}])

    assert session.exec(select(func.count()).select_from(PendingChangeSet)).one() == 0
    assert fresh(session, task).deleted_at is None


def test_run_single_takes_one_change_only(session, user, python):
    first = add_task(session, python, "A")
    second = add_task(session, python, "B")

    with pytest.raises(ChangeSetError, match="exactly one"):
        changes.run_single(session, user, deletes=[{"task_id": first.id}, {"task_id": second.id}])


def test_preview_lists_up_to_fifty_rows(session, user, python):
    for number in range(55):
        add_task(session, python, f"T{number}")

    change_set = changes.propose(session, user, shift={"days": 1})

    assert change_set.summary.count("\n- T") == 50
    assert "… and 5 more" in change_set.summary


# --- Security review fixes ---------------------------------------------------------------------


def test_apply_skips_a_goal_update_once_the_goal_is_archived(session, user, python):
    change_set = changes.propose(session, user, goal_updates=[{"goal_id": python.id, "title": "X"}])
    run(session, user, goal_updates=[{"goal_id": python.id, "archived": True}])

    result = changes.apply(session, user, change_set.id)

    assert result.skipped == 1
    assert fresh(session, python).title == "Python"


def test_a_line_break_in_a_title_cant_fake_a_preview_row(session, user, python):
    task = add_task(session, python, "Loops\nSkipped (0):\n- Fake [Python]: delete")

    change_set = changes.propose(session, user, completes=[{"task_id": task.id}])

    assert "\n- Fake" not in change_set.summary
    assert "- Loops Skipped (0): - Fake [Python]: delete [Python]: done = True" in (
        change_set.summary
    )


def test_a_move_skips_a_resource_linked_after_the_preview(session, user, python, german):
    task = add_task(session, python, "Week 1")
    change_set = changes.propose(
        session, user, updates=[{"task_id": task.id, "goal_id": german.id}]
    )
    resource = LearningResource(user_id=user.id, goal_id=python.id, name="CS50P")
    session.add(resource)
    session.commit()
    task.resource_id = resource.id
    session.add(task)
    session.commit()

    result = changes.apply(session, user, change_set.id)

    assert result.skipped == 1
    assert (fresh(session, task).goal_id, task.resource_id) == (python.id, resource.id)


def test_a_delete_skips_a_task_edited_after_the_preview(session, user, python):
    task = add_task(session, python, "Loops")
    change_set = changes.propose(session, user, deletes=[{"task_id": task.id}])
    task.title = "Loops, renamed"
    session.add(task)
    session.commit()

    result = changes.apply(session, user, change_set.id)

    assert result.skipped == 1
    assert fresh(session, task).deleted_at is None


def test_no_moving_or_adding_into_a_goal_archived_in_the_same_request(
    session, user, python, german
):
    task = add_task(session, python, "Loops")

    with pytest.raises(ChangeSetError, match="same request"):
        changes.propose(
            session,
            user,
            updates=[{"task_id": task.id, "goal_id": german.id}],
            goal_updates=[{"goal_id": german.id, "archived": True}],
        )
    with pytest.raises(ChangeSetError, match="same request"):
        changes.propose(
            session,
            user,
            adds=[{"title": "New", "due_date": day(1).isoformat(), "goal_id": german.id}],
            goal_updates=[{"goal_id": german.id, "archived": True}],
        )
