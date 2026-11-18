"""Editing tasks and goals and correcting the time spent (#39, D-116…D-119)."""

import datetime as dt
import json

import pytest
from freezegun import freeze_time
from sqlmodel import select

from app.models import LearningGoal, LearningTask, StudyLog
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token

HTMX = {"HX-Request": "true"}
TODAY = dt.date(2026, 11, 18)  # a Wednesday

pytestmark = pytest.mark.usefixtures("frozen")


@pytest.fixture
def frozen():
    with freeze_time("2026-11-18 10:00:00"):  # 11:00 in Vienna
        yield


def goal_of(session, user, title="Python", hours=None, status="active") -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, weekly_hours_target=hours, status=status)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def task_of(session, user, goal, due=TODAY, minutes=45, title="Lecture 4") -> LearningTask:
    return learning.add_task(session, user, goal, title=title, due_date=due, minutes=minutes)


def post(client, url, **data):
    return client.post(url, data=data | {"csrf_token": csrf_token(client)}, headers=HTMX)


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def fresh(session, model, row_id):
    session.expire_all()
    return session.get(model, row_id)


# --- Editing an open task (D-116) -----------------------------------------------------------


def test_the_edit_form_shows_the_saved_values(auth_client, session, user):
    task = task_of(session, user, goal_of(session, user))

    html = auth_client.get(f"/learning/tasks/{task.id}/edit").text

    assert 'value="Lecture 4"' in html
    assert 'value="2026-11-18"' in html and 'value="45"' in html
    assert 'name="kind" value="open"' in html
    assert "Time spent" not in html


def test_an_open_task_is_saved(auth_client, session, user):
    task = task_of(session, user, goal_of(session, user))

    response = post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="open",
        title="Lecture 5",
        due_date="2026-11-25",
        minutes="90",
    )

    assert response.status_code == 200
    assert toast(response) == "Task updated."
    assert json.loads(response.headers["HX-Trigger"])["close-dialog"] == "edit-task"
    task = fresh(session, LearningTask, task.id)
    assert (task.title, task.due_date, task.estimated_minutes) == (
        "Lecture 5",
        dt.date(2026, 11, 25),
        90,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("title", "", "This field is required."),
        ("title", "x" * 201, ""),
        ("minutes", "4", "Enter minutes from 5 to 600."),
        ("minutes", "601", "Enter minutes from 5 to 600."),
        ("minutes", "abc", "Enter minutes from 5 to 600."),
        ("due_date", "2026-10-01", "Pick a date from 30 days ago to 2 years from now."),
        ("due_date", "tomorrow", "Use the format YYYY-MM-DD."),
    ],
)
def test_bad_values_show_an_error_and_save_nothing(
    auth_client, session, user, field, value, message
):
    task = task_of(session, user, goal_of(session, user))
    data = {"kind": "open", "title": "New", "due_date": "2026-11-20", "minutes": "30"} | {
        field: value
    }

    response = post(auth_client, f"/learning/tasks/{task.id}", **data)

    assert "HX-Trigger" not in response.headers
    assert f'id="edit-task-{field}-error"' in response.text
    assert message in response.text
    assert fresh(session, LearningTask, task.id).title == "Lecture 4"


def test_an_old_catch_up_task_can_get_a_new_title(auth_client, session, user):
    goal = goal_of(session, user)
    task = task_of(session, user, goal)
    task.due_date = TODAY - dt.timedelta(days=40)  # older than a new task may be
    session.add(task)
    session.commit()

    post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="open",
        title="Still to do",
        due_date=task.due_date.isoformat(),
        minutes="45",
    )

    task = fresh(session, LearningTask, task.id)
    assert task.title == "Still to do"
    assert task.due_date == TODAY - dt.timedelta(days=40)


# --- Correcting the time spent (D-117) ------------------------------------------------------


def done_task(session, user, goal, minutes=45) -> LearningTask:
    task = task_of(session, user, goal, minutes=minutes)
    return learning.set_done(session, user, task, True)


def logs(session, user, task) -> list[StudyLog]:
    session.expire_all()
    return list(
        session.exec(
            select(StudyLog).where(StudyLog.user_id == user.id, StudyLog.task_id == task.id)
        )
    )


def test_a_done_task_shows_title_and_time_spent(auth_client, session, user):
    task = done_task(session, user, goal_of(session, user))

    html = auth_client.get(f"/learning/tasks/{task.id}/edit").text

    assert "Time spent (minutes)" in html and 'value="45"' in html
    assert 'name="kind" value="done"' in html
    assert 'name="due_date"' not in html


