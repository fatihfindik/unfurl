"""Learning rules besides the streak (SPEC §5.10, §6.10, D-092, D-093, D-094, D-096)."""

import datetime as dt

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


def day(offset: int) -> dt.date:
    return WED + dt.timedelta(days=offset)


def new_goal(session, user, **fields) -> LearningGoal:
    fields.setdefault("title", "Python")
    fields.setdefault("target_date", None)
    fields.setdefault("weekly_hours", None)
    fields.setdefault("resource", None)
    fields.setdefault("plan_weekly", False)
    return learning.create_goal(session, user, **fields)


def task(goal, due: dt.date, title="Task", done=False, done_at=None) -> LearningTask:
    return LearningTask(
        user_id=goal.user_id, goal_id=goal.id, title=title, due_date=due, done=done,
        done_at=done_at,
    )  # fmt: skip


# --- Creating goals and tasks ---------------------------------------------------------------


def test_create_goal_without_a_resource(session, user):
    goal = new_goal(session, user, title="German C1", weekly_hours=3.0)

    assert (goal.title, goal.weekly_hours_target, goal.source) == ("German C1", 3.0, "manual")
    assert learning.resources_by_goal(session, user) == {}
    assert learning.tasks(session, user) == []


def test_create_goal_with_one_task_per_week(session, user):
    resource = {"name": "CS50P", "url": "https://cs50.harvard.edu/python/", "units_total": 3}
    goal = new_goal(session, user, weekly_hours=1.5, resource=resource, plan_weekly=True)

    tasks = learning.tasks(session, user)
    assert [(t.title, t.due_date, t.estimated_minutes) for t in tasks] == [
        ("Week 1", WED, 90),
        ("Week 2", day(7), 90),
        ("Week 3", day(14), 90),
    ]
    row = learning.resources_by_goal(session, user)[goal.id]
    assert all(t.resource_id == row.id and t.url == row.url for t in tasks)


def test_one_task_per_week_uses_the_unit_label_and_an_hour_by_default(session, user):
    resource = {"name": "Duolingo", "unit_label": "Lesson", "units_total": 1}
    new_goal(session, user, resource=resource, plan_weekly=True)

    [only] = learning.tasks(session, user)
    assert (only.title, only.estimated_minutes) == ("Lesson 1", 60)


def test_one_task_per_week_is_capped(session, user):
    resource = {"name": "Course", "units_total": 80}
    new_goal(session, user, weekly_hours=40.0, resource=resource, plan_weekly=True)

    tasks = learning.tasks(session, user)
    assert len(tasks) == learning.MAX_TASKS_PER_PLAN
    assert tasks[0].estimated_minutes == learning.MAX_TASK_MINUTES


def test_no_tasks_without_the_box_or_a_total(session, user):
    new_goal(session, user, resource={"name": "Book", "units_total": 4})
    new_goal(session, user, resource={"name": "Videos"}, plan_weekly=True)

    assert learning.tasks(session, user) == []


def test_goal_limit(session, user, monkeypatch):
    monkeypatch.setattr(learning, "MAX_GOALS", 1)
    new_goal(session, user)

    with pytest.raises(learning.TooMany):
        new_goal(session, user)


def test_task_limit_counts_the_planned_tasks(session, user, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 3)
    resource = {"name": "Course", "units_total": 4}

    with pytest.raises(learning.TooMany):
        new_goal(session, user, resource=resource, plan_weekly=True)
    # Nothing was saved, the goal neither.
    assert learning.goals(session, user) == []


def test_add_task_limit(session, user, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 1)
    goal = new_goal(session, user)
    learning.add_task(session, user, goal, title="One", due_date=WED, minutes=30)

    with pytest.raises(learning.TooMany):
        learning.add_task(session, user, goal, title="Two", due_date=WED, minutes=30)


# --- Ownership -----------------------------------------------------------------------------


