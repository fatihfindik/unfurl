"""Preview, apply and undo a set of Learning changes (SPEC §5.9c, D-193).

The local MCP server (D-191) uses this when Claude wants to change more than two
records: `propose` checks everything and saves a preview, `apply` makes the changes
in ONE transaction, and `undo` takes them back within 24 hours.

Every change is stored with its `before` and `after` values. Apply writes a row only
if it still looks like `before` (and undo only if it still looks like `after`):
anything the user changed in between is left alone and counted as skipped.
"""

import datetime as dt
from dataclasses import dataclass

from sqlmodel import Session, col, func, select, update

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog, User
from app.models.base import as_utc, utcnow
from app.models.change_set import PendingChangeSet
from app.models.learning import GOAL_STATUSES
from app.models.profile import SHORT_TEXT, URL_TEXT
from app.schemas.learning import (
    MAX_HOURS_PER_WEEK,
    MIN_TASK_MINUTES,
    TARGET_YEARS,
    check_task_date,
    years_ahead,
)
from app.schemas.profile import check_url
from app.services import clock
from app.services import learning_service as learning

PENDING_FOR = dt.timedelta(hours=1)  # a preview must be applied within an hour
UNDO_FOR = dt.timedelta(hours=24)
KEEP_FOR = dt.timedelta(days=30)  # old sets are removed after that
MAX_CHANGES = 1000
MAX_PENDING = 10
SHIFT_DAYS = (-30, 730)
SAMPLE = 50  # changes listed by name in a preview
SKIPPED_SAMPLE = 10  # skipped rows listed by name

# Task fields a change can touch: name in the change -> column on LearningTask.
FIELDS = {"title": "title", "due_date": "due_date", "minutes": "estimated_minutes", "url": "url"}
# An update can also move a task to another goal (D-202): its resource goes with the old goal.
TASK_FIELDS = FIELDS | {"goal_id": "goal_id", "resource_id": "resource_id"}
# What may change on a done task (D-116, updated by D-202): title and goal, never its
# date, minutes or link.
DONE_EDITABLE = {"title", "goal_id", "resource_id"}
# Goal fields a goal update can touch: name in the change -> column on LearningGoal.
GOAL_FIELDS = {
    "title": "title",
    "weekly_hours": "weekly_hours_target",
    "target_date": "target_date",
    "status": "status",
    "archived": "archived_at",
}


class ChangeSetError(ValueError):
    """A request that can't be done; the message is shown to the user as it is."""


@dataclass
class Result:
    change_set: PendingChangeSet
    done: int
    skipped: int


# --- Checking values (the same rules as the forms, D-094) ------------------------------


