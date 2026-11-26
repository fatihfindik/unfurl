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

from sqlmodel import Session, col, func, select

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog, User
from app.models.base import as_utc, utcnow
from app.models.change_set import PendingChangeSet
from app.models.profile import SHORT_TEXT, URL_TEXT
from app.schemas.learning import MIN_TASK_MINUTES, check_task_date
from app.schemas.profile import check_url
from app.services import learning_service as learning

PENDING_FOR = dt.timedelta(hours=1)  # a preview must be applied within an hour
UNDO_FOR = dt.timedelta(hours=24)
KEEP_FOR = dt.timedelta(days=30)  # old sets are removed after that
MAX_CHANGES = 1000
MAX_PENDING = 10
SHIFT_DAYS = (-30, 730)
SAMPLE = 10  # changes listed by name in a preview

# Task fields a change can touch: name in the change -> column on LearningTask.
FIELDS = {"title": "title", "due_date": "due_date", "minutes": "estimated_minutes", "url": "url"}


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


def _current(task: LearningTask, fields) -> dict:
    """The task's values for these fields, as stored in a change (dates as text)."""
    values = {}
    for name in fields:
        value = getattr(task, FIELDS[name])
        values[name] = value.isoformat() if isinstance(value, dt.date) else value
    return values


def _set(task: LearningTask, values: dict) -> None:
    for name, value in values.items():
        if name == "due_date":
            value = dt.date.fromisoformat(value)
        setattr(task, FIELDS[name], value)


# --- Reading the user's rows -----------------------------------------------------------------


def _tasks_by_id(session: Session, user: User, ids) -> dict[int, LearningTask]:
    ids = {number for number in ids if type(number) is int}
    if not ids:
        return {}
    rows = session.exec(
        select(LearningTask).where(LearningTask.user_id == user.id, col(LearningTask.id).in_(ids))
    )
    return {task.id: task for task in rows}