def test_get_owned_hides_other_users_rows(session, user):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    theirs = new_goal(session, other)
    mine = new_goal(session, user)

    assert learning.get_owned(session, user, LearningGoal, str(mine.id)).id == mine.id
    for bad in (theirs.id, "abc", "0", "-1", None, learning.MAX_ID + 1):
        with pytest.raises(HTTPException) as error:
            learning.get_owned(session, user, LearningGoal, bad)
        assert error.value.status_code == 404


# --- Completing a task (D-092) ------------------------------------------------------------


def test_ticking_a_task_logs_its_time_and_a_unit(session, user):
    goal = new_goal(session, user, resource={"name": "CS50P", "units_total": 2}, plan_weekly=True)
    first = learning.tasks(session, user)[0]

    learning.set_done(session, user, first, True)

    assert first.done and first.done_at is not None
    [entry] = session.exec(select(StudyLog)).all()
    assert (entry.date, entry.minutes, entry.goal_id, entry.task_id) == (
        WED, 60, goal.id, first.id,
    )  # fmt: skip
    assert learning.resources_by_goal(session, user)[goal.id].units_done == 1


def test_unticking_reverses_it(session, user):
    goal = new_goal(session, user, resource={"name": "CS50P", "units_total": 2}, plan_weekly=True)
    first = learning.tasks(session, user)[0]
    learning.set_done(session, user, first, True)

    learning.set_done(session, user, first, False)

    assert not first.done and first.done_at is None
    assert session.exec(select(StudyLog)).all() == []
    assert learning.resources_by_goal(session, user)[goal.id].units_done == 0


def test_ticking_twice_changes_nothing(session, user):
    goal = new_goal(session, user)
    one = learning.add_task(session, user, goal, title="Read", due_date=WED, minutes=20)

    learning.set_done(session, user, one, True)
    learning.set_done(session, user, one, True)

    assert len(session.exec(select(StudyLog)).all()) == 1


def test_units_done_never_passes_the_total(session, user):
    goal = new_goal(session, user, resource={"name": "CS50P", "units_total": 1}, plan_weekly=True)
    resource = learning.resources_by_goal(session, user)[goal.id]
    extra = LearningTask(
        user_id=user.id, goal_id=goal.id, resource_id=resource.id, title="Extra", due_date=WED
    )
    session.add(extra)
    session.commit()

    for one in learning.tasks(session, user):
        learning.set_done(session, user, one, True)

    assert learning.resources_by_goal(session, user)[goal.id].units_done == 1


def test_another_users_resource_is_not_touched(session, user):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    their_goal = new_goal(session, other, resource={"name": "Theirs", "units_total": 5})
    theirs = learning.resources_by_goal(session, other)[their_goal.id]
    goal = new_goal(session, user)
    one = LearningTask(
        user_id=user.id, goal_id=goal.id, resource_id=theirs.id, title="Odd", due_date=WED
    )
    session.add(one)
    session.commit()

    learning.set_done(session, user, one, True)

    session.refresh(theirs)
    assert theirs.units_done == 0


# --- This week and progress ---------------------------------------------------------------


def test_this_week_counts_logged_minutes_and_the_active_targets(session, user):
    new_goal(session, user, weekly_hours=4.0)
    paused = new_goal(session, user, weekly_hours=10.0)
    paused.status = "paused"
    session.add(paused)
    # Monday 16 to Sunday 22 is this week; Sunday 15 and Monday 23 are not.
    for when, minutes in ((day(-2), 30), (day(4), 45), (day(-3), 100), (day(5), 100)):
        session.add(StudyLog(user_id=user.id, date=when, minutes=minutes))
    session.commit()

    week = learning.this_week(session, user)

    assert (week.start, week.end) == (dt.date(2026, 11, 16), dt.date(2026, 11, 22))
    assert (week.minutes, week.target_hours) == (75, 4.0)