def _title(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChangeSetError(f"{what}: the title is empty.")
    if len(value.strip()) > SHORT_TEXT:
        raise ChangeSetError(f"{what}: the title is longer than {SHORT_TEXT} characters.")
    return value.strip()


def _date(value, what: str) -> str:
    if not isinstance(value, str):
        raise ChangeSetError(f"{what}: use a date like 2026-12-01.")
    try:
        return check_task_date(value).isoformat()
    except ValueError as error:
        raise ChangeSetError(f"{what}: {error}") from None


def _minutes(value, what: str) -> int:
    if type(value) is not int or not MIN_TASK_MINUTES <= value <= learning.MAX_TASK_MINUTES:
        raise ChangeSetError(
            f"{what}: minutes must be from {MIN_TASK_MINUTES} to {learning.MAX_TASK_MINUTES}."
        )
    return value


def _url(value, what: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > URL_TEXT:
        raise ChangeSetError(f"{what}: the link must be text of at most {URL_TEXT} characters.")
    try:
        return check_url(value)
    except ValueError as error:
        raise ChangeSetError(f"{what}: {error}") from None


def _date_ok(value: str) -> bool:
    """True if a date is still inside the task date rule (30 days back, 2 years ahead)."""
    try:
        check_task_date(value)
    except ValueError:
        return False
    return True


def _id_ok(value) -> bool:
    """An id the database can compare: an int (not bool) from 1 to MAX_ID. Anything
    else simply matches nothing (a huge int would crash SQLite and Postgres)."""
    return type(value) is int and 0 < value <= learning.MAX_ID


def _current(task: LearningTask, fields) -> dict:
    """The task's values for these fields, as stored in a change (dates as text)."""
    values = {}
    for name in fields:
        value = getattr(task, TASK_FIELDS[name])
        values[name] = value.isoformat() if isinstance(value, dt.date) else value
    return values


def _set(task: LearningTask, values: dict) -> None:
    for name, value in values.items():
        if name == "due_date":
            value = dt.date.fromisoformat(value)
        setattr(task, TASK_FIELDS[name], value)


def _goal_current(goal: LearningGoal, fields) -> dict:
    """The goal's values for these fields, as stored in a change. `archived` is a
    true/false; the moment it was archived is not part of the comparison."""
    values = {}
    for name in fields:
        value = getattr(goal, GOAL_FIELDS[name])
        if name == "archived":
            value = value is not None
        elif isinstance(value, dt.date):
            value = value.isoformat()
        values[name] = value
    return values


def _goal_set(goal: LearningGoal, values: dict) -> None:
    for name, value in values.items():
        if name == "archived":
            value = utcnow() if value else None
        elif name == "target_date" and value is not None:
            value = dt.date.fromisoformat(value)
        setattr(goal, GOAL_FIELDS[name], value)


def _hours(value, what: str) -> float | None:
    """Weekly hours: empty (null) or 0.5 to MAX_HOURS_PER_WEEK, like the goal form."""
    if value is None:
        return None
    if type(value) not in (int, float) or not 0.5 <= value <= MAX_HOURS_PER_WEEK:
        raise ChangeSetError(f"{what}: weekly_hours must be from 0.5 to {MAX_HOURS_PER_WEEK}.")
    return float(value)


def _target(value, what: str) -> str | None:
    """A goal's target date: empty (null) or today to TARGET_YEARS ahead, like the form."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChangeSetError(f"{what}: use a target_date like 2026-12-01.")
    day = _iso(value, f"{what} target_date")
    today = clock.today()
    if not today <= day <= years_ahead(today, TARGET_YEARS):
        raise ChangeSetError(
            f"{what}: target_date must be between today and {TARGET_YEARS} years from now."
        )
    return day.isoformat()


# --- Reading the user's rows -----------------------------------------------------------------


def _tasks_by_id(
    session: Session, user: User, ids, *, include_deleted: bool = False
) -> dict[int, LearningTask]:
    """The user's tasks with these ids. Soft-deleted ones (D-200) only if asked: just the
    undo of a delete needs them. Tasks of archived goals are included; the callers
    check _archived_ids, because moving one to a live goal is allowed (D-201)."""
    ids = {number for number in ids if _id_ok(number)}
    if not ids:
        return {}
    query = select(LearningTask).where(
        LearningTask.user_id == user.id, col(LearningTask.id).in_(ids)
    )
    if not include_deleted:
        query = query.where(learning.LIVE_TASK)
    return {task.id: task for task in session.exec(query)}


def _archived_ids(session: Session, user: User) -> set[int]:
    """Ids of the user's archived goals: their tasks are read-only (D-201)."""
    return {goal.id for goal in learning.archived_goals(session, user)}


def _owned_set(session: Session, user: User, set_id) -> PendingChangeSet:
    if not _id_ok(set_id):
        raise ChangeSetError("Unknown change set.")
    change_set = session.exec(
        select(PendingChangeSet).where(
            PendingChangeSet.id == set_id, PendingChangeSet.user_id == user.id
        )
    ).first()
    if change_set is None:
        raise ChangeSetError("Unknown change set.")
    return change_set


def _count(session: Session, user: User, model) -> int:
    """Rows that count for the limits: soft-deleted tasks don't (D-200)."""
    return learning.count_rows(session, user, model)


def _clean_up(session: Session, user: User) -> None:
    """Remove this user's expired previews and sets older than KEEP_FOR."""
    now = utcnow()
    for change_set in session.exec(
        select(PendingChangeSet).where(PendingChangeSet.user_id == user.id)
    ):
        age = now - as_utc(change_set.created_at)
        if (change_set.status == "pending" and age > PENDING_FOR) or age > KEEP_FOR:
            session.delete(change_set)
    session.flush()


# --- Propose ---------------------------------------------------------------------------------


def propose(
    session: Session,
    user: User,
    *,
    shift: dict | None = None,
    moves: list[dict] = (),
    updates: list[dict] = (),
    completes: list[dict] = (),
    new_goals: list = (),
    adds: list[dict] = (),
    deletes: list[dict] = (),
    goal_updates: list[dict] = (),
) -> PendingChangeSet:
    """Check a request and save it as a preview. Nothing in the plan changes yet.
    Call inside clock.use_timezone(user.timezone): the date rules use "today"."""
    changes, skipped = _build(
        session,
        user,
        shift=shift,
        moves=moves,
        updates=updates,
        completes=completes,
        new_goals=new_goals,
        adds=adds,
        deletes=deletes,
        goal_updates=goal_updates,
    )
    _clean_up(session, user)
    pending = session.exec(
        select(func.count())
        .select_from(PendingChangeSet)
        .where(PendingChangeSet.user_id == user.id, PendingChangeSet.status == "pending")
    ).one()
    if pending >= MAX_PENDING:
        raise ChangeSetError("Too many previews waiting; apply or discard one first.")

    change_set = _new_set(session, user, changes, skipped)
    session.commit()
    session.refresh(change_set)
    return change_set


def run_single(session: Session, user: User, **request) -> Result:
    """One change made right away, still as a change set so undo_changes can take it
    back (D-203). Preview and apply share one transaction: if apply fails, no pending
    set is left behind, and the MAX_PENDING previews don't block it."""
    changes, skipped = _build(session, user, **request)
    if len(changes) != 1:
        raise ChangeSetError("This tool changes exactly one record.")
    try:
        change_set = _new_set(session, user, changes, skipped)
        return apply(session, user, change_set.id)
    except Exception:
        session.rollback()
        raise


def _new_set(session: Session, user: User, changes: list, skipped: list) -> PendingChangeSet:
    titles = {goal.id: goal.title for goal in learning.all_goals(session, user)}
    change_set = PendingChangeSet(
        user_id=user.id,
        kind="learning_plan",
        changes=changes,
        summary=_summary(changes, skipped, titles),
    )
    session.add(change_set)
    session.flush()
    return change_set


def _build(
    session: Session,
    user: User,
    *,
    shift: dict | None = None,
    moves: list[dict] = (),
    updates: list[dict] = (),
    completes: list[dict] = (),
    new_goals: list = (),
    adds: list[dict] = (),
    deletes: list[dict] = (),
    goal_updates: list[dict] = (),
) -> tuple[list[dict], list[str]]:
    """Check a request and turn it into concrete changes, one per row; nothing is saved."""
    for items in (moves, updates, completes, new_goals, adds, deletes, goal_updates):
        if len(items) > MAX_CHANGES:
            raise ChangeSetError(f"Too many changes; at most {MAX_CHANGES} at once.")
    changes: list[dict] = []
    skipped: list[str] = []
    touched: set[int] = set()
    ids = [c.get("task_id") for c in [*moves, *updates, *completes, *deletes]]
    tasks = _tasks_by_id(session, user, ids)
    archived = _archived_ids(session, user)

    def task_for(item: dict, what: str, *, archived_ok: bool = False) -> LearningTask:
        task = tasks.get(item.get("task_id"))
        if task is None:
            raise ChangeSetError(f"{what}: task {item.get('task_id')!r} not found.")
        if task.goal_id in archived and not archived_ok:
            raise ChangeSetError(
                f"{what}: task {task.id} belongs to an archived goal; unarchive it first."
            )
        if task.id in touched:
            raise ChangeSetError(f"Task {task.id} is changed more than once in this request.")
        touched.add(task.id)
        return task

    # Shift: every open task (of a goal, in a date range) by N days. Turned into one
    # "move" per task now, so the preview and the later check are exact (plan-reviewer).
    if shift:
        days = shift.get("days")
        if type(days) is not int or days == 0 or not SHIFT_DAYS[0] <= days <= SHIFT_DAYS[1]:
            raise ChangeSetError(
                f"Shift: days must be from {SHIFT_DAYS[0]} to {SHIFT_DAYS[1]}, not 0."
            )
        query = learning.live_tasks_query(user).where(col(LearningTask.done).is_(False))
        if shift.get("goal_id") is not None:
            goal = _owned_goal(session, user, shift["goal_id"], "Shift")
            query = query.where(LearningTask.goal_id == goal.id)
        if shift.get("from_date"):
            query = query.where(
                LearningTask.due_date >= _iso(shift["from_date"], "Shift from_date")
            )
        if shift.get("to_date"):
            query = query.where(LearningTask.due_date <= _iso(shift["to_date"], "Shift to_date"))
        explicit = {c.get("task_id") for c in [*moves, *updates, *completes, *deletes]}
        for task in session.exec(query.order_by(col(LearningTask.due_date), col(LearningTask.id))):
            if task.id in explicit:
                continue  # an explicit change for this task wins over the shift
            new = (task.due_date + dt.timedelta(days=days)).isoformat()
            if not _date_ok(new):
                skipped.append(f"{task.title} ({task.due_date} → {new}: outside the date rule)")
                continue
            touched.add(task.id)
            changes.append(_move(task, new))

    for item in moves:
        task = task_for(item, "Move")
        if task.done:
            raise ChangeSetError(f"Move: task {task.id} is done; done tasks keep their date.")
        new = _date(item.get("due_date"), f"Move task {task.id}")
        if new != task.due_date.isoformat():
            changes.append(_move(task, new))

    for item in updates:
        item = {key: value for key, value in item.items() if key != "goal_id" or value is not None}
        # Moving a task out of an archived goal is the one change it allows (D-201).
        task = task_for(item, "Update", archived_ok="goal_id" in item)
        after: dict = {}
        if "title" in item:
            after["title"] = _title(item["title"], f"Update task {task.id}")
        if task.done and set(item) - {"task_id", "title", "goal_id"}:
            raise ChangeSetError(
                f"Update: task {task.id} is done; only its title and goal can change."
            )
        if task.goal_id in archived and set(item) - {"task_id", "title", "goal_id"}:
            raise ChangeSetError(
                f"Update: task {task.id} belongs to an archived goal; only move it out."
            )
        if "goal_id" in item:
            goal = _owned_goal(session, user, item["goal_id"], f"Update task {task.id}")
            after["goal_id"] = goal.id
            after["resource_id"] = None if goal.id != task.goal_id else task.resource_id
        if "due_date" in item:
            after["due_date"] = _date(item["due_date"], f"Update task {task.id}")
        if "minutes" in item:
            after["minutes"] = _minutes(item["minutes"], f"Update task {task.id}")
        if "url" in item:
            after["url"] = _url(item["url"], f"Update task {task.id}")
        before = _current(task, after)
        after = {name: value for name, value in after.items() if before[name] != value}
        if after:
            before = {name: before[name] for name in after}
            changes.append(
                {
                    "op": "update",
                    "target_id": task.id,
                    "goal_id": task.goal_id,
                    "title": task.title,
                    "before": before,
                    "after": after,
                }
            )

    for item in deletes:
        task = task_for(item, "Delete")
        changes.append(
            {
                "op": "delete",
                "target_id": task.id,
                "goal_id": task.goal_id,
                "title": task.title,
                "before": {"deleted": False},
                "after": {"deleted": True},
            }
        )

    for item in completes:
        task = task_for(item, "Complete")
        done = item.get("done", True)
        if type(done) is not bool:
            raise ChangeSetError(f"Complete task {task.id}: done must be true or false.")
        if not done and task.done:
            # Reopening removes the task's logged time (D-092); only the app does that.
            raise ChangeSetError(f"Task {task.id} is done: reopen it in the Unfurl app.")
        if done != task.done:
            changes.append(
                {
                    "op": "complete",
                    "target_id": task.id,
                    "goal_id": task.goal_id,
                    "title": task.title,
                    "before": {"done": task.done},
                    "after": {"done": done},
                }
            )

    goals = learning.all_goals(session, user)  # archived ones count for titles and the limit
    by_title = {goal.title.casefold(): goal for goal in goals}
    new_refs: dict[str, int] = {}
    for ref, raw in enumerate(new_goals):
        # A title, or {"title", "weekly_hours"?, "target_date"?} (D-202).
        item = raw if isinstance(raw, dict) else {"title": raw}
        if set(item) - {"title", "weekly_hours", "target_date"}:
            raise ChangeSetError("New goal: only title, weekly_hours and target_date.")
        title = _title(item.get("title"), "New goal")
        if title.casefold() in new_refs:
            raise ChangeSetError(f'New goal: you already have a goal "{title}".')
        _check_free_title(by_title, title, "New goal")
        new_refs[title.casefold()] = ref
        after = {
            "title": title,
            "weekly_hours": _hours(item.get("weekly_hours"), f'New goal "{title}"'),
            "target_date": _target(item.get("target_date"), f'New goal "{title}"'),
        }
        changes.append({"op": "add_goal", "ref": ref, "before": None, "after": after})

    for number, item in enumerate(adds, start=1):
        what = f"New task {number}"
        after = {
            "title": _title(item.get("title"), what),
            "due_date": _date(item.get("due_date"), what),
            "minutes": _minutes(item.get("minutes", 60), what),
            "url": _url(item.get("url"), what),
        }
        if item.get("goal_id") is not None:
            after["goal_id"] = _owned_goal(session, user, item["goal_id"], what).id
        elif isinstance(item.get("goal_title"), str):
            key = item["goal_title"].strip().casefold()
            if key in by_title:
                after["goal_id"] = _live_goal(by_title[key], what).id
            elif key in new_refs:
                after["goal_ref"] = new_refs[key]
            else:
                raise ChangeSetError(
                    f'{what}: no goal "{item["goal_title"][:60]}"; add it to new_goals.'
                )
        else:
            raise ChangeSetError(f"{what}: give goal_id or goal_title.")
        changes.append({"op": "add", "target_id": None, "before": None, "after": after})

    renamed: set[str] = set()
    seen_goals: set[int] = set()
    for item in goal_updates:
        what = f"Goal update {item.get('goal_id')!r}"
        goal = _owned_goal(session, user, item.get("goal_id"), what, archived_ok=True)
        if goal.id in seen_goals:
            raise ChangeSetError(f"Goal {goal.id} is changed more than once in this request.")
        seen_goals.add(goal.id)
        fields = set(item) - {"goal_id"}
        if fields - set(GOAL_FIELDS):
            raise ChangeSetError(
                f"{what}: only title, weekly_hours, target_date, status and archived."
            )
        if goal.archived_at is not None and (fields != {"archived"} or item["archived"]):
            raise ChangeSetError(f"{what}: the goal is archived; only archived=false can change.")
        after = {}
        if "title" in item:
            after["title"] = _title(item["title"], what)
            key = after["title"].casefold()
            if key != goal.title.casefold():
                if key in renamed or key in new_refs:
                    raise ChangeSetError(f'{what}: another goal gets the title "{after["title"]}".')
                _check_free_title(by_title, after["title"], what)
                renamed.add(key)
        if "weekly_hours" in item:
            after["weekly_hours"] = _hours(item["weekly_hours"], what)
        if "target_date" in item:
            after["target_date"] = _target(item["target_date"], what)
        if "status" in item:
            if item["status"] not in GOAL_STATUSES:
                raise ChangeSetError(f"{what}: status must be one of {', '.join(GOAL_STATUSES)}.")
            after["status"] = item["status"]
        if "archived" in item:
            if type(item["archived"]) is not bool:
                raise ChangeSetError(f"{what}: archived must be true or false.")
            after["archived"] = item["archived"]
        before = _goal_current(goal, after)
        after = {name: value for name, value in after.items() if before[name] != value}
        if after:
            changes.append(
                {
                    "op": "update_goal",
                    "target_id": goal.id,
                    "title": goal.title,
                    "before": {name: before[name] for name in after},
                    "after": after,
                }
            )

    if not changes:
        message = "Nothing would change."
        if skipped:
            message += " Skipped: " + "; ".join(skipped[:SKIPPED_SAMPLE])
        raise ChangeSetError(message)
    if len(changes) > MAX_CHANGES:
        raise ChangeSetError(f"Too many changes ({len(changes)}); at most {MAX_CHANGES} at once.")
    added_goals = sum(1 for c in changes if c["op"] == "add_goal")
    added_tasks = sum(1 for c in changes if c["op"] == "add")
    if len(goals) + added_goals > learning.MAX_GOALS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_GOALS} goals.")
    if _count(session, user, LearningTask) + added_tasks > learning.MAX_TASKS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_TASKS} tasks.")
    return changes, skipped