def test_the_time_spent_is_corrected(auth_client, session, user):
    goal = goal_of(session, user, hours=5)
    task = done_task(session, user, goal)

    response = post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="done",
        title="Lecture 4",
        spent_minutes="90",
    )

    assert toast(response) == "Task updated."
    [log] = logs(session, user, task)
    assert log.minutes == 90
    html = auth_client.get("/learning").text
    assert "1.5 h" in html  # This week and the goal's "h logged"
    assert "1.5 h logged" in html


def test_a_done_row_shows_the_time_spent(auth_client, session, user):
    task = done_task(session, user, goal_of(session, user))
    post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="done",
        title="Lecture 4",
        spent_minutes="20",
    )

    html = auth_client.get("/learning?view=all").text

    done_section = html.split("Done (")[1]
    assert "20 min" in done_section and "45 min" not in done_section


@pytest.mark.parametrize("value", ["0", "601", "", "1.5"])
def test_a_bad_time_spent_is_refused(auth_client, session, user, value):
    task = done_task(session, user, goal_of(session, user))

    response = post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="done",
        title="Lecture 4",
        spent_minutes=value,
    )

    assert "Enter minutes from 1 to 600." in response.text
    assert logs(session, user, task)[0].minutes == 45


def test_a_short_time_spent_is_fine(auth_client, session, user):
    task = done_task(session, user, goal_of(session, user))

    post(
        auth_client, f"/learning/tasks/{task.id}", kind="done", title="Lecture 4", spent_minutes="1"
    )

    assert logs(session, user, task)[0].minutes == 1


def test_a_done_task_without_a_log_gets_one_on_its_day(auth_client, session, user):
    user.timezone = "Asia/Tokyo"
    session.add(user)
    session.commit()
    goal = goal_of(session, user)
    task = task_of(session, user, goal)
    task.done = True
    task.done_at = dt.datetime(2026, 11, 16, 20, 0, tzinfo=dt.UTC)  # 17 Nov in Tokyo
    session.add(task)
    session.commit()

    post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="done",
        title="Lecture 4",
        spent_minutes="30",
    )

    [log] = logs(session, user, task)
    assert (log.minutes, log.goal_id, log.date) == (30, goal.id, dt.date(2026, 11, 17))


def test_extra_logs_are_merged_into_one(session, user):
    goal = goal_of(session, user)
    task = done_task(session, user, goal)
    session.add(StudyLog(user_id=user.id, goal_id=goal.id, task_id=task.id, date=TODAY, minutes=10))
    session.commit()

    learning.update_done_task(session, user, task, title="Lecture 4", spent_minutes=50)

    assert [log.minutes for log in logs(session, user, task)] == [50]


def test_a_task_changed_in_another_tab_saves_nothing(auth_client, session, user):
    task = task_of(session, user, goal_of(session, user))
    learning.set_done(session, user, task, True)  # ticked off after the form opened

    response = post(
        auth_client,
        f"/learning/tasks/{task.id}",
        kind="open",
        title="Renamed",
        due_date="2026-11-20",
        minutes="30",
    )

    assert "This task changed in the meantime." in response.text
    assert 'name="kind" value="done"' in response.text  # the right form now
    assert "HX-Trigger" not in response.headers
    task = fresh(session, LearningTask, task.id)
    assert (task.title, task.due_date) == ("Lecture 4", TODAY)


def test_the_task_menu_has_edit(auth_client, session, user):
    task = task_of(session, user, goal_of(session, user))

    html = auth_client.get("/learning").text

    assert f'data-dialog-url="/learning/tasks/{task.id}/edit"' in html
    assert '<dialog id="edit-task"' in html


# --- Editing a goal (D-118, D-119) ----------------------------------------------------------


def test_the_goal_form_shows_the_saved_values(auth_client, session, user):
    goal = goal_of(session, user, hours=2.5)

    html = auth_client.get(f"/learning/goals/{goal.id}/edit").text

    assert 'value="Python"' in html and 'value="2.5"' in html
    assert '<option value="active" selected>Active</option>' in html


def test_a_goal_is_saved(auth_client, session, user):
    goal = goal_of(session, user, hours=2)

    response = post(
        auth_client,
        f"/learning/goals/{goal.id}",
        title="German B2",
        target_date="2027-06-30",
        weekly_hours="4.5",
        status="paused",
    )

    assert toast(response) == "Goal updated."
    goal = fresh(session, LearningGoal, goal.id)
    assert (goal.title, goal.target_date, goal.weekly_hours_target, goal.status) == (
        "German B2",
        dt.date(2027, 6, 30),
        4.5,
        "paused",
    )