def test_this_week_counts_the_tasks_due_this_week(session, user):
    goal = new_goal(session, user)
    session.add_all([
        task(goal, day(-2), done=True, done_at=NOW),
        task(goal, day(1)),
        task(goal, day(7)),
    ])  # fmt: skip
    session.commit()

    week = learning.this_week(session, user)

    assert (week.tasks_done, week.tasks_total) == (1, 2)


def test_an_empty_week(session, user):
    week = learning.this_week(session, user)

    assert (week.minutes, week.target_hours, week.tasks_done, week.tasks_total) == (0, 0, 0, 0)


@pytest.mark.parametrize(
    ("units_total", "units_done", "tasks_done", "expected"),
    [
        (10, 3, None, 30),  # from the resource's units
        (None, 0, [True, False, False, True], 50),  # from the tasks
        (None, 0, [], 0),  # nothing to go on
        (4, 9, None, 100),  # never above 100
    ],
)
def test_progress(units_total, units_done, tasks_done, expected):
    goal = LearningGoal(id=1, user_id=1, title="G")
    resource = LearningResource(
        goal_id=1, user_id=1, name="R", units_total=units_total, units_done=units_done
    )
    goal_tasks = [task(goal, WED, done=done) for done in tasks_done or []]

    assert learning.progress_pct(goal, resource, goal_tasks) == expected


# --- Plan views (D-096) -------------------------------------------------------------------


def plan_tasks() -> list[LearningTask]:
    goal = LearningGoal(id=1, user_id=1, title="G")
    rows = [
        task(goal, day(-3), "overdue"),
        task(goal, day(-2), "caught up today", done=True, done_at=NOW),
        task(goal, day(-1), "done last week", done=True, done_at=NOW - dt.timedelta(days=1)),
        task(goal, day(0), "today"),
        task(goal, day(0), "today done", done=True, done_at=NOW),
        task(goal, day(4), "sunday"),
        task(goal, day(5), "next monday"),
        task(goal, day(6), "in six days"),
        task(goal, day(11), "next sunday"),
        task(goal, day(12), "later"),
        task(goal, day(-40), "done long ago", done=True, done_at=NOW - dt.timedelta(days=40)),
    ]
    return sorted(rows, key=lambda row: row.due_date)


def titles(tasks) -> list[str]:
    return [one.title for one in tasks]


def test_today_view():
    plan = learning.build_plan(plan_tasks(), "today", WED)

    assert [titles(group.tasks) for group in plan.groups] == [["today"]]
    assert titles(plan.done) == ["today done"]
    assert plan.catchup_count == 1


def test_next_seven_days_view():
    plan = learning.build_plan(plan_tasks(), "week", WED)

    assert titles(plan.groups[0].tasks) == ["today", "sunday", "next monday", "in six days"]
    assert titles(plan.done) == ["today done"]


def test_catchup_view():
    plan = learning.build_plan(plan_tasks(), "catchup", WED)

    assert titles(plan.groups[0].tasks) == ["overdue"]
    assert titles(plan.done) == ["caught up today"]


def test_all_tasks_view_groups_by_week():
    plan = learning.build_plan(plan_tasks(), "all", WED)

    assert [(group.label, titles(group.tasks)) for group in plan.groups] == [
        ("Overdue", ["overdue"]),
        ("This week", ["today", "sunday"]),
        ("Next week", ["next monday", "in six days", "next sunday"]),
        ("Later", ["later"]),
    ]
    assert set(titles(plan.done)) == {"caught up today", "done last week", "today done"}


def test_all_tasks_leaves_out_empty_groups():
    goal = LearningGoal(id=1, user_id=1, title="G")

    plan = learning.build_plan([task(goal, day(12), "later")], "all", WED)

    assert [group.label for group in plan.groups] == ["Later"]


def test_an_unknown_view_is_today():
    assert learning.build_plan(plan_tasks(), "nope", WED).view == "today"
