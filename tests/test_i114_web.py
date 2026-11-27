"""Learning page: task counts on the tabs and goals, "Show archived (n)" and
Unarchive (D-201, D-204, #114)."""

import datetime as dt
import json

import pytest

from app.models import LearningGoal, LearningTask
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


def add_goal(session, user, title, **fields) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, **fields)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def add_task(session, goal, title, offset=0, **fields) -> LearningTask:
    task = LearningTask(
        user_id=goal.user_id,
        goal_id=goal.id,
        title=title,
        due_date=WED + dt.timedelta(days=offset),
        estimated_minutes=60,
        **fields,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def post(client, url):
    return client.post(url, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(client)})


def toast(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["toast"]["message"]


def goal_article(html: str, goal_id: int) -> str:
    return html.split(f'data-goal="{goal_id}"', 1)[1].split("</article>", 1)[0]


@pytest.fixture
def plan(session, user):
    """Python: overdue, today, in 3 days, in 20 days, one done, one deleted.
    German: one task today, then archived."""
    python = add_goal(session, user, "Python")
    add_task(session, python, "Overdue", -2)
    add_task(session, python, "Today", 0)
    add_task(session, python, "Soon", 3)
    add_task(session, python, "Later", 20)
    add_task(session, python, "Done", -1, done=True, done_at=NOW)
    add_task(session, python, "Deleted", 0, deleted_at=NOW)
    german = add_goal(session, user, "German")
    add_task(session, german, "German today", 0)
    add_task(session, german, "German done", -1, done=True, done_at=NOW)
    german.archived_at = NOW
    session.add(german)
    session.commit()
    return python, german


# --- Counts ------------------------------------------------------------------------------------


def test_every_tab_shows_its_count(auth_client, plan):
    html = auth_client.get("/learning").text

    for label in ("Today (1)", "Next 7 days (2)", "All tasks (4)", "Catch-up (1)", "Done (1)"):
        assert label in html, label


def test_tab_counts_follow_the_goal_filter(auth_client, session, user, plan):
    python, _ = plan
    rust = add_goal(session, user, "Rust")
    add_task(session, rust, "Rust today", 0)

    html = auth_client.get(f"/learning?goal={rust.id}").text

    assert "Today (1)" in html and "All tasks (1)" in html and "Done (0)" in html
    assert "Today (2)" in auth_client.get("/learning").text  # both goals


def test_each_goal_shows_done_and_total_tasks(auth_client, session, user, plan):
    python, _ = plan
    empty = add_goal(session, user, "Empty")

    html = auth_client.get("/learning").text

    assert "1/5 tasks" in goal_article(html, python.id)  # the deleted one isn't counted
    assert "tasks" not in goal_article(html, empty.id).split("h logged", 1)[1].split("<", 1)[0]


# --- Archived goals ---------------------------------------------------------------------------


def test_no_archived_section_without_archived_goals(auth_client, session, user):
    add_goal(session, user, "Python")

    assert "data-archived-goals" not in auth_client.get("/learning").text


def test_archived_goals_are_listed_apart(auth_client, plan):
    _, german = plan

    html = auth_client.get("/learning").text

    assert f'data-goal="{german.id}"' not in html
    assert "German today" not in html
    archived = html.split("data-archived-goals", 1)[1]
    assert "Show archived (1)" in archived
    assert "German" in archived and "2 tasks" in archived
    assert f'hx-post="/learning/goals/{german.id}/unarchive"' in archived


def test_unarchive_brings_the_goal_and_its_tasks_back(auth_client, session, user, plan):
    _, german = plan
    german.status = "paused"
    session.add(german)
    session.commit()

    response = post(auth_client, f"/learning/goals/{german.id}/unarchive")

    assert response.status_code == 204
    assert toast(response) == "Goal unarchived."
    session.refresh(german)
    assert (german.archived_at, german.status) == (None, "paused")
    assert "German today" in [t.title for t in learning.tasks(session, user)]
    assert "data-archived-goals" not in auth_client.get("/learning").text


def test_unarchive_of_a_live_goal_only_says_so(auth_client, plan):
    python, _ = plan

    response = post(auth_client, f"/learning/goals/{python.id}/unarchive")

    assert response.status_code == 204
    assert toast(response) == "This goal isn't archived."


def test_unarchive_of_another_users_goal_is_404(auth_client, session):
    other = create_user(session, email="other@example.com", password="x" * 12, full_name="O")
    theirs = add_goal(session, other, "Theirs", archived_at=NOW)

    for goal_id in (theirs.id, 999999, "abc", 0):
        assert post(auth_client, f"/learning/goals/{goal_id}/unarchive").status_code == 404
    session.refresh(theirs)
    assert theirs.archived_at is not None


def test_unarchive_needs_the_csrf_token(auth_client, plan):
    _, german = plan

    assert auth_client.post(f"/learning/goals/{german.id}/unarchive").status_code == 403


def test_unarchive_needs_a_login(client, session, user):
    goal = add_goal(session, user, "G", archived_at=NOW)

    response = client.post(
        f"/learning/goals/{goal.id}/unarchive",
        headers={"X-CSRF-Token": csrf_token(client)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_unarchive_is_rate_limited(auth_client, plan):
    _, german = plan

    codes = [
        post(auth_client, f"/learning/goals/{german.id}/unarchive").status_code for _ in range(61)
    ]

    assert codes[60] == 429
