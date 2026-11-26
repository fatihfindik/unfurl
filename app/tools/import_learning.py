"""Import learning goals and tasks for one user from a JSON file, once (D-186, #101).

    python -m app.tools.import_learning --email you@example.com < tasks.json           # dry run
    python -m app.tools.import_learning --email you@example.com --apply < tasks.json   # write

The file:
    {"goals": [{"title": "1 · Foundations",
                "tasks": [{"title": "...", "due_date": "2026-11-02", "minutes": 120,
                           "url": "https://...", "done": false}]}]}

Without --apply nothing is written. With it, everything is written in one transaction,
or nothing: every check runs before the first row is added. A goal whose title the
user already has stops the import, so running it twice doesn't copy everything twice.
"""

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from typing import TextIO

from sqlmodel import Session, select

from app.models.learning import LearningGoal, LearningTask
from app.models.profile import SHORT_TEXT, URL_TEXT
from app.models.user import User, normalize_email
from app.schemas.learning import MIN_TASK_MINUTES, check_task_date
from app.schemas.profile import check_url
from app.services import clock, learning_service
from app.services.learning_service import MAX_GOALS, MAX_TASK_MINUTES, MAX_TASKS

MAX_INPUT_BYTES = 1_000_000  # the real file is about 60 KB
DONE_AT = dt.time(12)  # a done task counts as done at noon on its date (D-186)

GOAL_KEYS = {"title", "tasks"}
TASK_KEYS = {"title", "due_date", "minutes", "url", "done"}


class ImportFailed(Exception):
    """The file can't be imported; `problems` lists every reason, nothing was written."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass
class TaskIn:
    title: str
    due_date: dt.date
    minutes: int
    url: str | None
    done: bool


@dataclass
class GoalIn:
    title: str
    tasks: list[TaskIn] = field(default_factory=list)


def _title(value, where: str, problems: list[str], warnings: list[str]) -> str:
    """A title without outer spaces; a too long one is shortened with "…" (a warning)."""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: the title is empty.")
        return ""
    title = value.strip()
    if len(title) > SHORT_TEXT:
        title = title[: SHORT_TEXT - 1] + "…"
        warnings.append(f"{where}: the title was shortened to {SHORT_TEXT} characters.")
    return title


def _task(raw, where: str, problems: list[str], warnings: list[str]) -> TaskIn | None:
    if not isinstance(raw, dict):
        problems.append(f"{where}: must be an object.")
        return None
    if unknown := sorted(set(raw) - TASK_KEYS):
        problems.append(f"{where}: unknown fields {', '.join(unknown)}.")
    title = _title(raw.get("title"), where, problems, warnings)

    # The same rules as the task form (D-094): 30 days back to 2 years ahead.
    due_date = None
    try:
        raw_date = raw.get("due_date")
        # Only text: fromisoformat would also take the number 20261102 (security review).
        if raw_date is not None and not isinstance(raw_date, str):
            raise ValueError("Use the format YYYY-MM-DD.")
        due_date = check_task_date(raw_date)
    except ValueError as error:
        problems.append(f"{where}: {error}")

    minutes = raw.get("minutes")
    # bool is a kind of int in Python, so True would pass as 1 without this check.
    if type(minutes) is not int or not MIN_TASK_MINUTES <= minutes <= MAX_TASK_MINUTES:
        problems.append(f"{where}: minutes must be from {MIN_TASK_MINUTES} to {MAX_TASK_MINUTES}.")

    url = raw.get("url")
    if url == "":
        url = None  # an empty link is no link; false or 0 is an error below
    if url is not None:
        try:
            if not isinstance(url, str) or len(url) > URL_TEXT:
                raise ValueError(f"the link must be text of at most {URL_TEXT} characters.")
            check_url(url)
        except ValueError as error:
            problems.append(f"{where}: {error}")

    done = raw.get("done", False)
    if type(done) is not bool:
        problems.append(f"{where}: done must be true or false.")
    elif done and due_date is not None and due_date > clock.today():
        problems.append(f"{where}: a done task can't have a date in the future.")

    if title and due_date and type(minutes) is int and type(done) is bool:
        return TaskIn(title, due_date, minutes, url, done)
    return None


def parse(text: str) -> tuple[list[GoalIn], list[str]]:
    """The goals in the file and the warnings, or ImportFailed with every problem.
    Uses the user's time zone for the date rules, so call it inside clock.use_timezone."""
    if len(text.encode()) > MAX_INPUT_BYTES:
        raise ImportFailed([f"The file is larger than {MAX_INPUT_BYTES:,} bytes."])
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ImportFailed([f"The file is not valid JSON: {error}"]) from None
    except RecursionError:  # very deeply nested lists or objects
        raise ImportFailed(["The file is nested too deeply."]) from None
    if not isinstance(data, dict) or set(data) != {"goals"} or not isinstance(data["goals"], list):
        raise ImportFailed(['The file must be {"goals": [...]} and nothing else.'])

    problems: list[str] = []
    warnings: list[str] = []
    goals: list[GoalIn] = []
    for g, raw_goal in enumerate(data["goals"], start=1):
        where = f"goal {g}"
        if not isinstance(raw_goal, dict):
            problems.append(f"{where}: must be an object.")
            continue
        if unknown := sorted(set(raw_goal) - GOAL_KEYS):
            problems.append(f"{where}: unknown fields {', '.join(unknown)}.")
        goal = GoalIn(_title(raw_goal.get("title"), where, problems, warnings))
        raw_tasks = raw_goal.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            problems.append(f"{where}: needs at least one task.")
            raw_tasks = []
        for t, raw_task in enumerate(raw_tasks, start=1):
            task = _task(raw_task, f"{where}, task {t}", problems, warnings)
            if task is not None:
                goal.tasks.append(task)
        goals.append(goal)

    if not goals:
        problems.append("The file has no goals.")
    # "Phase 1" and "phase 1" count as the same goal (casefold is lower() for all languages).
    titles = [goal.title.casefold() for goal in goals if goal.title]
    for title in sorted({t for t in titles if titles.count(t) > 1}):
        problems.append(f'The file has the goal "{title}" more than once.')
    if problems:
        raise ImportFailed(problems)
    return goals, warnings


