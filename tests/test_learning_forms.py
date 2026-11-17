"""New goal and Add task (SPEC §6.10, D-093, D-094)."""

import datetime as dt
import json

import pytest
from sqlmodel import select

from app.models import LearningGoal, LearningResource, LearningTask
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


def post(client, url, **fields):
    return client.post(
        url, data=fields, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)}
    )


def post_goal(client, **fields):
    fields.setdefault("title", "Python foundations")
    return post(client, "/learning/goals", **fields)


def goals(session, user):
    return session.exec(select(LearningGoal).where(LearningGoal.user_id == user.id)).all()


def tasks(session, user):
    return session.exec(select(LearningTask).where(LearningTask.user_id == user.id)).all()


def events(response) -> dict:
    return json.loads(response.headers.get("HX-Trigger", "{}"))


def error_of(html: str, field: str, prefix: str = "goal") -> str:
    marker = f'id="{prefix}-{field}-error"'
    assert marker in html, f"no error for {field}"
    return html.split(marker, 1)[1].split(">", 1)[1].split("<", 1)[0]


# --- The page -----------------------------------------------------------------------------


def test_page_has_the_buttons_and_modals(auth_client, session, user):
    html = auth_client.get("/learning").text

    assert 'data-dialog-open="new-goal"' in html
    assert '<dialog id="new-goal"' in html and '<dialog id="add-task"' in html
    assert 'name="plan_weekly"' in html and "checked" in html.split('name="plan_weekly"')[1][:80]
    # No goals yet: no "Add task" (a task needs a goal).
    assert 'data-dialog-load="add-task"' not in html

    goal = learning.create_goal(
        session, user, title="G", target_date=None, weekly_hours=None, resource=None,
        plan_weekly=False,
    )  # fmt: skip
    html = auth_client.get("/learning").text
    assert 'data-dialog-url="/learning/tasks/new"' in html
    assert f'data-dialog-url="/learning/tasks/new?goal={goal.id}"' in html


# --- New goal -----------------------------------------------------------------------------


def test_create_a_goal(auth_client, session, user):
    response = post_goal(auth_client, target_date="2027-02-28", weekly_hours="1,5")

    assert response.status_code == 200
    [goal] = goals(session, user)
    assert (goal.title, goal.target_date, goal.weekly_hours_target) == (
        "Python foundations", dt.date(2027, 2, 28), 1.5,
    )  # fmt: skip
    trigger = events(response)
    assert trigger["toast"]["message"] == "Goal created."
    assert trigger["learning-changed"] is True
    assert trigger["close-dialog"] == "new-goal"
    # The modal gets an empty form back.
    assert 'value="Python foundations"' not in response.text


def test_create_a_goal_with_a_resource_and_weekly_tasks(auth_client, session, user):
    post_goal(
        auth_client,
        weekly_hours="2",
        resource_name="CS50P",
        resource_url="https://cs50.harvard.edu/python/",
        unit_label="Week",
        units_total="3",
        plan_weekly="yes",
    )

    [resource] = session.exec(select(LearningResource)).all()
    assert (resource.name, resource.units_total, resource.user_id) == ("CS50P", 3, user.id)
    assert [(t.title, t.estimated_minutes) for t in tasks(session, user)] == [
        ("Week 1", 120), ("Week 2", 120), ("Week 3", 120),
    ]  # fmt: skip


def test_without_the_box_no_tasks_are_planned(auth_client, session, user):
    post_goal(auth_client, resource_name="Book", units_total="4")

    assert len(goals(session, user)) == 1
    assert tasks(session, user) == []