def _iso(value, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ChangeSetError(f"{what}: use a date like 2026-12-01.") from None


def _owned_goal(
    session: Session, user: User, goal_id, what: str, *, archived_ok: bool = False
) -> LearningGoal:
    """The user's goal; an archived one only with archived_ok (D-201: it is read-only)."""
    goal = None
    if _id_ok(goal_id):
        goal = session.exec(
            select(LearningGoal).where(LearningGoal.id == goal_id, LearningGoal.user_id == user.id)
        ).first()
    if goal is None:
        raise ChangeSetError(f"{what}: goal {goal_id!r} not found.")
    return goal if archived_ok else _live_goal(goal, what)


def _live_goal(goal: LearningGoal, what: str) -> LearningGoal:
    if goal.archived_at is not None:
        raise ChangeSetError(f'{what}: the goal "{goal.title}" is archived; unarchive it first.')
    return goal


def _check_free_title(by_title: dict, title: str, what: str) -> None:
    """Goal titles are unique per user, archived goals included (D-201)."""
    taken = by_title.get(title.casefold())
    if taken is None:
        return
    if taken.archived_at is not None:
        raise ChangeSetError(f'{what}: an archived goal is named "{title}"; unarchive it instead.')
    raise ChangeSetError(f'{what}: you already have a goal "{title}".')


def _move(task: LearningTask, new: str) -> dict:
    return {
        "op": "move",
        "target_id": task.id,
        "goal_id": task.goal_id,  # a task moved to another goal since is skipped
        "title": task.title,
        "before": {"due_date": task.due_date.isoformat()},
        "after": {"due_date": new},
    }


def _summary(changes: list[dict], skipped: list[str], titles: dict[int, str]) -> str:
    """The preview: counts per kind, the date range of moves, then one line per change
    (up to SAMPLE) with its goal, "[Old goal → New goal]" when it moves (#114)."""
    names = {
        "move": "move",
        "update": "update",
        "complete": "done/undone",
        "delete": "delete",
        "add_goal": "new goal",
        "add": "new task",
        "update_goal": "goal update",
    }
    new_goal_titles = {c["ref"]: c["after"]["title"] for c in changes if c["op"] == "add_goal"}

    def goal_name(goal_id) -> str:
        return titles.get(goal_id, "?")

    def fields(values: dict) -> dict:
        return {k: v for k, v in values.items() if k not in ("goal_id", "resource_id")}

    counts: dict[str, int] = {}
    for change in changes:
        counts[change["op"]] = counts.get(change["op"], 0) + 1
    lines = [", ".join(f"{count} × {names[op]}" for op, count in counts.items())]
    moved = [c for c in changes if c["op"] == "move"]
    if moved:
        old = sorted(c["before"]["due_date"] for c in moved)
        new = sorted(c["after"]["due_date"] for c in moved)
        lines.append(f"Moves: {old[0]}…{old[-1]} → {new[0]}…{new[-1]}")
    for change in changes[:SAMPLE]:
        op = change["op"]
        if op in ("move", "update", "complete", "delete"):
            goal = goal_name(change.get("goal_id"))
            if "goal_id" in change["after"]:
                goal += f" → {goal_name(change['after']['goal_id'])}"
            line = f"- {change['title']} [{goal}]"
            if op == "complete":
                line += f": done = {change['after']['done']}"
            elif op == "delete":
                line += ": delete (undo_changes brings it back)"
            elif fields(change["after"]):
                line += f": {fields(change['before'])} → {fields(change['after'])}"
            lines.append(line)
        elif op == "update_goal":
            lines.append(f'- goal "{change["title"]}": {change["before"]} → {change["after"]}')
        elif op == "add":
            after = change["after"]
            goal = goal_name(after["goal_id"]) if "goal_id" in after else ""
            goal = goal or new_goal_titles.get(after.get("goal_ref"), "?")
            lines.append(f"- new task: {after['title']} [{goal}] on {after['due_date']}")
        else:
            extra = {k: v for k, v in change["after"].items() if k != "title" and v is not None}
            lines.append(f"- new goal: {change['after']['title']}" + (f" {extra}" if extra else ""))
    if len(changes) > SAMPLE:
        lines.append(f"… and {len(changes) - SAMPLE} more")
    if skipped:
        lines.append(f"Skipped ({len(skipped)}):")
        lines += [f"- {item}" for item in skipped[:SKIPPED_SAMPLE]]
        if len(skipped) > SKIPPED_SAMPLE:
            lines.append(f"… and {len(skipped) - SKIPPED_SAMPLE} more")
    return "\n".join(lines)


# --- Apply, undo, discard ---------------------------------------------------------------------


def apply(session: Session, user: User, set_id) -> Result:
    """Make the changes of a preview, all in one transaction (D-193)."""
    change_set = _owned_set(session, user, set_id)
    if change_set.status != "pending":
        raise ChangeSetError(f"This change set is {change_set.status}, not waiting.")
    if utcnow() - as_utc(change_set.created_at) > PENDING_FOR:
        raise ChangeSetError("This preview is older than an hour; make a new one.")
    changes = [dict(change) for change in change_set.changes]
    added = sum(1 for c in changes if c["op"] == "add")
    if _count(session, user, LearningTask) + added > learning.MAX_TASKS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_TASKS} tasks.")
    new_goals = sum(1 for c in changes if c["op"] == "add_goal")
    if _count(session, user, LearningGoal) + new_goals > learning.MAX_GOALS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_GOALS} goals.")
    _claim(session, user, change_set, "pending")

    tasks = _tasks_by_id(session, user, [c.get("target_id") for c in changes])
    # Goals as ORM objects: a goal archived earlier in this same set counts at once.
    goals = {goal.id: goal for goal in learning.all_goals(session, user)}
    sort_base = len(goals)
    refs: dict[int, int] = {}
    done = skipped = 0

    def live(goal_id) -> bool:
        goal = goals.get(goal_id)
        return goal is not None and goal.archived_at is None

    try:
        for change in changes:
            change["applied"] = False
            op = change["op"]
            task = tasks.get(change.get("target_id")) if op != "update_goal" else None
            if op in ("move", "update"):
                fields = change["before"].keys()
                after = change["after"]
                new_date = after.get("due_date")
                if (
                    task is None
                    # archived since the preview: only a move out of the goal is allowed
                    or (not live(task.goal_id) and "goal_id" not in after)
                    or ("goal_id" in after and not live(after["goal_id"]))
                    or task.goal_id != change.get("goal_id", task.goal_id)
                    or _current(task, fields) != change["before"]
                    or (task.done and set(after) - DONE_EDITABLE)  # D-116, D-202
                    or (new_date and not _date_ok(new_date))
                ):
                    skipped += 1
                    continue
                _set(task, after)
                session.add(task)
            elif op == "delete":
                if task is None or not live(task.goal_id) or task.goal_id != change["goal_id"]:
                    skipped += 1
                    continue
                task.deleted_at = utcnow()
                session.add(task)
                change["deleted_at"] = as_utc(task.deleted_at).isoformat()
            elif op == "complete":
                if task is None or not live(task.goal_id) or task.done != change["before"]["done"]:
                    skipped += 1
                    continue
                learning.apply_done(session, user, task, change["after"]["done"])
                session.flush()
                log = session.exec(
                    select(StudyLog)
                    .where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
                    .order_by(col(StudyLog.id).desc())
                ).first()
                if log is not None:  # undo removes only this row, if unchanged
                    change["created_log"] = {"id": log.id, "minutes": log.minutes}
            elif op == "update_goal":
                goal = goals.get(change["target_id"])
                if (
                    goal is None
                    or _goal_current(goal, change["before"].keys()) != change["before"]
                    or _title_taken(goals, goal, change["after"].get("title"))
                ):
                    skipped += 1
                    continue
                _goal_set(goal, change["after"])
                session.add(goal)
            elif op == "add_goal":
                after = change["after"]
                if _title_taken(goals, None, after["title"]):
                    skipped += 1
                    continue
                goal = LearningGoal(
                    user_id=user.id,
                    title=after["title"],
                    weekly_hours_target=after.get("weekly_hours"),
                    target_date=_day_or_none(after.get("target_date")),
                    sort_order=sort_base,
                )
                sort_base += 1
                session.add(goal)
                session.flush()
                goals[goal.id] = goal
                refs[change["ref"]] = goal.id
                change["created_id"] = goal.id
            elif op == "add":
                after = change["after"]
                goal_id = after.get("goal_id")
                if goal_id is None:
                    goal_id = refs.get(after.get("goal_ref"))
                if not live(goal_id) or not _date_ok(after["due_date"]):
                    skipped += 1
                    continue
                task = LearningTask(
                    user_id=user.id,
                    goal_id=goal_id,
                    title=after["title"],
                    due_date=dt.date.fromisoformat(after["due_date"]),
                    estimated_minutes=after["minutes"],
                    url=after["url"],
                    sort_order=_count(session, user, LearningTask),
                )
                session.add(task)
                session.flush()
                change["created_id"] = task.id
                change["placed_goal_id"] = goal_id
            change["applied"] = True
            done += 1
        change_set.changes = changes  # a new list, so the JSON column is saved
        change_set.status = "applied"
        change_set.applied_at = utcnow()
        session.add(change_set)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(change_set)
    return Result(change_set, done, skipped)