def check_user(session: Session, user: User, goals: list[GoalIn]) -> None:
    """Stop before writing if a goal title exists already or a limit would be passed."""
    rows = session.exec(select(LearningGoal.title).where(LearningGoal.user_id == user.id))
    existing = {title.casefold() for title in rows}
    problems = [
        f'You already have a goal "{goal.title}". Nothing was imported.'
        for goal in goals
        if goal.title.casefold() in existing
    ]
    try:
        learning_service._check_room(session, user, LearningGoal, MAX_GOALS, adding=len(goals))
        total = sum(len(goal.tasks) for goal in goals)
        learning_service._check_room(session, user, LearningTask, MAX_TASKS, adding=total)
    except learning_service.TooMany:
        problems.append(f"This would pass the limit of {MAX_GOALS} goals or {MAX_TASKS} tasks.")
    if problems:
        raise ImportFailed(problems)


def write(session: Session, user: User, goals: list[GoalIn]) -> None:
    """Add every goal and task, then commit once. Any error rolls everything back."""
    start = len(learning_service.goals(session, user))  # new goals go after the old ones
    try:
        for offset, goal_in in enumerate(goals):
            goal = LearningGoal(user_id=user.id, title=goal_in.title, sort_order=start + offset)
            session.add(goal)
            session.flush()  # gives the goal its id for the tasks
            # Sorted by date; Python's sort keeps the file's order for the same date.
            ordered = sorted(goal_in.tasks, key=lambda task: task.due_date)
            for number, task in enumerate(ordered, start=1):
                session.add(
                    LearningTask(
                        user_id=user.id,
                        goal_id=goal.id,
                        title=task.title,
                        due_date=task.due_date,
                        estimated_minutes=task.minutes,
                        url=task.url,
                        done=task.done,
                        # No StudyLog: that time wasn't measured in Unfurl (D-186).
                        done_at=clock.to_utc(task.due_date, DONE_AT) if task.done else None,
                        sort_order=number,
                    )
                )
        session.commit()
    except Exception:
        session.rollback()
        raise


def counts(session: Session, user: User) -> tuple[int, int]:
    """How many goals and tasks the user has: shown so a wrong account is easy to spot."""
    goal_count = len(learning_service.goals(session, user))
    task_count = len(learning_service.tasks(session, user))
    return goal_count, task_count


def summary(
    user: User, before: tuple[int, int], goals: list[GoalIn], warnings: list[str], applied: bool
) -> str:
    """What was (or would be) imported. No email: the output may end up in a terminal log."""
    lines = [
        f"{'Imported' if applied else 'Dry run, nothing written'} for user #{user.id}"
        f" (before: {before[0]} goals, {before[1]} tasks):"
    ]
    for goal in goals:
        days = [task.due_date for task in goal.tasks]
        done = sum(task.done for task in goal.tasks)
        lines.append(
            f"  {goal.title}: {len(goal.tasks)} tasks ({done} done), {min(days)} to {max(days)}"
        )
    total = sum(len(goal.tasks) for goal in goals)
    lines.append(f"Total: {len(goals)} goals, {total} tasks.")
    lines += [f"Warning: {warning}" for warning in warnings]
    if not applied:
        lines.append("Run again with --apply to write it.")
    return "\n".join(lines)


def run(session: Session, email: str, text: str, apply: bool) -> str:
    """Check everything, write if `apply`, and return the summary (or ImportFailed)."""
    # Every query filters by this user's id (CLAUDE.md).
    user = session.exec(select(User).where(User.email == normalize_email(email))).first()
    if user is None:
        raise ImportFailed(["No user with that email."])
    # No request here, so the user's time zone is set by hand, like the scheduler does.
    with clock.use_timezone(user.timezone):
        goals, warnings = parse(text)
        check_user(session, user, goals)
        before = counts(session, user)
        if apply:
            write(session, user, goals)
        return summary(user, before, goals, warnings, apply)


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="the user who gets the goals")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    try:
        text = stdin.read(MAX_INPUT_BYTES + 1)  # one byte more, so a too large file is noticed
    except UnicodeDecodeError:
        print("Nothing was imported: the file is not UTF-8 text.", file=sys.stderr)
        return 1

    from app.db import engine  # here, not at the top: tests import this module freely

    with Session(engine) as session:
        try:
            print(run(session, args.email, text, args.apply))
        except ImportFailed as error:
            print("Nothing was imported:", file=sys.stderr)
            for problem in error.problems:
                print(f"  {problem}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
