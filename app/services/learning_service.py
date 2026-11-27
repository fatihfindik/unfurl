"""Learning rules (SPEC §5.10, §6.10; plan M10, D-089…D-096).

Routes only call this module. Every query filters by user_id (CLAUDE.md). Dates are the
user's dates (clock, D-071).
"""

import datetime as dt
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from app.models import (
    Application,
    LearningGoal,
    LearningResource,
    LearningTask,
    NotificationPreference,
    StudyLog,
    User,
)
from app.models.base import as_utc
from app.models.learning import DEFAULT_REST_DAYS, WEEKDAYS
from app.services import clock, profile_service, trash_service

MAX_GOALS = 50
MAX_TASKS = 1000
MAX_TASKS_PER_PLAN = 52  # "one task per week" for at most a year (D-093)
MAX_TASK_MINUTES = 600
ACTIVE_MINUTES = 10  # a day with this much logged time counts for the streak (SPEC §5.10)
STREAK_WINDOW = 400  # days looked back for the streak (D-091)
MAX_ID = profile_service.MAX_ID


class TooMany(Exception):
    """A limit of D-094 is reached."""


# --- Rest days and the streak (D-090, D-091) --------------------------------------------


def preferences(session: Session, user: User) -> NotificationPreference:
    """The user's row, created with the defaults the first time it is needed."""
    row = session.exec(
        select(NotificationPreference).where(NotificationPreference.user_id == user.id)
    ).first()
    if row is None:
        row = NotificationPreference(user_id=user.id, rest_days=list(DEFAULT_REST_DAYS))
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            # Another request created it at the same moment (security review M10, L2).
            session.rollback()
            return session.exec(
                select(NotificationPreference).where(NotificationPreference.user_id == user.id)
            ).one()
        session.refresh(row)
    return row


def rest_weekdays(prefs: NotificationPreference) -> set[int]:
    """The rest days as date.weekday() numbers (Monday = 0)."""
    return {WEEKDAYS.index(day) for day in prefs.rest_days if day in WEEKDAYS}


def count_streak(active_days: set[dt.date], rest: set[int], today: dt.date) -> int:
    """How many active days in a row up to today (SPEC §5.10).

    - An active day counts (also on a rest day).
    - A rest day that wasn't active is skipped: it neither counts nor breaks the streak.
    - Today not active yet doesn't break it either: the day isn't over.
    - Any other day without activity ends the streak.
    """
    streak = 0
    for back in range(STREAK_WINDOW):
        day = today - dt.timedelta(days=back)
        if day in active_days:
            streak += 1
        elif day.weekday() in rest or day == today:
            continue
        else:
            break
    return streak


def active_days(session: Session, user: User, today: dt.date) -> set[dt.date]:
    """Days with at least one completed task or ACTIVE_MINUTES of logged time."""
    since = today - dt.timedelta(days=STREAK_WINDOW)
    days = {
        clock.local(done_at).date()
        for done_at in session.exec(
            select(LearningTask.done_at).where(
                LearningTask.user_id == user.id,
                col(LearningTask.done).is_(True),
                col(LearningTask.done_at) >= clock.to_utc(since),
            )
        )
    }
    logged = session.exec(
        select(StudyLog.date, func.sum(StudyLog.minutes))
        .where(StudyLog.user_id == user.id, StudyLog.date >= since)
        .group_by(StudyLog.date)
    )
    days |= {day for day, minutes in logged if (minutes or 0) >= ACTIVE_MINUTES}
    return days


def streak(session: Session, user: User) -> int:
    today = clock.today()
    prefs = preferences(session, user)
    return count_streak(active_days(session, user, today), rest_weekdays(prefs), today)


# --- This week (D-092) --------------------------------------------------------------------


def week_of(day: dt.date) -> tuple[dt.date, dt.date]:
    """Monday and Sunday of the week of `day`."""
    monday = day - dt.timedelta(days=day.weekday())
    return monday, monday + dt.timedelta(days=6)


@dataclass(frozen=True)
class Week:
    start: dt.date
    end: dt.date
    minutes: int
    target_hours: float
    tasks_done: int
    tasks_total: int