UNITS = "Enter a whole number from 1 to 52."
NEEDS_NAME = "Add the name of the course"
BAD_LINK = {"resource_name": "C", "resource_url": "javascript:alert(1)"}
LONG_LINK = {"resource_name": "C", "resource_url": "https://x/" + "a" * 500}


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({"title": ""}, "title", "This field is required."),
        ({"title": "x" * 201}, "title", "Use at most 200 characters."),
        ({"target_date": "2026-11-17"}, "target_date", "Pick a date between today"),
        ({"target_date": "2031-11-19"}, "target_date", "Pick a date between today"),
        ({"target_date": "soon"}, "target_date", "Use the format YYYY-MM-DD."),
        ({"weekly_hours": "0.4"}, "weekly_hours", "Enter hours from 0.5 to 40."),
        ({"weekly_hours": "40.5"}, "weekly_hours", "Enter hours from 0.5 to 40."),
        ({"weekly_hours": "lots"}, "weekly_hours", "Enter hours from 0.5 to 40."),
        ({"resource_name": "x" * 201}, "resource_name", "Use at most 200 characters."),
        (BAD_LINK, "resource_url", "Enter a full link"),
        (LONG_LINK, "resource_url", "Use at most 500"),
        ({"resource_name": "C", "unit_label": "Year"}, "unit_label", "Choose from the list."),
        ({"resource_name": "C", "units_total": "0"}, "units_total", UNITS),
        ({"resource_name": "C", "units_total": "53"}, "units_total", UNITS),
        ({"resource_name": "C", "units_total": "2.5"}, "units_total", UNITS),
        ({"resource_url": "https://example.com"}, "resource_name", NEEDS_NAME),
        ({"units_total": "4"}, "resource_name", NEEDS_NAME),
        ({"resource_name": "C", "plan_weekly": "yes"}, "units_total", "Add how many there are"),
    ],
)  # fmt: skip
def test_goal_form_errors(auth_client, session, user, fields, field, message):
    response = post_goal(auth_client, **fields)

    assert response.status_code == 200
    assert error_of(response.text, field).startswith(message)
    assert "HX-Trigger" not in response.headers
    assert goals(session, user) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "x" * 200, "target_date": "2026-11-18", "weekly_hours": "0.5"},
        {"target_date": "2031-11-18", "weekly_hours": "40"},
        {"resource_name": "C", "units_total": "1"},
        {"resource_name": "C", "units_total": "52", "plan_weekly": "yes"},
    ],
)
def test_goal_form_limits_that_are_allowed(auth_client, session, user, fields):
    post_goal(auth_client, **fields)

    assert len(goals(session, user)) == 1


def test_typed_values_come_back_after_an_error(auth_client):
    response = post_goal(auth_client, title="", resource_name="<b>CS50P</b>")

    assert 'value="&lt;b&gt;CS50P&lt;/b&gt;"' in response.text


