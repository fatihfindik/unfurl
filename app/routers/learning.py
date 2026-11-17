"""Learning page (`/learning`): summary cards, the plan and the goals (SPEC §6.10, M10).

The rules are in app/services/learning_service.py; this module only reads the
request, calls the service and turns the results into words for the templates.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from app.db import get_session
from app.models import User
from app.models.learning import WEEKDAYS
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import require_user
from app.templating import is_htmx, templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]

VIEW_TITLES = {"today": "Today", "week": "Next 7 days", "all": "All tasks", "catchup": "Catch-up"}
DAY_NAMES = dict(zip(WEEKDAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), strict=True))
GOAL_COLORS = 6  # app/templates/learning/_parts.html has one class pair per color (D-096)


def duration_text(minutes: int) -> str:
    """25 -> "25 min", 60 -> "1 h", 90 -> "1.5 h"."""
    if minutes < 60:
        return f"{minutes} min"
    hours = round(minutes / 60, 1)
    return f"{hours:g} h"


def hours_text(minutes: int) -> str:
    """Logged time in hours with at most one decimal: 75 -> "1.3"."""
    return f"{round(minutes / 60, 1):g}"


def day_text(day: dt.date, today: dt.date) -> str:
    """When a task is due: "Today", "Tomorrow", "Yesterday" or "Thu, 19 Nov"."""
    names = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}
    return names.get((day - today).days, day.strftime("%a, %-d %b"))


def rest_days_text(rest_days: list[str]) -> str:
    """The note on the Streak card (D-090)."""
    if not rest_days:
        return "No rest days. Every day counts."
    if set(rest_days) == {"sat", "sun"}:
        return "Weekends are rest days. Your streak is safe."
    names = ", ".join(DAY_NAMES[day] for day in WEEKDAYS if day in rest_days)
    return f"Rest days: {names}. Your streak is safe."


def task_row(task, today: dt.date, goal_info: dict) -> dict:
    goal = goal_info.get(task.goal_id, {"title": "Goal", "color": 0})
    when = day_text(task.due_date, today)
    if not task.done and task.due_date < today:
        when = f"Was due {when}" if when != "Yesterday" else "Was due yesterday"
    return {
        "id": task.id,
        "title": task.title,
        "done": task.done,
        "goal": goal["title"],
        "color": goal["color"],
        "when": when,
        "duration": duration_text(task.estimated_minutes),
        "url": task.url,
    }


def page_context(session: Session, user: User, view: str) -> dict:
    """Everything the page shows. The tasks are read once and split in Python."""
    today = clock.today()
    prefs = learning.preferences(session, user)
    week = learning.this_week(session, user)
    goals = learning.goals(session, user)
    resources = learning.resources_by_goal(session, user)
    tasks = learning.tasks(session, user)
    minutes = learning.minutes_by_goal(session, user)
    plan = learning.build_plan(tasks, view, today)

    goal_info = {}
    goal_cards = []
    for position, goal in enumerate(goals):
        color = position % GOAL_COLORS
        goal_info[goal.id] = {"title": goal.title, "color": color}
        resource = resources.get(goal.id)
        goal_tasks = [task for task in tasks if task.goal_id == goal.id]
        progress = ""
        if resource is not None and resource.units_total:
            progress = f"{resource.unit_label} {resource.units_done} / {resource.units_total}"
        meta = [f"{hours_text(minutes.get(goal.id, 0))} h logged"]
        if goal.target_date:
            meta.append(f"target {goal.target_date.strftime('%b %Y')}")
        goal_cards.append(
            {
                "id": goal.id,
                "title": goal.title,
                "color": color,
                "pct": learning.progress_pct(goal, resource, goal_tasks),
                "resource": resource,
                "progress": progress,
                "meta": " · ".join(meta),
            }
        )

    target_minutes = round(week.target_hours * 60)
    return {
        "view": plan.view,
        "views": [
            (key, f"{title} ({plan.catchup_count})" if key == "catchup" else title)
            for key, title in VIEW_TITLES.items()
        ],
        "groups": [
            {"label": group.label, "rows": [task_row(t, today, goal_info) for t in group.tasks]}
            for group in plan.groups
        ],
        "done_rows": [task_row(t, today, goal_info) for t in plan.done],
        "streak": learning.streak(session, user),
        "rest_note": rest_days_text(prefs.rest_days),
        "week_hours": hours_text(week.minutes),
        "week_target": f"{week.target_hours:g}" if target_minutes else "",
        "week_pct": min(100, round(100 * week.minutes / target_minutes)) if target_minutes else 0,
        "tasks_done": week.tasks_done,
        "tasks_total": week.tasks_total,
        "week_range": f"{week.start.strftime('%a, %-d %b')} – {week.end.strftime('%a, %-d %b')}",
        "goal_cards": goal_cards,
    }


@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request, session: DbSession, user: RequiredUser, view: str = "today"):
    context = page_context(session, user, view)
    if is_htmx(request):
        # A tab was picked, or the page asks again after a change: only the region.
        return templates.TemplateResponse(request, "learning/_region.html", context)
    return templates.TemplateResponse(request, "learning/index.html", context)