def this_week(session: Session, user: User) -> Week:
    """Time logged and tasks of the current week, and the weekly target of the active goals."""
    start, end = week_of(clock.today())
    minutes = session.exec(
        select(func.sum(StudyLog.minutes)).where(
            StudyLog.user_id == user.id, StudyLog.date >= start, StudyLog.date <= end
        )
    ).one()
    target = session.exec(
        select(func.sum(LearningGoal.weekly_hours_target)).where(
            LearningGoal.user_id == user.id, LearningGoal.status == "active", LIVE_GOAL
        )
    ).one()
    tasks = list(
        session.exec(
            live_tasks_query(user)
            .with_only_columns(LearningTask.done)
            .where(LearningTask.due_date >= start, LearningTask.due_date <= end)
        )
    )
    return Week(start, end, minutes or 0, float(target or 0), sum(tasks), len(tasks))


# --- Plan views (SPEC §6.10, D-096, D-187) ---------------------------------------------------

VIEWS = ("today", "week", "all", "catchup", "done")


@dataclass(frozen=True)
class Group:
    label: str  # "" when the view has a single list
    tasks: list[LearningTask]


@dataclass(frozen=True)
class Plan:
    view: str
    groups: list[Group]  # open tasks (the Done tab: its one group of done tasks)
    catchup_count: int  # open tasks from before today, for the "Catch-up (n)" tab
    done_count: int  # all done tasks, for the "Done (n)" tab


def _newest_done_first(task: LearningTask) -> tuple:
    """Sort key: the latest done_at first, the same moment by id; no done_at goes last."""
    stamp = as_utc(task.done_at).timestamp() if task.done_at else float("-inf")
    return (-stamp, -(task.id or 0))


def build_plan(all_tasks: list[LearningTask], view: str, today: dt.date) -> Plan:
    """Split the tasks (sorted by date) for one tab of the plan card.

    - Today: due today. Next 7 days: today … today + 6. Catch-up: due before today
      and not done. All tasks: open tasks grouped Overdue / This week / Next week /
      Later. These tabs show open tasks only: a task ticked off leaves the list, and
      the toast's Undo or the Done tab brings it back (D-187).
    - Done: every completed task, the newest first.
    """
    if view not in VIEWS:
        view = "today"
    open_tasks = [task for task in all_tasks if not task.done]
    done_tasks = [task for task in all_tasks if task.done]
    catchup = [task for task in open_tasks if task.due_date < today]

    if view == "today":
        groups = [Group("", [t for t in open_tasks if t.due_date == today])]
    elif view == "week":
        last = today + dt.timedelta(days=6)
        groups = [Group("", [t for t in open_tasks if today <= t.due_date <= last])]
    elif view == "catchup":
        groups = [Group("", catchup)]
    elif view == "done":
        groups = [Group("", sorted(done_tasks, key=_newest_done_first))]
    else:
        _, sunday = week_of(today)
        next_sunday = sunday + dt.timedelta(days=7)
        groups = [
            Group("Overdue", catchup),
            Group("This week", [t for t in open_tasks if today <= t.due_date <= sunday]),
            Group("Next week", [t for t in open_tasks if sunday < t.due_date <= next_sunday]),
            Group("Later", [t for t in open_tasks if t.due_date > next_sunday]),
        ]
        groups = [group for group in groups if group.tasks]
    return Plan(view, groups, len(catchup), len(done_tasks))


# --- Goals and tasks -------------------------------------------------------------------------


# Live rows (D-200, D-201): a task soft-deleted from the MCP server, a goal that is
# archived and every task of an archived goal are hidden and can't be changed. History
# (done_at for the streak, StudyLog for the hours) is read without these filters.
LIVE_GOAL = col(LearningGoal.archived_at).is_(None)
LIVE_TASK = col(LearningTask.deleted_at).is_(None)


def live_tasks_query(user: User):
    """SELECT of the user's tasks that are shown: not deleted, of a goal not archived."""
    return (
        select(LearningTask)
        .join(LearningGoal, col(LearningGoal.id) == LearningTask.goal_id)
        .where(LearningTask.user_id == user.id, LearningGoal.user_id == user.id)
        .where(LIVE_TASK, LIVE_GOAL)
    )


