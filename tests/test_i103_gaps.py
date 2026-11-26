"""Gaps in the #103 tests (D-187…D-189): the plan's view and goal filter with odd input,
other users' goals, htmx region and history requests, and ownership on the Done tab's
untick / edit / delete."""

import datetime as dt
import re

import pytest
from sqlmodel import select

from app.models import LearningTask
from app.services import learning_service as learning
from app.services.auth_service import create_user
from tests.helpers import csrf_token
from tests.test_learning_pages import NOW, add_goal, add_task, day, frozen, plan_card  # noqa: F401

VIEWS = ["today", "week", "all", "catchup", "done"]


def titles(html: str) -> list[str]:
    """Every task title in the plan card, open and done rows alike."""
    return re.findall(r"<span class=\"text-sm[^\"]*\">([^<]+)</span>", plan_card(html))


def region_tag(html: str) -> str:
    return html.split('id="learning-region"', 1)[1].split(">", 1)[0]


@pytest.fixture
def mine(session, user):
    """Jana's two goals, each with an open task today, an overdue one and a done one."""
    python = add_goal(session, user, title="Python")
    german = add_goal(session, user, title="German")
    for goal, short in ((python, "Py"), (german, "De")):
        add_task(session, goal, f"{short} today", day(0))
        add_task(session, goal, f"{short} overdue", day(-2))
        add_task(session, goal, f"{short} done", day(-1), done=True, done_at=NOW)
    return python, german


@pytest.fixture
def other(session):
    return create_user(session, email="other@example.test", password="x" * 12, full_name="O")


@pytest.fixture
def theirs(session, other):
    """Another user's goal with tasks in every tab, so a leak would show anywhere."""
    goal = add_goal(session, other, title="Their secret goal")
    add_task(session, goal, "Their today", day(0))
    add_task(session, goal, "Their overdue", day(-3))
    add_task(session, goal, "Their later", day(30))
    add_task(session, goal, "Their done", day(-1), done=True, done_at=NOW)
    return goal


def htmx(client, **more) -> dict:
    return {"HX-Request": "true", "X-CSRF-Token": csrf_token(client), **more}


