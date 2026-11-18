"""Learning page (`/learning`): summary cards, the plan and the goals (SPEC §6.10, M10).

The rules are in app/services/learning_service.py; this module only reads the
request, calls the service and turns the results into words for the templates.
"""

import datetime as dt
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError
from sqlmodel import Session, select
from starlette.datastructures import FormData

from app.db import get_session
from app.models import LearningGoal, LearningTask, User
from app.models.learning import UNIT_LABELS, WEEKDAYS
from app.schemas.learning import (
    GOAL_STATUS_LABELS,
    DoneTaskEditForm,
    GoalEditForm,
    GoalForm,
    TaskEditForm,
    TaskForm,
    check_rest_days,
    check_task_date,
)
from app.schemas.profile import form_errors
from app.security import limiter
from app.services import clock, undo_service
from app.services import learning_service as learning
from app.services import notification_service as notifications
from app.services.auth_service import require_user
from app.templating import is_htmx, templates

# Every page here needs a logged-in user; without one the visitor goes to /login.
router = APIRouter(dependencies=[Depends(require_user)])

DbSession = Annotated[Session, Depends(get_session)]
RequiredUser = Annotated[User, Depends(require_user)]


async def read_form_data(request: Request) -> FormData:
    """The submitted form. An async dependency, so the routes can stay normal (sync)
    functions that FastAPI runs in a worker thread, next to the database calls."""
    return await request.form()


SubmittedForm = Annotated[FormData, Depends(read_form_data)]

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


def task_row(task, today: dt.date, goal_info: dict, spent: dict | None = None) -> dict:
    """One task for the templates. `spent`: minutes logged per task; a done task shows
    the time really spent (D-117), an open one its estimate."""
    goal = goal_info.get(task.goal_id, {"title": "Goal", "color": 0})
    minutes = task.estimated_minutes
    if task.done and spent:
        minutes = spent.get(task.id, minutes)
    when = day_text(task.due_date, today)
    if not task.done and task.due_date < today:
        when = f"Was due {when}" if when != "Yesterday" else "Was due yesterday"
    return {
        "id": task.id,
        "title": task.title,
        "done": task.done,
        "due": task.due_date.isoformat(),  # the reschedule form starts with it
        "goal": goal["title"],
        "color": goal["color"],
        "when": when,
        "duration": duration_text(minutes),
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
    spent = learning.minutes_by_task(session, user)
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
                "status": goal.status,  # Paused / Done get a badge (D-119)
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
        "done_rows": [task_row(t, today, goal_info, spent) for t in plan.done],
        "streak": learning.streak(session, user),
        "rest_note": rest_days_text(prefs.rest_days),
        "week_hours": hours_text(week.minutes),
        "week_target": f"{week.target_hours:g}" if target_minutes else "",
        "week_pct": min(100, round(100 * week.minutes / target_minutes)) if target_minutes else 0,
        "tasks_done": week.tasks_done,
        "tasks_total": week.tasks_total,
        "week_range": f"{week.start.strftime('%a, %-d %b')} – {week.end.strftime('%a, %-d %b')}",
        "goal_cards": goal_cards,
        "rest_days": prefs.rest_days,  # the rest days modal (D-090)
        "next_reminder": notifications.next_reminder(session, user, clock.now()),  # D-115
    }


@router.get("/learning", response_class=HTMLResponse)
def learning_page(request: Request, session: DbSession, user: RequiredUser, view: str = "today"):
    context = page_context(session, user, view)
    if is_htmx(request):
        # A tab was picked, or the page asks again after a change: only the region.
        return templates.TemplateResponse(request, "learning/_region.html", context)
    extra = goal_form_context() | {"day_choices": DAY_CHOICES}
    return templates.TemplateResponse(request, "learning/index.html", context | extra)


def page_events(*, toast: str, undo_url: str = "", undo_token: str = "", **more) -> dict:
    """The HX-Trigger header after a change (D-065): a toast, and the region reloads
    itself so the cards, the plan and the goals show the new numbers."""
    events = {
        "toast": {"message": toast, "undo_url": undo_url, "undo_token": undo_token},
        "learning-changed": True,
    } | more
    return {"HX-Trigger": json.dumps(events)}


# --- New goal (D-093) ---------------------------------------------------------------------

UNIT_CHOICES = [(label, f"{label}s") for label in UNIT_LABELS]  # "Week" shown as "Weeks"


