"""Completing a task with toast + Undo, and the rest days (D-090, D-092).

M10 acceptance: ticking a task updates the hours and the counters."""

import datetime as dt
import json

import pytest
from sqlmodel import select

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog
from app.services import clock, undo_service
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


def events(response) -> dict:
    return json.loads(response.headers.get("HX-Trigger", "{}"))


@pytest.fixture
def goal(session, user) -> LearningGoal:
    return learning.create_goal(
        session,
        user,
        title="Python",
        target_date=None,
        weekly_hours=2.0,
        resource={"name": "CS50P", "units_total": 3},
        plan_weekly=True,
    )


@pytest.fixture
def task(session, user, goal) -> LearningTask:
    return learning.tasks(session, user)[0]  # "Week 1", due today, 120 minutes


def tick(client, task, done="yes"):
    return post(client, f"/learning/tasks/{task.id}/done", done=done)


# --- Ticking ------------------------------------------------------------------------------


def test_ticking_saves_and_answers_with_a_toast_and_undo(auth_client, session, task):
    response = tick(auth_client, task)

    assert response.status_code == 204
    session.refresh(task)
    assert task.done
    trigger = events(response)
    assert trigger["toast"]["message"] == "Task done."
    assert trigger["toast"]["undo_url"] == "/learning/undo"
    assert trigger["toast"]["undo_token"]
    assert trigger["learning-changed"] is True


def test_ticking_updates_hours_and_counters(auth_client, session, user, goal, task):
    # M10 acceptance criterion.
    before = auth_client.get("/learning").text
    assert "0 h" in before.split('data-card="week"', 1)[1][:400]

    tick(auth_client, task)
    html = auth_client.get("/learning").text

    week = html.split('data-card="week"', 1)[1].split("</section>", 1)[0]
    assert "2 h" in week and "/ 2 h" in week
    assert "1 <span" in html.split('data-card="tasks"', 1)[1][:400]
    streak = html.split('data-card="streak"', 1)[1].split('data-card="week"', 1)[0]
    assert "1 day" in streak
    goal_html = html.split(f'data-goal="{goal.id}"', 1)[1].split("</article>", 1)[0]
    assert "Week 1 / 3" in goal_html and "33%" in goal_html
    # A done task leaves the open list and shows in the Done tab (D-187).
    assert "Done (1)" in html
    assert f'data-task="{task.id}"' not in html
    assert f'data-task="{task.id}"' in auth_client.get("/learning?view=done").text


def test_the_same_tick_twice_is_one_tick(auth_client, session, task):
    tick(auth_client, task)
    tick(auth_client, task)

    assert len(session.exec(select(StudyLog)).all()) == 1
    assert session.exec(select(LearningResource)).one().units_done == 1


def test_unticking_from_the_done_tab(auth_client, session, task):
    tick(auth_client, task)
    html = auth_client.get("/learning?view=done").text
    assert f'hx-post="/learning/tasks/{task.id}/done"' in html
    assert "Mark as not done: Week 1" in html

    response = tick(auth_client, task, done="no")

    session.refresh(task)
    assert not task.done
    assert events(response)["toast"]["message"] == "Task moved back to open."
    assert session.exec(select(StudyLog)).all() == []


def test_another_users_task_is_404(auth_client, session):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    their_goal = LearningGoal(user_id=other.id, title="Theirs")
    session.add(their_goal)
    session.commit()
    theirs = LearningTask(user_id=other.id, goal_id=their_goal.id, title="T", due_date=WED)
    session.add(theirs)
    session.commit()

    response = tick(auth_client, theirs)

    assert response.status_code == 404
    session.refresh(theirs)
    assert not theirs.done


@pytest.mark.parametrize("task_id", ["abc", "0", "99999", "-1"])
def test_unknown_task_is_404(auth_client, task_id):
    response = post(auth_client, f"/learning/tasks/{task_id}/done", done="yes")

    assert response.status_code == 404


def test_ticking_needs_the_csrf_token(auth_client, session, task):
    response = auth_client.post(f"/learning/tasks/{task.id}/done", data={"done": "yes"})

    assert response.status_code == 403
    session.refresh(task)
    assert not task.done


