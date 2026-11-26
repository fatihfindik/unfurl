"""Local MCP server: manage Unfurl's Learning plan from the Claude app (D-191, #107).

The Claude Desktop app starts it and talks to it over stdin/stdout (the "stdio"
transport of the Model Context Protocol):

    docker exec -i unfurl-personal-app python -m app.mcp_server --email you@example.com
    uv run python -m app.mcp_server --email you@example.com        (development)

It works for one user, found once by email; every tool only reads and writes that
user's rows. No tool deletes anything. A change to more than two records goes through
a preview (`propose_learning_changes`) that only `apply_changes` makes real (D-193);
in Claude Desktop, never "Always allow" `apply_changes` or `undo_changes`.

The tool logic lives in plain functions that take (session, user, ...), so tests can
call them without an MCP client. Nothing here imports app.main, so the scheduler and
the web app don't start.
"""

import argparse
import datetime as dt
import functools
import inspect
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine
from sqlmodel import Session, col, func, select

from app.models import LearningGoal, LearningTask, User
from app.models.change_set import PendingChangeSet
from app.models.user import normalize_email
from app.schemas.learning import MAX_HOURS_PER_WEEK, MIN_SPENT_MINUTES, TARGET_YEARS, years_ahead
from app.services import change_set_service as changes
from app.services import clock
from app.services import learning_service as learning
from app.services.change_set_service import ChangeSetError

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
# Apply and undo change many rows at once: the user should confirm each call.
BULK = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

MAX_LIST = 200
INSTRUCTIONS = """Unfurl is the user's job search and learning app. These tools manage
the Learning plan: goals and their tasks. Dates are YYYY-MM-DD in the user's time zone.
To change more than two tasks, call propose_learning_changes, show the user the preview
and call apply_changes only after the user says yes. Nothing can be deleted."""


class Failed(ToolError):
    """A message for the user (bad input, not found); shown by Claude as it is."""


# --- Shared helpers ---------------------------------------------------------------------------


def task_info(task: LearningTask, goal_titles: dict[int, str]) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "goal_id": task.goal_id,
        "goal": goal_titles.get(task.goal_id, ""),
        "due_date": task.due_date.isoformat(),
        "minutes": task.estimated_minutes,
        "url": task.url,
        "done": task.done,
        "done_on": clock.local(task.done_at).date().isoformat() if task.done_at else None,
    }


def _goal_titles(session: Session, user: User) -> dict[int, str]:
    return {goal.id: goal.title for goal in learning.goals(session, user)}


def _owned(session: Session, user: User, model, item_id) -> object:
    try:
        return learning.get_owned(session, user, model, item_id)
    except HTTPException:
        name = "Goal" if model is LearningGoal else "Task"
        raise Failed(f"{name} {item_id!r} not found.") from None