def get_owned(session: Session, user: User, model, item_id, *, live: bool = True):
    """A row of `model` that belongs to `user`, otherwise 404 (also for bad ids).
    With live=True (the default) an archived goal, a deleted task and a task of an
    archived goal count as missing too: they can't be changed (D-201)."""
    try:
        number = int(item_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404) from None
    if not 0 < number <= MAX_ID:
        raise HTTPException(status_code=404)
    if model is LearningTask and live:
        query = live_tasks_query(user).where(LearningTask.id == number)
    else:
        query = select(model).where(model.id == number, model.user_id == user.id)
        if model is LearningGoal and live:
            query = query.where(LIVE_GOAL)
    row = session.exec(query).first()
    if row is None:
        raise HTTPException(status_code=404)
    return row


def goals(session: Session, user: User) -> list[LearningGoal]:
    """The goals that are shown: not archived."""
    return [goal for goal in all_goals(session, user) if goal.archived_at is None]


def all_goals(session: Session, user: User) -> list[LearningGoal]:
    """Every goal, archived ones too: for the goal limit and unique titles (D-201)."""
    return list(
        session.exec(
            select(LearningGoal)
            .where(LearningGoal.user_id == user.id)
            .order_by(col(LearningGoal.sort_order), col(LearningGoal.id))
        )
    )


def archived_goals(session: Session, user: User) -> list[LearningGoal]:
    return [goal for goal in all_goals(session, user) if goal.archived_at is not None]


def resources_by_goal(session: Session, user: User) -> dict[int, LearningResource]:
    """The first resource of each goal (the page shows one per goal)."""
    rows = session.exec(
        select(LearningResource)
        .where(LearningResource.user_id == user.id)
        .order_by(col(LearningResource.id))
    )
    found: dict[int, LearningResource] = {}
    for row in rows:
        found.setdefault(row.goal_id, row)
    return found


def tasks(session: Session, user: User) -> list[LearningTask]:
    """The live tasks (see live_tasks_query), sorted by date."""
    return list(
        session.exec(
            live_tasks_query(user).order_by(
                col(LearningTask.due_date), col(LearningTask.sort_order), col(LearningTask.id)
            )
        )
    )


def progress_pct(goal: LearningGoal, resource: LearningResource | None, goal_tasks) -> int:
    """From the resource's units if it has a total, else from the goal's tasks."""
    if resource is not None and resource.units_total:
        return min(100, round(100 * resource.units_done / resource.units_total))
    if goal_tasks:
        return round(100 * sum(task.done for task in goal_tasks) / len(goal_tasks))
    return 0


def minutes_by_goal(session: Session, user: User) -> dict[int, int]:
    rows = session.exec(
        select(StudyLog.goal_id, func.sum(StudyLog.minutes))
        .where(StudyLog.user_id == user.id, col(StudyLog.goal_id).is_not(None))
        .group_by(StudyLog.goal_id)
    )
    return {goal_id: minutes or 0 for goal_id, minutes in rows}


def minutes_by_task(session: Session, user: User) -> dict[int, int]:
    """Time logged per task: the "Done (n)" rows show what was really spent (D-117)."""
    rows = session.exec(
        select(StudyLog.task_id, func.sum(StudyLog.minutes))
        .where(StudyLog.user_id == user.id, col(StudyLog.task_id).is_not(None))
        .group_by(StudyLog.task_id)
    )
    return {task_id: minutes or 0 for task_id, minutes in rows}


def count_rows(session: Session, user: User, model) -> int:
    """Rows of `model` that count for the limits of D-094: archived goals and their
    tasks count (the rows are there), soft-deleted tasks don't (D-200)."""
    query = select(func.count()).select_from(model).where(model.user_id == user.id)
    if model is LearningTask:
        query = query.where(LIVE_TASK)
    return session.exec(query).one()


def goal_task_count(session: Session, user: User, goal: LearningGoal) -> int:
    """A goal's tasks the user still sees as theirs (not soft-deleted), for the delete
    confirmation. Deleted ones go with the goal too, but they are already gone for the user."""
    return session.exec(
        select(func.count())
        .select_from(LearningTask)
        .where(LearningTask.user_id == user.id, LearningTask.goal_id == goal.id, LIVE_TASK)
    ).one()