# --- Logged out ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query", ["view=done&goal=1", "view=catchup&goal=2", "view=x&goal=abc", "goal=1&goal=2"]
)
def test_logged_out_with_filters_goes_to_login(client, query):
    response = client.get(f"/learning?{query}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("query", ["view=done&goal=1", "view=week&goal=2"])
def test_logged_out_htmx_gets_hx_redirect(client, query):
    response = client.get(
        f"/learning?{query}", headers={"HX-Request": "true"}, follow_redirects=False
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"].startswith("/login")
    assert "learning-region" not in response.text


# --- Another user's goal ------------------------------------------------------------------


@pytest.mark.parametrize("view", VIEWS)
def test_another_users_goal_leaks_nothing_in_any_view(auth_client, mine, theirs, view):
    unfiltered = auth_client.get(f"/learning?view={view}").text
    html = auth_client.get(f"/learning?view={view}&goal={theirs.id}").text

    assert "Their" not in html
    # The same as no filter: same rows and the same tab counts.
    assert titles(html) == titles(unfiltered)
    assert "Catch-up (2)" in html and "Done (2)" in html
    assert "goal=" not in region_tag(html)


@pytest.mark.parametrize("view", VIEWS)
def test_another_users_goal_in_a_region_request(auth_client, mine, theirs, view):
    response = auth_client.get(
        f"/learning?view={view}&goal={theirs.id}", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert "Their" not in response.text
    assert f"goal={theirs.id}" not in response.text


def test_add_task_form_with_another_users_goal_preselects_nothing(auth_client, mine, theirs):
    html = auth_client.get(f"/learning/tasks/new?goal={theirs.id}").text

    assert "Their secret goal" not in html
    goal_select = html.split('name="goal_id"', 1)[1].split("</select>", 1)[0]
    assert " selected" not in goal_select  # the duration select has its own default
    python, _ = mine
    assert f'<option value="{python.id}">Python</option>' in html


def test_add_task_form_with_own_goal_preselects_it(auth_client, mine):
    python, _ = mine
    html = auth_client.get(f"/learning/tasks/new?goal={python.id}").text

    assert f'<option value="{python.id}" selected>Python</option>' in html


# --- Repeated and odd parameters ----------------------------------------------------------


def test_repeated_goal_params_never_leak(auth_client, mine, theirs):
    python, german = mine
    for query in (
        f"goal={python.id}&goal={theirs.id}",
        f"goal={theirs.id}&goal={python.id}",
        f"goal={python.id}&goal={german.id}",
    ):
        response = auth_client.get(f"/learning?view=all&{query}")
        assert response.status_code == 200
        assert "Their" not in response.text
        # One goal filter at most, and only one of Jana's.
        kept = re.findall(r"goal=(\d+)", region_tag(response.text))
        assert set(kept) <= {str(python.id), str(german.id)}


def test_repeated_view_params_give_a_known_tab(auth_client, mine):
    response = auth_client.get("/learning?view=done&view=bogus")

    assert response.status_code == 200
    assert re.search(r'hx-get="/learning\?view=(today|week|all|catchup|done)"', response.text)


@pytest.mark.parametrize(
    "raw",
    [
        "x" * 10_000,
        "1" * 5_000,  # more digits than int() accepts by default
        "%00",
        "1;DROP TABLE learningtask",
        "1 OR 1=1",
        "1e3",
        "0x1",
        "١",  # Arabic-Indic digit one
        "../1",
        '"><script>alert(1)</script>',
    ],
)
def test_odd_goal_values_never_break_the_page(auth_client, mine, theirs, raw):
    response = auth_client.get("/learning", params={"view": "all", "goal": raw})

    assert response.status_code == 200
    assert "Their" not in response.text
    assert "<script>alert" not in response.text
    kept = re.findall(r"goal=([^\"&]*)", region_tag(response.text))
    assert all(value.isdigit() for value in kept)


@pytest.mark.parametrize(
    "raw", ["x" * 10_000, "DONE", "done ", "today\x00", "../done", "<b>week</b>", ""]
)
def test_odd_view_values_fall_back_to_today(auth_client, mine, raw):
    response = auth_client.get("/learning", params={"view": raw})

    assert response.status_code == 200
    assert 'hx-get="/learning?view=today"' in response.text
    assert "<b>week" not in response.text
    assert sorted(titles(response.text)) == ["De today", "Py today"]


# --- htmx region and history --------------------------------------------------------------


@pytest.mark.parametrize("view", VIEWS)
def test_region_response_keeps_the_filter(auth_client, mine, view):
    python, _ = mine
    response = auth_client.get(
        f"/learning?view={view}&goal={python.id}", headers={"HX-Request": "true"}
    )

    html = response.text
    assert html.lstrip().startswith('<div id="learning-region"')
    assert "<html" not in html
    assert f'hx-get="/learning?view={view}&amp;goal={python.id}"' in region_tag(html)
    assert all(t.startswith("Py") for t in titles(html))


def test_history_restore_returns_the_full_page(auth_client, mine):
    python, _ = mine
    response = auth_client.get(
        f"/learning?view=done&goal={python.id}",
        headers={"HX-Request": "true", "HX-History-Restore-Request": "true"},
    )

    assert "<html" in response.text
    assert re.search(r"<h1[^>]*>Learning</h1>", response.text)
    assert f'hx-get="/learning?view=done&amp;goal={python.id}"' in response.text
    assert titles(response.text) == ["Py done"]


# --- Goal states --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["paused", "done"])
def test_paused_or_done_goal_is_still_a_filter(auth_client, session, mine, status):
    python, _ = mine
    python.status = status
    session.add(python)
    session.commit()

    html = auth_client.get(f"/learning?view=all&goal={python.id}").text

    assert sorted(titles(html)) == ["Py overdue", "Py today"]
    assert f'<option value="{python.id}" selected>Python</option>' in html


def test_deleted_goal_falls_back_to_all_goals(auth_client, session, user, mine):
    python, _ = mine
    goal_id = python.id
    learning.delete_goal(session, user, python)

    html = auth_client.get(f"/learning?view=all&goal={goal_id}").text

    assert sorted(titles(html)) == ["De overdue", "De today"]
    assert "goal=" not in region_tag(html)
    assert "Catch-up (1)" in html


# --- Done tab: ordering -------------------------------------------------------------------


def test_done_tab_with_many_tasks_has_a_stable_order(auth_client, session, user):
    goal = add_goal(session, user)
    # 40 tasks: some share the same moment (newer id first), some have no done_at (last).
    tasks = []
    for number in range(40):
        done_at = None if number % 10 == 0 else NOW - dt.timedelta(hours=number // 3)
        tasks.append(add_task(session, goal, f"T{number}", day(-1), done=True, done_at=done_at))

    def order(task):
        stamp = task.done_at.timestamp() if task.done_at else float("-inf")
        return (-stamp, -task.id)

    expected = [task.title for task in sorted(tasks, key=order)]
    first = auth_client.get("/learning?view=done").text
    second = auth_client.get("/learning?view=done").text

    assert "Done (40)" in first
    assert titles(first) == expected
    assert titles(second) == expected


# --- Done tab: ownership of untick, edit and delete ---------------------------------------


@pytest.fixture
def their_done(session, theirs) -> LearningTask:
    return session.exec(
        select(LearningTask).where(LearningTask.goal_id == theirs.id, LearningTask.done)
    ).one()


def test_untick_another_users_done_task_is_404(auth_client, session, their_done):
    response = auth_client.post(
        f"/learning/tasks/{their_done.id}/done", data={"done": "no"}, headers=htmx(auth_client)
    )

    assert response.status_code == 404
    session.refresh(their_done)
    assert their_done.done and their_done.done_at is not None


def test_edit_form_of_another_users_done_task_is_404(auth_client, their_done):
    response = auth_client.get(f"/learning/tasks/{their_done.id}/edit")

    assert response.status_code == 404
    assert "Their done" not in response.text


def test_update_another_users_done_task_is_404(auth_client, session, their_done):
    response = auth_client.post(
        f"/learning/tasks/{their_done.id}",
        data={"kind": "done", "title": "Hijacked", "spent_minutes": "5"},
        headers=htmx(auth_client),
    )

    assert response.status_code == 404
    session.refresh(their_done)
    assert their_done.title == "Their done"


def test_delete_another_users_done_task_is_404(auth_client, session, their_done):
    response = auth_client.post(
        f"/learning/tasks/{their_done.id}/delete", headers=htmx(auth_client)
    )

    assert response.status_code == 404
    session.expire_all()
    assert session.get(LearningTask, their_done.id) is not None