def undo(session: Session, user: User, set_id) -> Result:
    """Take an applied set back within 24 hours, in one transaction. Rows changed since
    are left alone. Tasks and goals this set added are removed only if unchanged."""
    change_set = _owned_set(session, user, set_id)
    if change_set.status != "applied":
        raise ChangeSetError(
            f"This change set is {change_set.status}; only applied ones can be undone."
        )
    if change_set.applied_at is None or utcnow() - as_utc(change_set.applied_at) > UNDO_FOR:
        raise ChangeSetError("Undo is possible for 24 hours after applying.")
    _claim(session, user, change_set, "applied")
    changes = [c for c in change_set.changes if c.get("applied")]
    ids = [
        c.get("target_id") or c.get("created_id")
        for c in changes
        if c["op"] not in ("add_goal", "update_goal")
    ]
    # Deleted ones too: only the undo of a delete may touch them (checked per op below).
    tasks = _tasks_by_id(session, user, ids, include_deleted=True)
    goals = {goal.id: goal for goal in learning.all_goals(session, user)}
    done = skipped = 0

    def live(goal_id) -> bool:
        goal = goals.get(goal_id)
        return goal is not None and goal.archived_at is None

    try:
        for change in reversed(changes):
            op = change["op"]
            task = None
            if op not in ("add_goal", "update_goal"):
                task = tasks.get(change.get("target_id") or change.get("created_id"))
                if task is not None and task.deleted_at is not None and op != "delete":
                    task = None  # deleted since: left alone
            if op in ("move", "update"):
                before, after = dict(change["before"]), change["after"]
                if "goal_id" in before:
                    before["resource_id"] = _resource_back(session, user, before)
                if (
                    task is None
                    or not live(task.goal_id)  # read-only now (D-201)
                    or ("goal_id" in before and not live(before["goal_id"]))
                    or _current(task, after.keys()) != after
                    or (task.done and set(before) - DONE_EDITABLE)  # D-116, D-202
                ):
                    skipped += 1
                    continue
                _set(task, before)
                session.add(task)
            elif op == "delete":
                if (
                    task is None
                    or task.deleted_at is None
                    or as_utc(task.deleted_at).isoformat() != change.get("deleted_at")
                    or not live(task.goal_id)
                    or _count(session, user, LearningTask) + 1 > learning.MAX_TASKS
                ):
                    skipped += 1
                    continue
                task.deleted_at = None
                session.add(task)
                session.flush()  # the next count sees it
            elif op == "complete":
                if (
                    task is None
                    or not live(task.goal_id)
                    or not task.done
                    or not _only_own_log(session, user, task, change)
                ):
                    skipped += 1  # reopened since, or its time was edited in the app
                    continue
                learning.apply_done(session, user, task, False)
            elif op == "update_goal":
                goal = goals.get(change["target_id"])
                after = change["after"]
                if (
                    goal is None
                    or (goal.archived_at is not None and "archived" not in after)
                    or _goal_current(goal, after.keys()) != after
                    or _title_taken(goals, goal, change["before"].get("title"))
                ):
                    skipped += 1
                    continue
                _goal_set(goal, change["before"])
                session.add(goal)
            elif op == "add":
                expected = {k: change["after"][k] for k in FIELDS}
                if (
                    task is None
                    or task.done
                    or not live(task.goal_id)
                    or task.resource_id is not None
                    or task.goal_id != change.get("placed_goal_id")
                    or _current(task, FIELDS) != expected
                    or _has_log(session, user, task)
                ):
                    skipped += 1
                    continue
                session.delete(task)  # the one written exception to "never deletes" (D-193)
            elif op == "add_goal":
                session.flush()
                goal = goals.get(change.get("created_id"))
                after = change["after"]
                if (
                    goal is None
                    or goal.title != after["title"]
                    # The user edited it since (description, date, hours, status): keep it.
                    or goal.description is not None
                    or goal.weekly_hours_target != after.get("weekly_hours")
                    or goal.target_date != _day_or_none(after.get("target_date"))
                    or goal.status != "active"
                    or goal.archived_at is not None
                    or _goal_in_use(session, user, goal)
                ):
                    skipped += 1
                    continue
                session.delete(goal)
                del goals[goal.id]
            done += 1
        change_set.status = "undone"
        session.add(change_set)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(change_set)
    return Result(change_set, done, skipped)


