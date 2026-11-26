"""The Learning page (SPEC §6.10, M10): cards, plan tabs, goals."""

import datetime as dt
import re

import pytest

from app.models import LearningGoal, LearningResource, LearningTask, StudyLog
from app.services import clock
from app.services import learning_service as learning
from app.services.auth_service import create_user

# Wednesday 18 November 2026, 10:00 in Vienna.
NOW = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
WED = dt.date(2026, 11, 18)


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(clock, "utcnow", lambda: NOW)


def day(offset: int) -> dt.date:
    return WED + dt.timedelta(days=offset)


def add_goal(session, user, title="Python foundations", **fields) -> LearningGoal:
    goal = LearningGoal(user_id=user.id, title=title, **fields)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


def add_task(session, goal, title, due, **fields) -> LearningTask:
    task = LearningTask(user_id=goal.user_id, goal_id=goal.id, title=title, due_date=due, **fields)
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def card(html: str, name: str) -> str:
    return html.split(f'data-card="{name}"', 1)[1].split("</div>", 1)[0]


# --- Access -------------------------------------------------------------------------------


def test_logged_out_goes_to_login(client):
    response = client.get("/learning", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/learning"


def test_empty_page(auth_client):
    response = auth_client.get("/learning")

    assert response.status_code == 200
    html = response.text
    assert re.search(r"<h1[^>]*>Learning</h1>", html)
    assert "Coming soon." not in html
    assert "No goals yet." in html
    assert "0 days" in html
    assert "Weekends are rest days. Your streak is safe." in html
    assert "Set hours per week on a goal to see a target." in html
    assert "Mon, 16 Nov – Sun, 22 Nov" in html
    # Next reminder since M11b (D-115): nothing planned yet.
    assert "Next reminder" in html and "No tasks planned" in html


# --- Summary cards ------------------------------------------------------------------------


def test_cards_show_streak_hours_and_tasks(auth_client, session, user):
    goal = add_goal(session, user, weekly_hours_target=4.0)
    add_task(session, goal, "Done", day(-1), done=True, done_at=NOW - dt.timedelta(days=1))
    add_task(session, goal, "Open", day(1))
    session.add(StudyLog(user_id=user.id, goal_id=goal.id, date=day(-1), minutes=90))
    session.commit()

    html = auth_client.get("/learning").text

    assert "1 day" in card(html, "streak")
    week = card(html, "week")
    assert "1.5 h" in week and "/ 4 h" in week
    assert 'value="38"' in html  # 90 of 240 minutes
    assert "1 <span" in card(html, "tasks") and "/ 2" in card(html, "tasks")


def test_rest_day_note_follows_the_users_days(auth_client, session, user):
    learning.set_rest_days(session, user, ["mon", "fri"])
    assert "Rest days: Mon, Fri. Your streak is safe." in auth_client.get("/learning").text

    learning.set_rest_days(session, user, [])
    assert "No rest days. Every day counts." in auth_client.get("/learning").text


# --- Plan tabs ----------------------------------------------------------------------------


@pytest.fixture
def planned(session, user):
    goal = add_goal(session, user)
    add_task(session, goal, "Overdue lesson", day(-3))
    add_task(
        session, goal, "Today lesson", day(0), estimated_minutes=90, url="https://cs50.harvard.edu/"
    )
    add_task(session, goal, "Friday lesson", day(2), estimated_minutes=25)
    add_task(session, goal, "Later lesson", day(20))
    add_task(session, goal, "Finished", day(0), done=True, done_at=NOW)
    return goal


def plan_titles(html: str) -> list[str]:
    plan = html.split('id="plan-title"', 1)[1].split('id="goals-title"', 1)[0]
    plan = plan.split('<details class="group', 1)[0]  # open tasks only, not "Done (n)"
    return re.findall(r'<span class="text-sm font-medium[^"]*">([^<]+)</span>', plan)


def test_today_is_the_default_tab(auth_client, planned):
    html = auth_client.get("/learning").text

    assert plan_titles(html) == ["Today lesson"]
    assert 'aria-current="page"' in html.split('href="/learning?view=today"', 1)[1][:200]
    assert "Catch-up (1)" in html
    assert "1.5 h" in html
    assert 'href="https://cs50.harvard.edu/"' in html
    assert 'rel="noopener noreferrer"' in html
    assert "Done (1)" in html


def test_next_seven_days_tab(auth_client, planned):
    html = auth_client.get("/learning?view=week").text

    assert plan_titles(html) == ["Today lesson", "Friday lesson"]
    assert "Fri, 20 Nov" in html and "25 min" in html


def test_catchup_tab(auth_client, planned):
    html = auth_client.get("/learning?view=catchup").text

    assert plan_titles(html) == ["Overdue lesson"]
    assert "Was due Sun, 15 Nov" in html
    assert "These are overdue. No stress, you can reschedule them." in html
    # The assistant link comes with M12 (D-089).
    assert "Ask the assistant" not in html


def test_all_tasks_tab_groups_by_week(auth_client, planned):
    html = auth_client.get("/learning?view=all").text

    assert plan_titles(html) == ["Overdue lesson", "Today lesson", "Friday lesson", "Later lesson"]
    labels = re.findall(r"tracking-\[0\.04em\] text-text-3\">([^<]+)</li>", html)
    assert labels == ["Overdue", "This week", "Later"]


def test_unknown_tab_shows_today(auth_client, planned):
    html = auth_client.get("/learning?view=<script>").text

    assert plan_titles(html) == ["Today lesson"]
    # The region reloads itself with the tab it shows, never with the raw value.
    assert 'hx-get="/learning?view=today"' in html
    assert "view=&lt;script" not in html and "view=<script" not in html


def test_empty_tab_messages(auth_client, session, user):
    add_goal(session, user)

    assert "Nothing planned for today." in auth_client.get("/learning").text
    assert "Nothing to catch up on." in auth_client.get("/learning?view=catchup").text


def test_htmx_tab_returns_only_the_region(auth_client, planned):
    response = auth_client.get("/learning?view=week", headers={"HX-Request": "true"})

    assert response.text.lstrip().startswith('<div id="learning-region"')
    assert "<h1" not in response.text
    assert 'hx-get="/learning?view=week"' in response.text


# --- Goals card ---------------------------------------------------------------------------


def test_goal_with_a_resource(auth_client, session, user):
    goal = add_goal(session, user, target_date=dt.date(2027, 2, 28))
    session.add(
        LearningResource(
            user_id=user.id,
            goal_id=goal.id,
            name="CS50P",
            provider="Harvard",
            url="https://cs50.harvard.edu/python/",
            units_total=10,
            units_done=3,
        )
    )
    session.add(StudyLog(user_id=user.id, goal_id=goal.id, date=day(-1), minutes=120))
    session.commit()

    html = auth_client.get("/learning").text
    goal_html = html.split(f'data-goal="{goal.id}"', 1)[1].split("</article>", 1)[0]

    assert "Python foundations" in goal_html
    assert "30%" in goal_html
    assert "CS50P · Harvard" in goal_html
    assert "Week 3 / 10" in goal_html
    assert "2 h logged · target Feb 2027" in goal_html
    assert 'href="https://cs50.harvard.edu/python/"' in goal_html


def test_goals_get_colors_in_order(auth_client, session, user):
    for number in range(7):
        add_goal(session, user, title=f"Goal {number}")

    html = auth_client.get("/learning").text

    dots = re.findall(r'rounded-full (bg-\[#[0-9A-F]{6}\])"></span>', html)
    assert len(dots) == 7
    assert dots[0] == dots[6] and dots[0] != dots[1]


def test_user_text_is_escaped(auth_client, session, user):
    goal = add_goal(session, user, title="<b>bold</b>")
    add_task(session, goal, "<img src=x>", day(0))

    html = auth_client.get("/learning").text

    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "<img src=x>" not in html


def test_other_users_data_is_not_shown(auth_client, session):
    other = create_user(session, email="other@example.test", password="x" * 12, full_name="O")
    goal = add_goal(session, other, title="Their secret goal")
    add_task(session, goal, "Their task", day(0))

    html = auth_client.get("/learning?view=all").text

    assert "Their secret goal" not in html and "Their task" not in html


# --- Done tab, Add task at the top, edit by title (D-187, D-189) ---------------------------


def plan_card(html: str) -> str:
    return html.split('id="plan-title"', 1)[1].split('id="goals-title"', 1)[0]


def test_done_tab_lists_every_done_task_newest_first(auth_client, session, user):
    goal = add_goal(session, user)
    old = add_task(session, goal, "Old one", day(-60), done=True,
                   done_at=NOW - dt.timedelta(days=60))  # fmt: skip
    new = add_task(session, goal, "New one", day(-1), done=True, done_at=NOW)
    html = auth_client.get("/learning?view=done").text

    assert "Done (2)" in html
    card_html = plan_card(html)
    assert card_html.index(f'data-task="{new.id}"') < card_html.index(f'data-task="{old.id}"')
    assert "Done 18 Nov" in card_html and "Done 19 Sep" in card_html
    assert "line-through" in card_html


def test_done_from_another_year_shows_the_year(auth_client, session, user):
    goal = add_goal(session, user)
    add_task(session, goal, "Last year", day(0), done=True, done_at=NOW - dt.timedelta(days=365))

    assert "Done 18 Nov 2025" in auth_client.get("/learning?view=done").text


@pytest.mark.parametrize("view", ["today", "week", "all", "catchup"])
def test_other_tabs_show_open_tasks_only(auth_client, session, user, view):
    goal = add_goal(session, user)
    done = add_task(session, goal, "Finished", day(0), done=True, done_at=NOW)
    old = add_task(session, goal, "Finished late", day(-2), done=True, done_at=NOW)
    html = plan_card(auth_client.get(f"/learning?view={view}").text)

    assert f'data-task="{done.id}"' not in html and f'data-task="{old.id}"' not in html
    assert '<details class="group' not in html  # no collapsed Done section any more


def test_done_tab_empty(auth_client, session, user):
    add_goal(session, user)

    assert "No completed tasks yet." in auth_client.get("/learning?view=done").text


def test_add_task_is_in_the_plan_header(auth_client, session, user):
    goal = add_goal(session, user)
    for number in range(5):
        add_task(session, goal, f"Lesson {number}", day(number))
    html = plan_card(auth_client.get("/learning?view=all").text)

    add = html.index('data-dialog-url="/learning/tasks/new"')
    assert add < html.index('aria-label="Plan view"')  # above the tabs and the list
    assert html.count('data-dialog-url="/learning/tasks/new"') == 1


def test_no_add_task_in_the_header_without_goals(auth_client):
    html = plan_card(auth_client.get("/learning").text)

    assert 'data-dialog-url="/learning/tasks/new"' not in html


@pytest.mark.parametrize("view", ["today", "done"])
def test_a_title_opens_the_edit_dialog(auth_client, session, user, view):
    goal = add_goal(session, user)
    task = add_task(session, goal, "Lecture <4>", day(0), done=view == "done",
                    done_at=NOW if view == "done" else None)  # fmt: skip
    html = plan_card(auth_client.get(f"/learning?view={view}").text)
    row = html.split(f'data-task="{task.id}"', 1)[1].split("</li>", 1)[0]

    title = re.search(r'<button type="button" data-dialog-load="edit-task"[^>]*>', row).group(0)
    assert f'data-dialog-url="/learning/tasks/{task.id}/edit"' in title
    assert 'aria-label="Edit Lecture &lt;4&gt;"' in title  # escaped
    assert "Lecture <4>" not in row


def test_tasks_done_card_links_to_the_done_tab(auth_client, session, user):
    add_goal(session, user)
    tasks_card = card(auth_client.get("/learning").text, "tasks")

    assert 'hx-get="/learning?view=done"' in tasks_card and "See done tasks" in tasks_card