def _date(value: str | None, what: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise Failed(f"{what}: use a date like 2026-12-01.") from None


def _check(fn, *args):
    """Run one of change_set_service's value checks; its error becomes a tool error."""
    try:
        return fn(*args)
    except ChangeSetError as error:
        raise Failed(str(error)) from None


# --- Reading ----------------------------------------------------------------------------------


def get_learning_plan(session: Session, user: User) -> dict:
    """Goals with progress, today's and overdue tasks, the next 7 days, streak, week."""
    today = clock.today()
    goals = learning.goals(session, user)
    titles = {goal.id: goal.title for goal in goals}
    tasks = learning.tasks(session, user)
    resources = learning.resources_by_goal(session, user)
    week = learning.this_week(session, user)
    open_tasks = [t for t in tasks if not t.done]
    last = today + dt.timedelta(days=6)
    return {
        "today": today.isoformat(),
        "streak_days": learning.streak(session, user),
        "this_week": {
            "minutes_logged": week.minutes,
            "target_hours": week.target_hours,
            "tasks_done": week.tasks_done,
            "tasks_total": week.tasks_total,
        },
        "goals": [
            {
                "id": goal.id,
                "title": goal.title,
                "status": goal.status,
                "progress_pct": learning.progress_pct(
                    goal, resources.get(goal.id), [t for t in tasks if t.goal_id == goal.id]
                ),
                "open_tasks": sum(1 for t in open_tasks if t.goal_id == goal.id),
                "done_tasks": sum(1 for t in tasks if t.goal_id == goal.id and t.done),
            }
            for goal in goals
        ],
        "overdue": [task_info(t, titles) for t in open_tasks if t.due_date < today][:50],
        "today_tasks": [task_info(t, titles) for t in open_tasks if t.due_date == today],
        "next_7_days": [task_info(t, titles) for t in open_tasks if today < t.due_date <= last][
            :50
        ],
    }


def list_tasks(
    session: Session,
    user: User,
    goal_id: int | None = None,
    status: str = "open",
    from_date: str | None = None,
    to_date: str | None = None,
    text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Tasks by goal, state, date range or words in the title, sorted by date."""
    if status not in ("open", "done", "all"):
        raise Failed('status must be "open", "done" or "all".')
    if not 1 <= limit <= MAX_LIST or offset < 0:
        raise Failed(f"limit must be 1…{MAX_LIST} and offset 0 or more.")
    query = select(LearningTask).where(LearningTask.user_id == user.id)
    if goal_id is not None:
        query = query.where(LearningTask.goal_id == _owned(session, user, LearningGoal, goal_id).id)
    if status != "all":
        query = query.where(col(LearningTask.done).is_(status == "done"))
    if start := _date(from_date, "from_date"):
        query = query.where(LearningTask.due_date >= start)
    if end := _date(to_date, "to_date"):
        query = query.where(LearningTask.due_date <= end)
    if text:
        query = query.where(col(LearningTask.title).icontains(text[:100]))
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(
            col(LearningTask.due_date), col(LearningTask.sort_order), col(LearningTask.id)
        )
        .offset(offset)
        .limit(limit)
    )
    titles = _goal_titles(session, user)
    return {"total": total, "offset": offset, "tasks": [task_info(t, titles) for t in rows]}


# --- One record at a time ---------------------------------------------------------------------


def create_learning_goal(
    session: Session,
    user: User,
    title: str,
    target_date: str | None = None,
    weekly_hours: float | None = None,
) -> dict:
    title = _check(changes._title, title, "Goal")
    target = _date(target_date, "target_date")
    today = clock.today()
    if target and not today <= target <= years_ahead(today, TARGET_YEARS):
        raise Failed(f"target_date must be between today and {TARGET_YEARS} years from now.")
    if weekly_hours is not None and not 0.5 <= weekly_hours <= MAX_HOURS_PER_WEEK:
        raise Failed(f"weekly_hours must be from 0.5 to {MAX_HOURS_PER_WEEK}.")
    titles = {goal.title.casefold() for goal in learning.goals(session, user)}
    if title.casefold() in titles:
        raise Failed(f'You already have a goal "{title}".')
    try:
        goal = learning.create_goal(
            session,
            user,
            title=title,
            target_date=target,
            weekly_hours=weekly_hours,
            resource=None,
            plan_weekly=False,
        )
    except learning.TooMany:
        raise Failed(f"You have the most goals allowed ({learning.MAX_GOALS}).") from None
    return {"id": goal.id, "title": goal.title}


def add_task(
    session: Session,
    user: User,
    goal_id: int,
    title: str,
    due_date: str,
    minutes: int = 60,
    url: str | None = None,
) -> dict:
    goal = _owned(session, user, LearningGoal, goal_id)
    title = _check(changes._title, title, "Task")
    day = dt.date.fromisoformat(_check(changes._date, due_date, "due_date"))
    minutes = _check(changes._minutes, minutes, "minutes")
    url = _check(changes._url, url, "url")
    try:
        task = learning.add_task(
            session, user, goal, title=title, due_date=day, minutes=minutes, url=url
        )
    except learning.TooMany:
        raise Failed(f"You have the most tasks allowed ({learning.MAX_TASKS}).") from None
    return task_info(task, _goal_titles(session, user))


def update_task(
    session: Session,
    user: User,
    task_id: int,
    title: str | None = None,
    due_date: str | None = None,
    minutes: int | None = None,
    url: str | None = None,
) -> dict:
    """Title, date, minutes or link of one task. A done task keeps everything but its
    title (D-116); its time spent is edited in the app."""
    task = _owned(session, user, LearningTask, task_id)
    if task.done and (due_date is not None or minutes is not None or url is not None):
        raise Failed(f"Task {task.id} is done: only its title can change.")
    if title is not None:
        task.title = _check(changes._title, title, "title")
    if due_date is not None:
        task.due_date = dt.date.fromisoformat(_check(changes._date, due_date, "due_date"))
    if minutes is not None:
        task.estimated_minutes = _check(changes._minutes, minutes, "minutes")
    if url is not None:
        task.url = _check(changes._url, url, "url")  # "" removes the link
    session.add(task)
    session.commit()
    session.refresh(task)
    return task_info(task, _goal_titles(session, user))


def set_task_done(session: Session, user: User, task_id: int) -> dict:
    """Tick a task off. Its time is logged, as in the app (D-092). Reopening a done task
    removes its logged time, so only the app does that (security review F1)."""
    task = _owned(session, user, LearningTask, task_id)
    learning.set_done(session, user, task, True)
    return task_info(task, _goal_titles(session, user))


def log_study_time(
    session: Session, user: User, goal_id: int, minutes: int, date: str | None = None
) -> dict:
    """Time studied for a goal without a task; counts for the week and the streak."""
    goal = _owned(session, user, LearningGoal, goal_id)
    if type(minutes) is not int or not MIN_SPENT_MINUTES <= minutes <= learning.MAX_TASK_MINUTES:
        raise Failed(f"minutes must be from {MIN_SPENT_MINUTES} to {learning.MAX_TASK_MINUTES}.")
    today = clock.today()
    day = _date(date, "date") or today
    if not today - dt.timedelta(days=30) <= day <= today:
        raise Failed("date must be today or at most 30 days ago.")
    log = learning.log_time(session, user, goal, minutes, day)
    return {"goal_id": goal.id, "date": log.date.isoformat(), "minutes": log.minutes}


# --- Many records: preview, apply, undo (D-193) -----------------------------------------------


class Shift(BaseModel):
    """Move every OPEN task by `days` (negative = earlier). Optional: one goal, a range."""

    model_config = ConfigDict(extra="forbid")
    days: int
    goal_id: int | None = None
    from_date: str | None = None
    to_date: str | None = None


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int
    due_date: str


class Update(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int
    title: str | None = None
    due_date: str | None = None
    minutes: int | None = None
    url: str | None = None


class Complete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int
    done: bool = True


class NewTask(BaseModel):
    """A new task; give goal_id, or goal_title (an existing goal or one in new_goals)."""

    model_config = ConfigDict(extra="forbid")
    title: str
    due_date: str
    minutes: int = 60
    url: str | None = None
    goal_id: int | None = None
    goal_title: str | None = None


def set_info(change_set: PendingChangeSet) -> dict:
    return {
        "change_set_id": change_set.id,
        "status": change_set.status,
        "changes": len(change_set.changes),
        "preview": change_set.summary,
    }


def propose_learning_changes(
    session: Session,
    user: User,
    shift: Shift | None = None,
    moves: list[Move] | None = None,
    updates: list[Update] | None = None,
    completes: list[Complete] | None = None,
    new_goals: list[str] | None = None,
    adds: list[NewTask] | None = None,
) -> dict:
    def plain(items):
        return [item.model_dump(exclude_unset=True) for item in items or []]

    try:
        change_set = changes.propose(
            session,
            user,
            shift=shift.model_dump(exclude_none=True) if shift else None,
            moves=plain(moves),
            updates=plain(updates),
            completes=plain(completes),
            new_goals=list(new_goals or []),
            adds=[item.model_dump(exclude_none=True) for item in adds or []],
        )
    except ChangeSetError as error:
        raise Failed(str(error)) from None
    return set_info(change_set) | {
        "next_step": "Show the preview to the user. Call apply_changes only after they say "
        "yes; it expires in 60 minutes. discard_changes drops it."
    }


def apply_changes(session: Session, user: User, change_set_id: int) -> dict:
    try:
        result = changes.apply(session, user, change_set_id)
    except ChangeSetError as error:
        raise Failed(str(error)) from None
    return {
        "change_set_id": result.change_set.id,
        "applied": result.done,
        "skipped_changed_in_between": result.skipped,
        "undo": "undo_changes can take this back within 24 hours.",
    }


def undo_changes(session: Session, user: User, change_set_id: int) -> dict:
    try:
        result = changes.undo(session, user, change_set_id)
    except ChangeSetError as error:
        raise Failed(str(error)) from None
    return {
        "change_set_id": result.change_set.id,
        "undone": result.done,
        "left_alone_changed_since": result.skipped,
    }


def discard_changes(session: Session, user: User, change_set_id: int) -> dict:
    try:
        return set_info(changes.discard(session, user, change_set_id))
    except ChangeSetError as error:
        raise Failed(str(error)) from None


DESCRIPTIONS = {
    "get_learning_plan": "The learning plan: goals with progress, overdue tasks, today's "
    "tasks, the next 7 days, streak and this week's hours.",
    "list_tasks": "List tasks sorted by date. Filter by goal_id, status (open/done/all), "
    "from_date/to_date (YYYY-MM-DD) or text in the title. At most 200 per call; page with "
    "offset.",
    "create_learning_goal": "Create one learning goal (optional target_date YYYY-MM-DD and "
    "weekly_hours).",
    "add_task": "Add one task to a goal: title, due_date (YYYY-MM-DD, 30 days ago to 2 "
    "years ahead), minutes (5-600), optional url. For more than two tasks use "
    "propose_learning_changes.",
    "update_task": "Change one task's title, due_date, minutes or url (an empty url "
    "removes the link). A done task keeps its date. For more than two tasks use "
    "propose_learning_changes.",
    "set_task_done": "Mark one task done. A done task can only be reopened in the Unfurl app.",
    "log_study_time": "Log study time for a goal without a task (minutes 1-600, date today "
    "or up to 30 days ago).",
    "propose_learning_changes": "Prepare changes to many tasks WITHOUT making them: shift "
    "open tasks by N days (optionally one goal or a date range), moves, updates, completes, "
    "new_goals and adds. Returns a preview and a change_set_id. Show the preview to the "
    "user; nothing changes until apply_changes.",
    "apply_changes": "Make the changes of a preview, only after the user said yes. Rows "
    "the user changed since the preview are skipped.",
    "undo_changes": "Take back an applied change set (within 24 hours). Rows changed since "
    "are left alone; tasks it added are removed only if untouched.",
    "discard_changes": "Drop a preview without applying it.",
}
TOOLS = [
    (get_learning_plan, READ),
    (list_tasks, READ),
    (create_learning_goal, WRITE),
    (add_task, WRITE),
    (update_task, WRITE),
    (set_task_done, WRITE),
    (log_study_time, WRITE),
    (propose_learning_changes, WRITE),  # saves a preview; changes no plan data
    (apply_changes, BULK),
    (undo_changes, BULK),
    (discard_changes, WRITE),
]


# --- The server -------------------------------------------------------------------------------


def find_user(session: Session, email: str) -> User:
    user = session.exec(select(User).where(User.email == normalize_email(email))).first()
    if user is None:
        raise SystemExit("No Unfurl user with that email.")
    return user


def build_server(engine: Engine, user_id: int) -> MCPServer:
    """The MCP server for one user. Each tool call gets its own database session and
    reads the user again (the account may have been deleted meanwhile)."""
    server = MCPServer(name="unfurl", title="Unfurl", instructions=INSTRUCTIONS)

    @contextmanager
    def unit() -> Iterator[tuple[Session, User]]:
        with Session(engine) as session:
            user = session.get(User, user_id)
            if user is None:
                raise Failed("This Unfurl account no longer exists.")
            # No web request here: use the user's time zone by hand (D-186, D-191).
            with clock.use_timezone(user.timezone):
                yield session, user

    def tool(fn, annotations: ToolAnnotations, description: str):
        """Register fn(session, user, **args) as an MCP tool with the same arguments."""

        signature = inspect.signature(fn)
        params = list(signature.parameters.values())[2:]  # without session and user

        @functools.wraps(fn)
        def call(**kwargs):
            with unit() as (session, user):
                return fn(session, user, **kwargs)

        call.__signature__ = signature.replace(parameters=params)
        call.__annotations__ = {p.name: p.annotation for p in params} | {"return": dict}
        server.add_tool(call, name=fn.__name__, description=description, annotations=annotations)

    for fn, annotations in TOOLS:
        tool(fn, annotations, DESCRIPTIONS[fn.__name__])
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Unfurl's local MCP server (stdio).")
    parser.add_argument("--email", required=True, help="the Unfurl account to work for")
    args = parser.parse_args(argv)
    # stdout carries the protocol; every log line goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

    from app.db import engine

    with Session(engine) as session:
        user_id = find_user(session, args.email).id
    build_server(engine, user_id).run("stdio")


if __name__ == "__main__":
    main()