def _check_room(session: Session, user: User, model, limit: int, adding: int = 1) -> None:
    """Raise TooMany if `adding` more rows of `model` would pass the user's limit."""
    if count_rows(session, user, model) + adding > limit:
        raise TooMany


def create_goal(
    session: Session,
    user: User,
    *,
    title: str,
    target_date: dt.date | None,
    weekly_hours: float | None,
    resource: dict | None,
    plan_weekly: bool,
) -> LearningGoal:
    """A new goal, its resource (if given) and, if asked, one task per unit (D-093)."""
    _check_room(session, user, LearningGoal, MAX_GOALS)
    units = (resource or {}).get("units_total") or 0
    planned = min(units, MAX_TASKS_PER_PLAN) if plan_weekly else 0
    if planned:
        _check_room(session, user, LearningTask, MAX_TASKS, adding=planned)
    goal = LearningGoal(
        user_id=user.id,
        title=title,
        target_date=target_date,
        weekly_hours_target=weekly_hours,
        sort_order=len(all_goals(session, user)),
    )
    session.add(goal)
    session.flush()
    row = None
    if resource:
        row = LearningResource(user_id=user.id, goal_id=goal.id, **resource)
        session.add(row)
        session.flush()
    if planned and row is not None:
        # One task per unit, a week apart; each as long as the weekly hours (D-093).
        minutes = min(round((weekly_hours or 1) * 60), MAX_TASK_MINUTES)
        start = clock.today()
        for number in range(1, planned + 1):
            session.add(
                LearningTask(
                    user_id=user.id,
                    goal_id=goal.id,
                    resource_id=row.id,
                    title=f"{row.unit_label} {number}",
                    due_date=start + dt.timedelta(days=7 * (number - 1)),
                    estimated_minutes=minutes,
                    url=row.url,
                    sort_order=number,
                )
            )
    session.commit()
    session.refresh(goal)
    return goal