def test_goal_limit(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(learning, "MAX_GOALS", 1)
    post_goal(auth_client)

    response = post_goal(auth_client)

    assert "You have reached the limit of 1 goals." in response.text
    assert len(goals(session, user)) == 1


def test_task_limit_when_planning(auth_client, session, user, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 2)

    response = post_goal(auth_client, resource_name="C", units_total="3", plan_weekly="yes")

    assert "You have reached the limit of" in response.text
    assert goals(session, user) == [] and tasks(session, user) == []


def test_new_goal_needs_the_csrf_token(auth_client, session, user):
    response = auth_client.post("/learning/goals", data={"title": "G"})

    assert response.status_code == 403
    assert goals(session, user) == []


def test_new_goal_needs_a_login(client):
    response = client.post(
        "/learning/goals",
        data={"title": "G"},
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_new_goal_is_rate_limited(auth_client):
    codes = [post_goal(auth_client, title=f"G{n}").status_code for n in range(61)]

    assert codes[:60] == [200] * 60
    assert codes[60] == 429


# --- Add task -----------------------------------------------------------------------------


@pytest.fixture
def goal(session, user) -> LearningGoal:
    return learning.create_goal(
        session, user, title="Python", target_date=None, weekly_hours=None, resource=None,
        plan_weekly=False,
    )  # fmt: skip


def test_task_form_lists_the_goals_and_picks_one(auth_client, goal):
    html = auth_client.get(f"/learning/tasks/new?goal={goal.id}").text

    assert f'<option value="{goal.id}" selected' in html
    assert 'value="2026-11-18"' in html


def test_task_form_without_goals(auth_client):
    html = auth_client.get("/learning/tasks/new").text

    assert "Create a goal first." in html
    assert 'name="title"' not in html


def test_task_form_ignores_another_users_goal(auth_client, session, goal):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    theirs = LearningGoal(user_id=other.id, title="Their goal")
    session.add(theirs)
    session.commit()

    html = auth_client.get(f"/learning/tasks/new?goal={theirs.id}").text

    assert "Their goal" not in html
    assert "selected" not in html.split('name="goal_id"', 1)[1].split("</select>", 1)[0]


def test_add_a_task(auth_client, session, user, goal):
    response = post(
        auth_client, "/learning/tasks",
        goal_id=str(goal.id), title="Lecture 4", due_date="2026-11-20", minutes="25",
    )  # fmt: skip

    [task] = tasks(session, user)
    assert (task.goal_id, task.title, task.due_date, task.estimated_minutes) == (
        goal.id, "Lecture 4", dt.date(2026, 11, 20), 25,
    )  # fmt: skip
    trigger = events(response)
    assert trigger["toast"]["message"] == "Task added."
    assert trigger["close-dialog"] == "add-task" and trigger["learning-changed"]


@pytest.mark.parametrize(
    ("fields", "field", "message"),
    [
        ({"goal_id": ""}, "goal_id", "Choose a goal."),
        ({"title": ""}, "title", "This field is required."),
        ({"title": "x" * 201}, "title", "Use at most 200 characters."),
        ({"due_date": ""}, "due_date", "Pick a date."),
        ({"due_date": "2026-10-18"}, "due_date", "Pick a date from 30 days ago"),
        ({"due_date": "2028-11-19"}, "due_date", "Pick a date from 30 days ago"),
        ({"minutes": "4"}, "minutes", "Enter minutes from 5 to 600."),
        ({"minutes": "601"}, "minutes", "Enter minutes from 5 to 600."),
        ({"minutes": ""}, "minutes", "Enter minutes from 5 to 600."),
    ],
)
def test_task_form_errors(auth_client, session, user, goal, fields, field, message):
    data = {"goal_id": str(goal.id), "title": "T", "due_date": "2026-11-18", "minutes": "45"}

    response = post(auth_client, "/learning/tasks", **(data | fields))

    assert error_of(response.text, field, prefix="task").startswith(message)
    assert tasks(session, user) == []


@pytest.mark.parametrize(("due_date", "minutes"), [("2026-10-19", "5"), ("2028-11-18", "600")])
def test_task_form_limits_that_are_allowed(auth_client, session, user, goal, due_date, minutes):
    post(
        auth_client, "/learning/tasks",
        goal_id=str(goal.id), title="T", due_date=due_date, minutes=minutes,
    )  # fmt: skip

    assert len(tasks(session, user)) == 1


def test_a_task_cannot_go_to_another_users_goal(auth_client, session, user, goal):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    theirs = LearningGoal(user_id=other.id, title="Their goal")
    session.add(theirs)
    session.commit()

    response = post(
        auth_client, "/learning/tasks",
        goal_id=str(theirs.id), title="T", due_date="2026-11-18", minutes="45",
    )  # fmt: skip

    assert error_of(response.text, "goal_id", prefix="task") == "Choose a goal."
    assert session.exec(select(LearningTask)).all() == []


def test_task_limit(auth_client, session, user, goal, monkeypatch):
    monkeypatch.setattr(learning, "MAX_TASKS", 0)

    response = post(
        auth_client, "/learning/tasks",
        goal_id=str(goal.id), title="T", due_date="2026-11-18", minutes="45",
    )  # fmt: skip

    assert "You have reached the limit of" in response.text


def test_add_task_needs_the_csrf_token(auth_client, session, user, goal):
    response = auth_client.post(
        "/learning/tasks",
        data={"goal_id": str(goal.id), "title": "T", "due_date": "2026-11-18", "minutes": "45"},
    )

    assert response.status_code == 403
    assert tasks(session, user) == []


def test_task_form_needs_a_login(client):
    response = client.get("/learning/tasks/new", follow_redirects=False)

    assert response.status_code == 303