def test_ticking_needs_a_login(client, session, task):
    response = client.post(
        f"/learning/tasks/{task.id}/done",
        data={"done": "yes"},
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(task)
    assert not task.done


# --- Undo ---------------------------------------------------------------------------------


def undo(client, token):
    return post(client, "/learning/undo", token=token)


def test_undo_takes_the_tick_back(auth_client, session, task):
    token = events(tick(auth_client, task))["toast"]["undo_token"]

    response = undo(auth_client, token)

    assert response.status_code == 204
    assert events(response)["toast"]["message"] == "Undone."
    assert events(response)["learning-changed"] is True
    session.refresh(task)
    assert not task.done
    assert session.exec(select(StudyLog)).all() == []
    assert session.exec(select(LearningResource)).one().units_done == 0


def test_undo_of_an_untick_ticks_again(auth_client, session, task):
    tick(auth_client, task)
    token = events(tick(auth_client, task, done="no"))["toast"]["undo_token"]

    undo(auth_client, token)

    session.refresh(task)
    assert task.done


def test_undo_does_nothing_if_the_task_changed_since(auth_client, session, task):
    token = events(tick(auth_client, task))["toast"]["undo_token"]
    tick(auth_client, task, done="no")

    response = undo(auth_client, token)

    assert events(response)["toast"]["message"] == "Nothing to undo."
    session.refresh(task)
    assert not task.done


def test_undo_works_once(auth_client, session, task):
    token = events(tick(auth_client, task))["toast"]["undo_token"]
    undo(auth_client, token)

    response = undo(auth_client, token)

    assert events(response)["toast"]["message"] == "Nothing to undo."


@pytest.mark.parametrize("token", ["", "nonsense", "x" * 5000])
def test_bad_tokens_do_nothing(auth_client, session, task, token):
    tick(auth_client, task)

    response = undo(auth_client, token)

    assert events(response)["toast"]["message"] == "Nothing to undo."
    session.refresh(task)
    assert task.done


def test_another_users_token_does_nothing(auth_client, session, user, task):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    tick(auth_client, task)

    response = undo(auth_client, undo_service.make_learning_token(other, task.id, True))

    assert events(response)["toast"]["message"] == "Nothing to undo."
    session.refresh(task)
    assert task.done


def test_an_application_undo_token_is_no_learning_token(auth_client, session, user, task):
    tick(auth_client, task)
    other_kind = undo_service.make_delete_token(user, task.id)

    response = undo(auth_client, other_kind)

    assert events(response)["toast"]["message"] == "Nothing to undo."


def test_an_old_token_does_nothing(auth_client, session, task, monkeypatch):
    token = events(tick(auth_client, task))["toast"]["undo_token"]
    monkeypatch.setattr(undo_service, "UNDO_SECONDS", -1)

    response = undo(auth_client, token)

    assert events(response)["toast"]["message"] == "Nothing to undo."


def test_undo_needs_the_csrf_token(auth_client, session, task):
    token = events(tick(auth_client, task))["toast"]["undo_token"]

    response = auth_client.post("/learning/undo", data={"token": token})

    assert response.status_code == 403
    session.refresh(task)
    assert task.done


# --- Rest days (D-090) --------------------------------------------------------------------


def save_days(client, days):
    return client.post(
        "/learning/rest-days",
        data={"rest_days": days},
        headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)},
    )


def test_page_has_the_rest_days_modal(auth_client):
    html = auth_client.get("/learning").text

    assert 'data-dialog-open="rest-days"' in html
    form = html.split('id="rest-days-form"', 1)[1].split("</form>", 1)[0]
    assert 'value="sat" checked' in form and 'value="sun" checked' in form
    assert 'value="mon" checked' not in form


def test_save_rest_days(auth_client, session, user):
    response = save_days(auth_client, ["fri", "mon"])

    assert response.status_code == 200
    assert learning.preferences(session, user).rest_days == ["mon", "fri"]
    trigger = events(response)
    assert trigger["toast"]["message"] == "Rest days saved."
    assert trigger["close-dialog"] == "rest-days" and trigger["learning-changed"]
    assert 'value="mon" checked' in response.text


def test_no_rest_days(auth_client, session, user):
    save_days(auth_client, [])

    assert learning.preferences(session, user).rest_days == []


def test_six_rest_days_are_allowed_seven_are_not(auth_client, session, user):
    save_days(auth_client, ["mon", "tue", "wed", "thu", "fri", "sat"])
    assert len(learning.preferences(session, user).rest_days) == 6

    response = save_days(auth_client, ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])

    assert "Keep at least one day to study." in response.text
    assert "HX-Trigger" not in response.headers
    assert len(learning.preferences(session, user).rest_days) == 6


def test_unknown_days_are_refused(auth_client, session, user):
    response = save_days(auth_client, ["mon", "<script>"])

    assert "Choose days from the list." in response.text
    assert "<script>" not in response.text
    assert learning.preferences(session, user).rest_days == ["sat", "sun"]


def test_rest_days_need_the_csrf_token(auth_client, session, user):
    response = auth_client.post("/learning/rest-days", data={"rest_days": ["mon"]})

    assert response.status_code == 403
    assert learning.preferences(session, user).rest_days == ["sat", "sun"]


def test_rest_days_are_per_user(auth_client, session, user):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    save_days(auth_client, ["wed"])

    assert learning.preferences(session, other).rest_days == ["sat", "sun"]