def add_task(
    session: Session,
    user: User,
    goal: LearningGoal,
    *,
    title: str,
    due_date: dt.date,
    minutes: int,
    url: str | None = None,
) -> LearningTask:
    _check_room(session, user, LearningTask, MAX_TASKS)
    task = LearningTask(
        user_id=user.id,
        goal_id=goal.id,
        title=title,
        due_date=due_date,
        estimated_minutes=minutes,
        url=url,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def set_done(session: Session, user: User, task: LearningTask, done: bool) -> LearningTask:
    """Tick or untick a task (D-092): its time is logged (or the log removed) and the
    resource's units go up (or down)."""
    if task.done == done:
        return task
    apply_done(session, user, task, done)
    session.commit()
    session.refresh(task)
    return task


def apply_done(session: Session, user: User, task: LearningTask, done: bool) -> None:
    """The changes of set_done without the commit, so a set of changes can be written
    in one transaction (D-193). Does nothing if the task is already in that state."""
    if task.done == done:
        return
    resource = None
    if task.resource_id is not None:
        resource = session.exec(
            select(LearningResource).where(
                LearningResource.id == task.resource_id, LearningResource.user_id == user.id
            )
        ).first()
    if done:
        task.done, task.done_at = True, clock.now()
        session.add(
            StudyLog(
                user_id=user.id,
                goal_id=task.goal_id,
                task_id=task.id,
                date=clock.today(),
                minutes=task.estimated_minutes,
            )
        )
        if resource is not None and resource.units_done < (resource.units_total or 0):
            resource.units_done += 1
    else:
        task.done, task.done_at = False, None
        for log in session.exec(
            select(StudyLog).where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
        ):
            session.delete(log)
        if resource is not None and resource.units_done > 0:
            resource.units_done -= 1
    if resource is not None:
        session.add(resource)
    session.add(task)


def log_time(
    session: Session, user: User, goal: LearningGoal, minutes: int, day: dt.date
) -> StudyLog:
    """Study time without a task (SPEC §9.8 log_study_time, D-194): counts for the
    week's hours, the goal's time and the streak like any StudyLog row."""
    log = StudyLog(user_id=user.id, goal_id=goal.id, task_id=None, date=day, minutes=minutes)
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def set_rest_days(session: Session, user: User, days: list[str]) -> NotificationPreference:
    prefs = preferences(session, user)
    prefs.rest_days = [day for day in WEEKDAYS if day in days]
    session.add(prefs)
    session.commit()
    session.refresh(prefs)
    return prefs


# --- Reschedule and delete (D-095) --------------------------------------------------------


def reschedule(session: Session, user: User, task: LearningTask, due_date: dt.date) -> None:
    """A new date for an open task (the route refuses done ones)."""
    task.due_date = due_date
    session.add(task)
    session.commit()


# --- Editing (#39, D-116…D-118) --------------------------------------------------------------


def update_task(
    session: Session, user: User, task: LearningTask, *, title: str, due_date: dt.date, minutes: int
) -> None:
    """Title, date and duration of an open task (the route sends done ones elsewhere)."""
    task.title, task.due_date, task.estimated_minutes = title, due_date, minutes
    session.add(task)
    session.commit()


def update_done_task(
    session: Session, user: User, task: LearningTask, *, title: str, spent_minutes: int
) -> None:
    """Title of a completed task and the time really spent (D-117).

    Completing a task wrote one StudyLog row with the estimate (D-092); it gets the new
    minutes. Older data may have none: then one is written for the day the task was
    done. Extra rows are removed, so the task counts once."""
    task.title = title
    session.add(task)
    logs = list(
        session.exec(
            select(StudyLog)
            .where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
            .order_by(col(StudyLog.id))
        )
    )
    if logs:
        logs[0].minutes = spent_minutes
        session.add(logs[0])
        for extra in logs[1:]:
            session.delete(extra)
    else:
        day = clock.local(task.done_at).date() if task.done_at else clock.today()
        session.add(
            StudyLog(
                user_id=user.id,
                goal_id=task.goal_id,
                task_id=task.id,
                date=day,
                minutes=spent_minutes,
            )
        )
    session.commit()


def update_goal(
    session: Session,
    user: User,
    goal: LearningGoal,
    *,
    title: str,
    target_date: dt.date | None,
    weekly_hours: float | None,
    status: str,
) -> None:
    """A goal's title, target date, weekly hours and status (D-118). Only active goals
    count for the weekly target (D-092, D-119)."""
    goal.title, goal.target_date = title, target_date
    goal.weekly_hours_target, goal.status = weekly_hours, status
    session.add(goal)
    session.commit()


def _log_ids(session: Session, user: User, column, value: int) -> list[int]:
    return list(
        session.exec(select(StudyLog.id).where(StudyLog.user_id == user.id, column == value))
    )


def delete_task(session: Session, user: User, task: LearningTask) -> int:
    """Delete a task, keeping a copy for Undo (D-086). Its study logs stay (SET NULL),
    so the hours and the streak of the past don't change. Returns the copy's id."""
    payload = {
        "task": trash_service.payload_of(task) | {"old_id": task.id},
        "logs": [
            [log_id, task.id] for log_id in _log_ids(session, user, StudyLog.task_id, task.id)
        ],
    }
    copy = trash_service.remember(session, user, "learning_task", payload)
    session.delete(task)
    session.commit()
    return copy.id


def delete_goal(session: Session, user: User, goal: LearningGoal) -> int:
    """Delete a goal with its resources and tasks (the database cascades), keeping one
    copy of all of them for Undo. The logs stay, like for a task."""
    resources = session.exec(
        select(LearningResource).where(
            LearningResource.user_id == user.id, LearningResource.goal_id == goal.id
        )
    ).all()
    goal_tasks = session.exec(
        select(LearningTask).where(LearningTask.user_id == user.id, LearningTask.goal_id == goal.id)
    ).all()
    logs = session.exec(
        select(StudyLog).where(StudyLog.user_id == user.id, StudyLog.goal_id == goal.id)
    ).all()
    payload = {
        "goal": trash_service.payload_of(goal),
        # The old ids, to link tasks to resources and logs to tasks again on Undo.
        "resources": [trash_service.payload_of(r) | {"old_id": r.id} for r in resources],
        "tasks": [trash_service.payload_of(t) | {"old_id": t.id} for t in goal_tasks],
        "logs": [[log.id, log.task_id] for log in logs],
    }
    copy = trash_service.remember(session, user, "learning_goal", payload)
    session.delete(goal)
    session.commit()
    return copy.id


def _relink_logs(session: Session, user: User, links: list, goal_id: int, task_ids: dict) -> None:
    """Point the kept logs back at the restored goal and tasks. `links` holds
    [log id, old task id or None]. Only the user's logs that are still unlinked (no
    task, and no goal or this goal) are touched."""
    for log_id, old_task_id in links:
        log = session.exec(
            select(StudyLog).where(
                StudyLog.id == _copy_id(log_id),
                StudyLog.user_id == user.id,
                col(StudyLog.task_id).is_(None),
            )
        ).first()
        if log is None or log.goal_id not in (None, goal_id):
            continue
        log.goal_id = goal_id
        log.task_id = task_ids.get(old_task_id)
        session.add(log)


def _copy_id(value) -> int:
    """An id read from a copy, checked like a database id (M10 test gaps): anything
    else means the copy is broken and can't come back."""
    if type(value) is not int or not 0 < value <= MAX_ID:
        raise trash_service.CannotRestore
    return value


def _new_row(model, payload: dict, user: User, **fields):
    """A restored row: the copy's values, always for this user, with new links."""
    if not isinstance(payload, dict):
        raise trash_service.CannotRestore
    row = trash_service.row_from(model, {k: v for k, v in payload.items() if k != "old_id"})
    row.user_id = user.id
    for name, value in fields.items():
        setattr(row, name, value)
    return row


def restore_task(session: Session, user: User, payload: dict) -> None:
    """Undo a task delete. Its goal must still be there (else CannotRestore); a
    resource that is gone is simply left out."""
    data = payload["task"]
    goal = session.exec(
        select(LearningGoal).where(
            LearningGoal.id == _copy_id(data["goal_id"]), LearningGoal.user_id == user.id
        )
    ).first()
    if goal is None:
        raise trash_service.CannotRestore
    try:
        _check_room(session, user, LearningTask, MAX_TASKS)
    except TooMany:
        raise trash_service.CannotRestore from None
    resource_id = data.get("resource_id")
    if resource_id is not None:
        resource = session.exec(
            select(LearningResource.id).where(
                LearningResource.id == _copy_id(resource_id), LearningResource.user_id == user.id
            )
        ).first()
        resource_id = resource
    task = _new_row(LearningTask, data, user, goal_id=goal.id, resource_id=resource_id)
    session.add(task)
    session.flush()
    _relink_logs(session, user, payload.get("logs", []), goal.id, {data["old_id"]: task.id})


def restore_goal(session: Session, user: User, payload: dict) -> None:
    """Undo a goal delete: the goal, its resources and tasks come back with new ids,
    linked to each other again, and its logs point at them again."""
    try:
        _check_room(session, user, LearningGoal, MAX_GOALS)
        _check_room(session, user, LearningTask, MAX_TASKS, adding=len(payload["tasks"]))
    except TooMany:
        raise trash_service.CannotRestore from None
    goal = _new_row(LearningGoal, payload["goal"], user)
    if goal.source_application_id is not None:
        # Keep the link only to an application that is still this user's.
        goal.source_application_id = session.exec(
            select(Application.id).where(
                Application.id == _copy_id(goal.source_application_id),
                Application.user_id == user.id,
            )
        ).first()
    session.add(goal)
    session.flush()
    resource_ids = {}
    for data in payload["resources"]:
        resource = _new_row(LearningResource, data, user, goal_id=goal.id)
        session.add(resource)
        session.flush()
        resource_ids[data["old_id"]] = resource.id
    task_ids = {}
    for data in payload["tasks"]:
        task = _new_row(
            LearningTask,
            data,
            user,
            goal_id=goal.id,
            resource_id=resource_ids.get(data.get("resource_id")),
        )
        session.add(task)
        session.flush()
        task_ids[data["old_id"]] = task.id
    _relink_logs(session, user, payload.get("logs", []), goal.id, task_ids)