def too_many(what: str) -> str:
    """The message when a limit of D-094 is reached ("goals" or "tasks")."""
    limit = learning.MAX_GOALS if what == "goals" else learning.MAX_TASKS
    return f"You have reached the limit of {limit} {what}."


def goal_form_context(values: dict | None = None, errors: dict | None = None) -> dict:
    return {
        "goal_values": values or {"plan_weekly": "yes"},  # the box starts ticked
        "goal_errors": errors or {},
        "unit_choices": UNIT_CHOICES,
    }


def goal_form(request: Request, values=None, errors=None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "learning/_goal_form.html", goal_form_context(values, errors)
    )


@router.post("/learning/goals", response_class=HTMLResponse)
@limiter.limit("60/minute")
def goal_create(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    """The "New goal" form in the modal (htmx). Errors come back as the form."""
    values = dict(data)
    try:
        form = GoalForm.model_validate(values)
    except ValidationError as exc:
        return goal_form(request, values, form_errors(exc))
    if errors := form.field_errors():
        return goal_form(request, values, errors)

    try:
        learning.create_goal(
            session,
            user,
            title=form.title,
            target_date=form.target_date,
            weekly_hours=form.weekly_hours,
            resource=form.resource,
            plan_weekly=form.plan_weekly,
        )
    except learning.TooMany:
        # Either the goals are full, or the planned tasks would pass the task limit.
        full = len(learning.goals(session, user)) >= learning.MAX_GOALS
        return goal_form(request, values, {"form": too_many("goals" if full else "tasks")})

    response = goal_form(request)
    response.headers.update(page_events(toast="Goal created.", **{"close-dialog": "new-goal"}))
    return response


# --- Add task -----------------------------------------------------------------------------

DURATION_CHOICES = [("25", "25 min"), ("45", "45 min"), ("60", "1 h"), ("120", "2 h")]


def task_form(request: Request, session: Session, user: User, values: dict, errors=None):
    goals = learning.goals(session, user)
    return templates.TemplateResponse(
        request,
        "learning/_task_form.html",
        {
            "values": values,
            "errors": errors or {},
            "goal_choices": [(str(goal.id), goal.title) for goal in goals],
            "duration_choices": DURATION_CHOICES,
        },
    )


@router.get("/learning/tasks/new", response_class=HTMLResponse)
def task_new(request: Request, session: DbSession, user: RequiredUser, goal: str = ""):
    """The "Add task" form, loaded into the modal when it opens. "+ Add task" on a
    goal card passes that goal; an unknown id simply preselects nothing."""
    values = {"goal_id": goal, "due_date": clock.today().isoformat(), "minutes": "45"}
    return task_form(request, session, user, values)


@router.post("/learning/tasks", response_class=HTMLResponse)
@limiter.limit("60/minute")
def task_create(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    values = dict(data)
    try:
        form = TaskForm.model_validate(values)
    except ValidationError as exc:
        return task_form(request, session, user, values, form_errors(exc))
    try:
        goal = learning.get_owned(session, user, LearningGoal, form.goal_id)
    except HTTPException:
        return task_form(request, session, user, values, {"goal_id": "Choose a goal."})

    try:
        learning.add_task(
            session, user, goal, title=form.title, due_date=form.due_date, minutes=form.minutes
        )
    except learning.TooMany:
        return task_form(request, session, user, values, {"form": too_many("tasks")})

    response = task_form(request, session, user, {})
    response.headers.update(page_events(toast="Task added.", **{"close-dialog": "add-task"}))
    return response


# --- Completing a task (D-092) ------------------------------------------------------------

UNDO_URL = "/learning/undo"


@router.post("/learning/tasks/{task_id}/done")
@limiter.limit("60/minute")
def task_done(
    request: Request, session: DbSession, user: RequiredUser, task_id: str, data: SubmittedForm
):
    """Tick a task off (done=yes) or back on (done=no). The wanted state is sent, so
    a double click doesn't flip it twice. Toast with Undo; the region reloads."""
    task = learning.get_owned(session, user, LearningTask, task_id)
    done = data.get("done") == "yes"
    learning.set_done(session, user, task, done)
    token = undo_service.make_learning_token(user, task.id, done)
    toast = "Task done." if done else "Task moved back to open."
    return Response(
        status_code=204, headers=page_events(toast=toast, undo_url=UNDO_URL, undo_token=token)
    )


@router.post("/learning/undo")
@limiter.limit("60/minute")
def learning_undo(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    """Undo of a tick: only if the task is still the user's and still as the action
    left it; otherwise nothing changes ("Nothing to undo.")."""
    found = undo_service.read_learning_token(user, str(data.get("token", "")))
    message = "Nothing to undo."
    if found is not None:
        task_id, done = found
        task = session.exec(
            select(LearningTask).where(LearningTask.id == task_id, LearningTask.user_id == user.id)
        ).first()
        if task is not None and task.done == done:
            learning.set_done(session, user, task, not done)
            message = "Undone."
    return Response(status_code=204, headers=page_events(toast=message))


# --- Rest days (D-090) --------------------------------------------------------------------

DAY_CHOICES = [(day, DAY_NAMES[day]) for day in WEEKDAYS]


def rest_days_form(request: Request, chosen: list[str], error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "learning/_rest_days_form.html",
        {"rest_days": chosen, "day_choices": DAY_CHOICES, "rest_error": error},
    )


@router.post("/learning/rest-days", response_class=HTMLResponse)
@limiter.limit("60/minute")
def rest_days_save(request: Request, session: DbSession, user: RequiredUser, data: SubmittedForm):
    chosen = [str(day) for day in data.getlist("rest_days")]
    try:
        days = check_rest_days(chosen)
    except ValueError as exc:
        return rest_days_form(request, [d for d in chosen if d in WEEKDAYS], str(exc))
    learning.set_rest_days(session, user, days)
    response = rest_days_form(request, days)
    response.headers.update(page_events(toast="Rest days saved.", **{"close-dialog": "rest-days"}))
    return response


# --- Reschedule and delete (D-095) --------------------------------------------------------

DELETE_UNDO_URL = "/undo/delete"  # app/routers/undo.py, shared with profile rows and CVs


@router.post("/learning/tasks/{task_id}/reschedule")
@limiter.limit("60/minute")
def task_reschedule(
    request: Request, session: DbSession, user: RequiredUser, task_id: str, data: SubmittedForm
):
    """A new date for an open task (the small form in the task's menu). Problems are
    said in the toast; nothing changes then."""
    task = learning.get_owned(session, user, LearningTask, task_id)
    if task.done:
        return Response(status_code=204, headers=page_events(toast="A done task keeps its date."))
    try:
        due_date = check_task_date(data.get("due_date") or None)
    except ValueError as exc:
        return Response(status_code=204, headers=page_events(toast=str(exc)))
    learning.reschedule(session, user, task, due_date)
    toast = f"Moved to {day_text(due_date, clock.today())}."
    return Response(status_code=204, headers=page_events(toast=toast))


# --- Editing a task (#39, D-116, D-117) ---------------------------------------------------

CHANGED_MEANWHILE = "This task changed in the meantime. Check the values and save again."


def task_edit_form(
    request: Request, session: Session, user: User, task: LearningTask, values=None, errors=None
) -> HTMLResponse:
    if values is None:  # first opened: the saved values
        values = {"title": task.title}
        if task.done:
            spent = learning.minutes_by_task(session, user).get(task.id)
            values["spent_minutes"] = str(spent or task.estimated_minutes)
        else:
            values |= {
                "due_date": task.due_date.isoformat(),
                "minutes": str(task.estimated_minutes),
            }
    return templates.TemplateResponse(
        request,
        "learning/_task_edit_form.html",
        {"task_id": task.id, "done": task.done, "values": values, "errors": errors or {}},
    )


@router.get("/learning/tasks/{task_id}/edit", response_class=HTMLResponse)
def task_edit(request: Request, session: DbSession, user: RequiredUser, task_id: str):
    """The "Edit task" form, loaded into its modal (app.js data-dialog-load)."""
    task = learning.get_owned(session, user, LearningTask, task_id)
    return task_edit_form(request, session, user, task)


@router.post("/learning/tasks/{task_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
def task_update(
    request: Request, session: DbSession, user: RequiredUser, task_id: str, data: SubmittedForm
):
    task = learning.get_owned(session, user, LearningTask, task_id)
    values = dict(data)
    # The form fits the task as it is now: ticked or unticked in another tab since the
    # form opened, nothing is saved and the right form comes back.
    if values.get("kind") != ("done" if task.done else "open"):
        return task_edit_form(request, session, user, task, errors={"form": CHANGED_MEANWHILE})

    try:
        if task.done:
            done_form = DoneTaskEditForm.model_validate(values)
        else:
            open_form = TaskEditForm.model_validate(values, context={"saved": task.due_date})
    except ValidationError as exc:
        return task_edit_form(request, session, user, task, values, form_errors(exc))

    if task.done:
        learning.update_done_task(
            session, user, task, title=done_form.title, spent_minutes=done_form.spent_minutes
        )
    else:
        learning.update_task(
            session,
            user,
            task,
            title=open_form.title,
            due_date=open_form.due_date,
            minutes=open_form.minutes,
        )
    response = task_edit_form(request, session, user, task)
    response.headers.update(page_events(toast="Task updated.", **{"close-dialog": "edit-task"}))
    return response


@router.post("/learning/tasks/{task_id}/delete")
@limiter.limit("60/minute")
def task_delete(request: Request, session: DbSession, user: RequiredUser, task_id: str):
    task = learning.get_owned(session, user, LearningTask, task_id)
    copy_id = learning.delete_task(session, user, task)
    token = undo_service.make_delete_token(user, copy_id)
    return Response(
        status_code=204,
        headers=page_events(toast="Task deleted.", undo_url=DELETE_UNDO_URL, undo_token=token),
    )


# --- Editing a goal (#39, D-118, D-119) ---------------------------------------------------


def goal_edit_form(request: Request, goal: LearningGoal, values=None, errors=None) -> HTMLResponse:
    if values is None:  # first opened: the saved values
        hours = goal.weekly_hours_target
        values = {
            "title": goal.title,
            "target_date": goal.target_date.isoformat() if goal.target_date else "",
            "weekly_hours": f"{hours:g}" if hours else "",
            "status": goal.status,
        }
    return templates.TemplateResponse(
        request,
        "learning/_goal_edit_form.html",
        {
            "goal_id": goal.id,
            "values": values,
            "errors": errors or {},
            "status_choices": list(GOAL_STATUS_LABELS.items()),
        },
    )


@router.get("/learning/goals/{goal_id}/edit", response_class=HTMLResponse)
def goal_edit(request: Request, session: DbSession, user: RequiredUser, goal_id: str):
    """The "Edit goal" form, loaded into its modal (app.js data-dialog-load)."""
    goal = learning.get_owned(session, user, LearningGoal, goal_id)
    return goal_edit_form(request, goal)


@router.post("/learning/goals/{goal_id}", response_class=HTMLResponse)
@limiter.limit("60/minute")
def goal_update(
    request: Request, session: DbSession, user: RequiredUser, goal_id: str, data: SubmittedForm
):
    goal = learning.get_owned(session, user, LearningGoal, goal_id)
    values = dict(data)
    try:
        form = GoalEditForm.model_validate(values, context={"saved": goal.target_date})
    except ValidationError as exc:
        return goal_edit_form(request, goal, values, form_errors(exc))
    learning.update_goal(
        session,
        user,
        goal,
        title=form.title,
        target_date=form.target_date,
        weekly_hours=form.weekly_hours,
        status=form.status,
    )
    response = goal_edit_form(request, goal)
    response.headers.update(page_events(toast="Goal updated.", **{"close-dialog": "edit-goal"}))
    return response


@router.get("/learning/goals/{goal_id}/delete", response_class=HTMLResponse)
def goal_delete_confirm(request: Request, session: DbSession, user: RequiredUser, goal_id: str):
    """The confirmation, loaded into its modal (app.js data-dialog-load)."""
    goal = learning.get_owned(session, user, LearningGoal, goal_id)
    task_count = sum(1 for task in learning.tasks(session, user) if task.goal_id == goal.id)
    return templates.TemplateResponse(
        request, "learning/_goal_delete.html", {"goal": goal, "task_count": task_count}
    )


@router.post("/learning/goals/{goal_id}/delete")
@limiter.limit("60/minute")
def goal_delete(request: Request, session: DbSession, user: RequiredUser, goal_id: str):
    goal = learning.get_owned(session, user, LearningGoal, goal_id)
    copy_id = learning.delete_goal(session, user, goal)
    token = undo_service.make_delete_token(user, copy_id)
    headers = page_events(
        toast="Goal deleted.",
        undo_url=DELETE_UNDO_URL,
        undo_token=token,
        **{"close-dialog": "delete-goal"},
    )
    return Response(status_code=204, headers=headers)