def test_target_date_and_hours_can_be_cleared(auth_client, session, user):
    goal = goal_of(session, user, hours=3)
    goal.target_date = dt.date(2027, 1, 1)
    session.add(goal)
    session.commit()

    post(
        auth_client,
        f"/learning/goals/{goal.id}",
        title="Python",
        target_date="",
        weekly_hours="",
        status="active",
    )

    goal = fresh(session, LearningGoal, goal.id)
    assert goal.target_date is None and goal.weekly_hours_target is None


def test_a_passed_target_date_can_stay(auth_client, session, user):
    goal = goal_of(session, user)
    goal.target_date = TODAY - dt.timedelta(days=10)
    session.add(goal)
    session.commit()

    post(
        auth_client,
        f"/learning/goals/{goal.id}",
        title="Renamed",
        target_date=goal.target_date.isoformat(),
        status="done",
    )

    assert fresh(session, LearningGoal, goal.id).title == "Renamed"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("status", "", "Choose from the list."),
        ("status", "archived", "Choose from the list."),
        ("weekly_hours", "41", "Enter hours from 0.5 to 40."),
        ("target_date", "2026-11-01", "Pick a date between today and 5 years from now."),
        ("title", "", "This field is required."),
    ],
)
def test_bad_goal_values_are_refused(auth_client, session, user, field, value, message):
    goal = goal_of(session, user)
    data = {"title": "New", "target_date": "", "weekly_hours": "", "status": "active"} | {
        field: value
    }

    response = post(auth_client, f"/learning/goals/{goal.id}", **data)

    assert f'id="edit-goal-{field}-error"' in response.text
    assert message in response.text
    assert fresh(session, LearningGoal, goal.id).title == "Python"


def test_a_paused_goal_has_a_badge_and_no_weekly_target(auth_client, session, user):
    goal_of(session, user, title="Active one", hours=2)
    paused = goal_of(session, user, title="Paused one", hours=5)

    post(
        auth_client,
        f"/learning/goals/{paused.id}",
        title="Paused one",
        weekly_hours="5",
        status="paused",
    )

    html = auth_client.get("/learning").text
    assert "/ 2 h" in html  # only the active goal's hours
    assert "data-goal-status" in html and ">Paused</span>" in html
    assert f'data-dialog-url="/learning/goals/{paused.id}/edit"' in html


# --- Access ---------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/learning/tasks/1/edit", "/learning/goals/1/edit"])
def test_the_forms_need_a_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_saving_needs_the_csrf_token(auth_client, session, user):
    task = task_of(session, user, goal_of(session, user))

    response = auth_client.post(
        f"/learning/tasks/{task.id}", data={"kind": "open", "title": "X"}, headers=HTMX
    )

    assert response.status_code == 403


def test_another_users_task_and_goal_are_not_found(auth_client, session, user):
    other = create_user(session, email="o@example.com", password="x" * 12, full_name="O")
    goal = goal_of(session, other)
    task = task_of(session, other, goal)

    assert auth_client.get(f"/learning/tasks/{task.id}/edit").status_code == 404
    assert auth_client.get(f"/learning/goals/{goal.id}/edit").status_code == 404
    data = {"kind": "open", "title": "Mine", "due_date": "2026-11-20", "minutes": "30"}
    assert post(auth_client, f"/learning/tasks/{task.id}", **data).status_code == 404
    assert (
        post(auth_client, f"/learning/goals/{goal.id}", title="Mine", status="done").status_code
        == 404
    )
    assert fresh(session, LearningTask, task.id).title == "Lecture 4"
    assert fresh(session, LearningGoal, goal.id).status == "active"


@pytest.mark.parametrize("bad_id", ["abc", "0", "-1", "99999999999"])
def test_bad_ids_are_not_found(auth_client, bad_id):
    assert auth_client.get(f"/learning/tasks/{bad_id}/edit").status_code == 404
    assert auth_client.get(f"/learning/goals/{bad_id}/edit").status_code == 404


def test_saving_is_rate_limited(auth_client, session, user):
    goal = goal_of(session, user)
    token = csrf_token(auth_client)
    data = {"title": "G", "status": "active", "csrf_token": token}

    codes = [
        auth_client.post(f"/learning/goals/{goal.id}", data=data, headers=HTMX).status_code
        for _ in range(61)
    ]

    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_a_done_task_title_is_checked_too(auth_client, session, user):
    task = done_task(session, user, goal_of(session, user))

    response = post(
        auth_client, f"/learning/tasks/{task.id}", kind="done", title="x" * 201, spent_minutes="30"
    )

    assert 'id="edit-task-title-error"' in response.text
    assert fresh(session, LearningTask, task.id).title == "Lecture 4"
    assert clock.today() == TODAY