def _day_or_none(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def _title_taken(goals: dict, goal: LearningGoal | None, title: str | None) -> bool:
    """True if another of these goals already has `title` (any case): checked again at
    apply and undo, since the user may have added or renamed a goal in between."""
    if title is None:
        return False
    key = title.casefold()
    return any(other.title.casefold() == key for other in goals.values() if other is not goal)


def _resource_back(session: Session, user: User, before: dict) -> int | None:
    """The resource a task had before it was moved, if it still exists and still belongs
    to the old goal (the restore_task pattern); otherwise none."""
    resource_id = before.get("resource_id")
    if not _id_ok(resource_id):
        return None
    return session.exec(
        select(LearningResource.id).where(
            LearningResource.id == resource_id,
            LearningResource.user_id == user.id,
            LearningResource.goal_id == before["goal_id"],
        )
    ).first()


def discard(session: Session, user: User, set_id) -> PendingChangeSet:
    change_set = _owned_set(session, user, set_id)
    if change_set.status != "pending":
        raise ChangeSetError(f"This change set is {change_set.status}, not waiting.")
    change_set.status = "discarded"
    session.add(change_set)
    session.commit()
    session.refresh(change_set)
    return change_set


def _claim(session: Session, user: User, change_set: PendingChangeSet, status: str) -> None:
    """Take the set with a conditional UPDATE, so two calls at the same time can't both
    apply (or undo) it: the second finds no row in that status (security review W1).
    Rolled back with everything else if the work fails."""
    claimed = session.exec(
        update(PendingChangeSet)
        .where(
            PendingChangeSet.id == change_set.id,
            PendingChangeSet.user_id == user.id,
            PendingChangeSet.status == status,
        )
        .values(status="working")
    )
    if claimed.rowcount != 1:
        session.rollback()
        raise ChangeSetError("This change set is being changed by another call; try again.")


def _only_own_log(session: Session, user: User, task: LearningTask, change: dict) -> bool:
    """True if the task's only StudyLog is the one this set wrote, with its minutes."""
    own = change.get("created_log")
    logs = list(
        session.exec(
            select(StudyLog).where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
        )
    )
    if own is None:
        return not logs
    return len(logs) == 1 and logs[0].id == own["id"] and logs[0].minutes == own["minutes"]


def _has_log(session: Session, user: User, task: LearningTask) -> bool:
    return (
        session.exec(
            select(StudyLog.id).where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
        ).first()
        is not None
    )


def _goal_in_use(session: Session, user: User, goal: LearningGoal) -> bool:
    for model in (LearningTask, LearningResource, StudyLog):
        row = session.exec(
            select(model.id).where(model.user_id == user.id, model.goal_id == goal.id)
        ).first()
        if row is not None:
            return True
    return False