def _owned_set(session: Session, user: User, set_id) -> PendingChangeSet:
    if type(set_id) is not int:
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
    return session.exec(
        select(func.count()).select_from(model).where(model.user_id == user.id)
    ).one()


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
    new_goals: list[str] = (),
    adds: list[dict] = (),
) -> PendingChangeSet:
    """Check a request and save it as a preview. Nothing in the plan changes yet.
    Call inside clock.use_timezone(user.timezone): the date rules use "today"."""
    changes: list[dict] = []
    skipped: list[str] = []
    touched: set[int] = set()
    ids = [c.get("task_id") for c in [*moves, *updates, *completes]]
    tasks = _tasks_by_id(session, user, ids)

    def task_for(item: dict, what: str) -> LearningTask:
        task = tasks.get(item.get("task_id"))
        if task is None:
            raise ChangeSetError(f"{what}: task {item.get('task_id')!r} not found.")
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
        query = select(LearningTask).where(
            LearningTask.user_id == user.id, col(LearningTask.done).is_(False)
        )
        if shift.get("goal_id") is not None:
            goal = _owned_goal(session, user, shift["goal_id"], "Shift")
            query = query.where(LearningTask.goal_id == goal.id)
        if shift.get("from_date"):
            query = query.where(
                LearningTask.due_date >= _iso(shift["from_date"], "Shift from_date")
            )
        if shift.get("to_date"):
            query = query.where(LearningTask.due_date <= _iso(shift["to_date"], "Shift to_date"))
        explicit = {c.get("task_id") for c in [*moves, *updates, *completes]}
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
        task = task_for(item, "Update")
        after: dict = {}
        if "title" in item:
            after["title"] = _title(item["title"], f"Update task {task.id}")
        if task.done and set(item) - {"task_id", "title"}:
            raise ChangeSetError(f"Update: task {task.id} is done; only its title can change.")
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
                    "title": task.title,
                    "before": before,
                    "after": after,
                }
            )

    for item in completes:
        task = task_for(item, "Complete")
        done = item.get("done", True)
        if type(done) is not bool:
            raise ChangeSetError(f"Complete task {task.id}: done must be true or false.")
        if done != task.done:
            changes.append(
                {
                    "op": "complete",
                    "target_id": task.id,
                    "title": task.title,
                    "before": {"done": task.done},
                    "after": {"done": done},
                }
            )

    goals = learning.goals(session, user)
    by_title = {goal.title.casefold(): goal for goal in goals}
    new_refs: dict[str, int] = {}
    for ref, raw in enumerate(new_goals):
        title = _title(raw, "New goal")
        if title.casefold() in by_title or title.casefold() in new_refs:
            raise ChangeSetError(f'New goal: you already have a goal "{title}".')
        new_refs[title.casefold()] = ref
        changes.append({"op": "add_goal", "ref": ref, "before": None, "after": {"title": title}})

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
                after["goal_id"] = by_title[key].id
            elif key in new_refs:
                after["goal_ref"] = new_refs[key]
            else:
                raise ChangeSetError(
                    f'{what}: no goal "{item["goal_title"]}"; add it to new_goals.'
                )
        else:
            raise ChangeSetError(f"{what}: give goal_id or goal_title.")
        changes.append({"op": "add", "target_id": None, "before": None, "after": after})

    if not changes:
        message = "Nothing would change."
        if skipped:
            message += " Skipped: " + "; ".join(skipped[:SAMPLE])
        raise ChangeSetError(message)
    if len(changes) > MAX_CHANGES:
        raise ChangeSetError(f"Too many changes ({len(changes)}); at most {MAX_CHANGES} at once.")
    added_goals = sum(1 for c in changes if c["op"] == "add_goal")
    added_tasks = sum(1 for c in changes if c["op"] == "add")
    if len(goals) + added_goals > learning.MAX_GOALS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_GOALS} goals.")
    if _count(session, user, LearningTask) + added_tasks > learning.MAX_TASKS:
        raise ChangeSetError(f"This would pass the limit of {learning.MAX_TASKS} tasks.")

    _clean_up(session, user)
    pending = session.exec(
        select(func.count())
        .select_from(PendingChangeSet)
        .where(PendingChangeSet.user_id == user.id, PendingChangeSet.status == "pending")
    ).one()
    if pending >= MAX_PENDING:
        raise ChangeSetError("Too many previews waiting; apply or discard one first.")

    change_set = PendingChangeSet(
        user_id=user.id, kind="learning_plan", changes=changes, summary=_summary(changes, skipped)
    )
    session.add(change_set)
    session.commit()
    session.refresh(change_set)
    return change_set


def _iso(value, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ChangeSetError(f"{what}: use a date like 2026-12-01.") from None


def _owned_goal(session: Session, user: User, goal_id, what: str) -> LearningGoal:
    goal = None
    if type(goal_id) is int:
        goal = session.exec(
            select(LearningGoal).where(LearningGoal.id == goal_id, LearningGoal.user_id == user.id)
        ).first()
    if goal is None:
        raise ChangeSetError(f"{what}: goal {goal_id!r} not found.")
    return goal


def _move(task: LearningTask, new: str) -> dict:
    return {
        "op": "move",
        "target_id": task.id,
        "title": task.title,
        "before": {"due_date": task.due_date.isoformat()},
        "after": {"due_date": new},
    }


def _summary(changes: list[dict], skipped: list[str]) -> str:
    """The preview: counts per kind, the date range of moves, a few examples, skipped."""
    names = {
        "move": "move",
        "update": "update",
        "complete": "done/undone",
        "add_goal": "new goal",
        "add": "new task",
    }
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
        if change["op"] in ("move", "update"):
            lines.append(f"- {change['title']}: {change['before']} → {change['after']}")
        elif change["op"] == "complete":
            lines.append(f"- {change['title']}: done = {change['after']['done']}")
        else:
            lines.append(f"- {names[change['op']]}: {change['after']['title']}")
    if len(changes) > SAMPLE:
        lines.append(f"… and {len(changes) - SAMPLE} more")
    if skipped:
        lines.append(f"Skipped ({len(skipped)}):")
        lines += [f"- {item}" for item in skipped[:SAMPLE]]
        if len(skipped) > SAMPLE:
            lines.append(f"… and {len(skipped) - SAMPLE} more")
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

    tasks = _tasks_by_id(session, user, [c.get("target_id") for c in changes])
    goal_ids = {goal.id for goal in learning.goals(session, user)}
    refs: dict[int, int] = {}
    done = skipped = 0
    try:
        for change in changes:
            change["applied"] = False
            op = change["op"]
            task = tasks.get(change.get("target_id"))
            if op in ("move", "update"):
                fields = change["before"].keys()
                new_date = change["after"].get("due_date")
                if (
                    task is None
                    or _current(task, fields) != change["before"]
                    or (new_date and (task.done or not _date_ok(new_date)))
                ):
                    skipped += 1
                    continue
                _set(task, change["after"])
                session.add(task)
            elif op == "complete":
                if task is None or task.done != change["before"]["done"]:
                    skipped += 1
                    continue
                learning.apply_done(session, user, task, change["after"]["done"])
            elif op == "add_goal":
                goal = LearningGoal(
                    user_id=user.id, title=change["after"]["title"], sort_order=len(goal_ids)
                )
                session.add(goal)
                session.flush()
                goal_ids.add(goal.id)
                refs[change["ref"]] = goal.id
                change["created_id"] = goal.id
            elif op == "add":
                after = change["after"]
                goal_id = after.get("goal_id")
                if goal_id is None:
                    goal_id = refs.get(after.get("goal_ref"))
                if goal_id not in goal_ids or not _date_ok(after["due_date"]):
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
    changes = [c for c in change_set.changes if c.get("applied")]
    ids = [c.get("target_id") or c.get("created_id") for c in changes if c["op"] != "add_goal"]
    tasks = _tasks_by_id(session, user, ids)
    done = skipped = 0
    try:
        for change in reversed(changes):
            op = change["op"]
            if op in ("move", "update"):
                task = tasks.get(change["target_id"])
                if task is None or _current(task, change["after"].keys()) != change["after"]:
                    skipped += 1
                    continue
                _set(task, change["before"])
                session.add(task)
            elif op == "complete":
                task = tasks.get(change["target_id"])
                if task is None or task.done != change["after"]["done"]:
                    skipped += 1
                    continue
                learning.apply_done(session, user, task, change["before"]["done"])
            elif op == "add":
                task = tasks.get(change.get("created_id"))
                expected = {k: change["after"][k] for k in FIELDS}
                if (
                    task is None
                    or task.done
                    or _current(task, FIELDS) != expected
                    or _has_log(session, user, task)
                ):
                    skipped += 1
                    continue
                session.delete(task)  # the one written exception to "never deletes" (D-193)
            elif op == "add_goal":
                session.flush()
                goal = session.exec(
                    select(LearningGoal).where(
                        LearningGoal.id == change.get("created_id"), LearningGoal.user_id == user.id
                    )
                ).first()
                if (
                    goal is None
                    or goal.title != change["after"]["title"]
                    or _goal_in_use(session, user, goal)
                ):
                    skipped += 1
                    continue
                session.delete(goal)
            done += 1
        change_set.status = "undone"
        session.add(change_set)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(change_set)
    return Result(change_set, done, skipped)


def discard(session: Session, user: User, set_id) -> PendingChangeSet:
    change_set = _owned_set(session, user, set_id)
    if change_set.status != "pending":
        raise ChangeSetError(f"This change set is {change_set.status}, not waiting.")
    change_set.status = "discarded"
    session.add(change_set)
    session.commit()
    session.refresh(change_set)
    return change_set


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
