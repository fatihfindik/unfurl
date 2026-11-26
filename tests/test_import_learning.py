"""The one-off learning import tool (D-186, #101)."""

import datetime as dt
import io
import json
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from app.models.base import as_utc
from app.models.learning import LearningGoal, LearningTask, StudyLog
from app.services import clock, learning_service
from app.services.auth_service import create_user
from app.tools import import_learning
from app.tools.import_learning import ImportFailed, run
from tests.conftest import USER_EMAIL


def today() -> dt.date:
    return clock.today()


def task(**fields) -> dict:
    data = {"title": "Read chapter 1", "due_date": str(today()), "minutes": 60}
    data.update(fields)
    return data


def payload(*goals) -> str:
    return json.dumps({"goals": list(goals)})


def goal(title="Foundations", tasks=None) -> dict:
    return {"title": title, "tasks": tasks if tasks is not None else [task()]}


def goals_of(session, user):
    return session.exec(select(LearningGoal).where(LearningGoal.user_id == user.id)).all()


def tasks_of(session, user):
    return session.exec(select(LearningTask).where(LearningTask.user_id == user.id)).all()


def nothing_written(session, user) -> bool:
    session.expire_all()
    return not goals_of(session, user) and not tasks_of(session, user)


@pytest.fixture
def other(session):
    return create_user(
        session, email="other@example.com", password="another-password-1", full_name="Other"
    )


# --- Dry run and apply --------------------------------------------------------------


def test_a_dry_run_writes_nothing_and_says_what_it_would_do(session, user):
    out = run(session, USER_EMAIL, payload(goal(tasks=[task(), task(title="Two")])), False)
    assert "Dry run, nothing written" in out
    assert "Foundations: 2 tasks (0 done)" in out
    assert "Total: 1 goals, 2 tasks." in out
    assert "--apply" in out
    assert nothing_written(session, user)


def test_apply_creates_goals_and_tasks_with_every_field(session, user):
    later = today() + dt.timedelta(days=3)
    text = payload(
        goal("Phase 1", [task(title="B", due_date=str(later), minutes=150, url="https://x.dev")]),
        goal("Phase 2", [task(title="C")]),
    )
    out = run(session, USER_EMAIL, text, True)
    assert out.startswith(f"Imported for user #{user.id}")
    goals = sorted(goals_of(session, user), key=lambda g: g.sort_order)
    assert [g.title for g in goals] == ["Phase 1", "Phase 2"]
    assert all(g.source == "manual" and g.target_date is None for g in goals)
    (b,) = [t for t in tasks_of(session, user) if t.goal_id == goals[0].id]
    assert (b.title, b.due_date, b.estimated_minutes, b.url) == ("B", later, 150, "https://x.dev")
    assert b.done is False and b.done_at is None


def test_new_goals_go_after_the_existing_ones(session, user):
    learning_service.create_goal(
        session,
        user,
        title="Old",
        target_date=None,
        weekly_hours=None,
        resource=None,
        plan_weekly=False,
    )
    run(session, USER_EMAIL, payload(goal("New")), True)
    by_title = {g.title: g.sort_order for g in goals_of(session, user)}
    assert by_title["New"] > by_title["Old"]


def test_tasks_are_ordered_by_date_within_a_goal(session, user):
    d1, d2 = today() + dt.timedelta(days=5), today() + dt.timedelta(days=1)
    run(
        session,
        USER_EMAIL,
        payload(
            goal(
                tasks=[task(title="Late", due_date=str(d1)), task(title="Early", due_date=str(d2))]
            )
        ),
        True,
    )
    ordered = sorted(tasks_of(session, user), key=lambda t: t.sort_order)
    assert [t.title for t in ordered] == ["Early", "Late"]


def test_the_other_users_data_is_untouched(session, user, other):
    run(session, USER_EMAIL, payload(goal()), True)
    assert goals_of(session, other) == [] and tasks_of(session, other) == []


def test_the_email_is_matched_like_at_login(session, user):
    run(session, f"  {USER_EMAIL.upper()} ", payload(goal()), True)
    assert len(goals_of(session, user)) == 1


def test_an_unknown_email_writes_nothing(session, user):
    with pytest.raises(ImportFailed, match="No user"):
        run(session, "nobody@example.com", payload(goal()), True)
    assert nothing_written(session, user)


# --- Done tasks -----------------------------------------------------------------------


def test_a_done_task_is_done_at_noon_in_the_users_time_zone_without_a_log(session, user):
    user.timezone = "America/New_York"
    session.add(user)
    session.commit()
    with clock.use_timezone("America/New_York"):
        day = clock.today() - dt.timedelta(days=3)
    run(session, USER_EMAIL, payload(goal(tasks=[task(due_date=str(day), done=True)])), True)
    (done,) = tasks_of(session, user)
    noon = dt.datetime.combine(day, dt.time(12), tzinfo=ZoneInfo("America/New_York"))
    assert done.done is True
    assert as_utc(done.done_at) == noon.astimezone(dt.UTC)
    assert session.exec(select(StudyLog).where(StudyLog.user_id == user.id)).all() == []


def test_a_done_task_in_the_future_is_an_error(session, user):
    tomorrow = str(today() + dt.timedelta(days=1))
    with pytest.raises(ImportFailed, match="future"):
        run(session, USER_EMAIL, payload(goal(tasks=[task(due_date=tomorrow, done=True)])), True)
    assert nothing_written(session, user)


# --- Titles ---------------------------------------------------------------------------


def test_a_long_title_is_shortened_with_a_warning(session, user):
    out = run(session, USER_EMAIL, payload(goal(tasks=[task(title="x" * 203)])), True)
    (saved,) = tasks_of(session, user)
    assert len(saved.title) == 200 and saved.title.endswith("…")
    assert "Warning: goal 1, task 1: the title was shortened" in out


def test_titles_lose_their_outer_spaces(session, user):
    run(session, USER_EMAIL, payload(goal("  Phase  ", [task(title="  A ")])), True)
    assert goals_of(session, user)[0].title == "Phase"
    assert tasks_of(session, user)[0].title == "A"


def test_a_goal_i_already_have_stops_the_import(session, user):
    run(session, USER_EMAIL, payload(goal("Phase")), True)
    with pytest.raises(ImportFailed, match='already have a goal "Phase"'):
        run(session, USER_EMAIL, payload(goal("Other"), goal(" Phase ")), True)
    session.expire_all()
    assert [g.title for g in goals_of(session, user)] == ["Phase"]
    assert len(tasks_of(session, user)) == 1


def test_the_same_goal_twice_in_the_file_is_an_error(session, user):
    with pytest.raises(ImportFailed, match="more than once"):
        run(session, USER_EMAIL, payload(goal("A"), goal("A ")), True)
    assert nothing_written(session, user)


def test_another_users_goal_with_the_same_title_doesnt_matter(session, user, other):
    run(session, "other@example.com", payload(goal("Phase")), True)
    run(session, USER_EMAIL, payload(goal("Phase")), True)
    assert len(goals_of(session, user)) == 1


# --- Invalid files: nothing is written ------------------------------------------------

OLD = str(dt.date.today() - dt.timedelta(days=40))
FAR = str(dt.date.today() + dt.timedelta(days=3 * 365))


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (task(title="   "), "title is empty"),
        (task(title=5), "title is empty"),
        (task(due_date="26/11/2026"), "YYYY-MM-DD"),
        (task(due_date=None), "Pick a date"),
        (task(due_date=OLD), "days ago"),
        (task(due_date=FAR), "years from now"),
        (task(minutes=4), "minutes must be"),
        (task(minutes=601), "minutes must be"),
        (task(minutes="60"), "minutes must be"),
        (task(minutes=True), "minutes must be"),
        (task(minutes=1.5), "minutes must be"),
        (task(url="javascript:alert(1)"), "https://"),
        (task(url="https://x.dev/" + "a" * 500), "at most 500"),
        (task(url=["https://x.dev"]), "link must be text"),
        (task(done="yes"), "true or false"),
        (task(colour="red"), "unknown fields colour"),
        ("not an object", "must be an object"),
    ],
)
def test_a_bad_task_stops_everything(session, user, bad, message):
    text = payload(goal("Fine"), goal("Broken", [task(), bad]))
    with pytest.raises(ImportFailed) as error:
        run(session, USER_EMAIL, text, True)
    assert any(p.startswith("goal 2, task 2") and message in p for p in error.value.problems)
    assert nothing_written(session, user)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "not valid JSON"),
        ("[]", '{"goals"'),
        (json.dumps({"goals": [], "extra": 1}), '{"goals"'),
        (json.dumps({"goals": {}}), '{"goals"'),
        (payload(), "no goals"),
        (payload({"title": "A", "tasks": [task()], "colour": "red"}), "unknown fields colour"),
        (payload({"title": "A", "tasks": []}), "at least one task"),
        (payload({"title": "A"}), "at least one task"),
        (payload({"title": "", "tasks": [task()]}), "title is empty"),
        (payload("A"), "must be an object"),
    ],
)
def test_a_bad_file_or_goal_stops_everything(session, user, text, message):
    with pytest.raises(ImportFailed) as error:
        run(session, USER_EMAIL, text, True)
    assert any(message in p for p in error.value.problems)
    assert nothing_written(session, user)


def test_every_problem_is_listed_at_once(session, user):
    text = payload(goal(tasks=[task(minutes=1), task(due_date="x")]))
    with pytest.raises(ImportFailed) as error:
        run(session, USER_EMAIL, text, True)
    assert len(error.value.problems) == 2


def test_a_too_large_file_is_refused(session, user):
    text = payload(goal(tasks=[task(title="x" * 150)] * 7000))
    assert len(text) > import_learning.MAX_INPUT_BYTES
    with pytest.raises(ImportFailed, match="larger than"):
        run(session, USER_EMAIL, text, True)
    assert nothing_written(session, user)


# --- Limits ---------------------------------------------------------------------------


def test_too_many_goals_stops_the_import(session, user, monkeypatch):
    monkeypatch.setattr(import_learning, "MAX_GOALS", 2)
    with pytest.raises(ImportFailed, match="limit"):
        run(session, USER_EMAIL, payload(goal("A"), goal("B"), goal("C")), True)
    assert nothing_written(session, user)


def test_too_many_tasks_stops_the_import(session, user, monkeypatch):
    monkeypatch.setattr(import_learning, "MAX_TASKS", 2)
    with pytest.raises(ImportFailed, match="limit"):
        run(session, USER_EMAIL, payload(goal(tasks=[task(), task(), task()])), True)
    assert nothing_written(session, user)


def test_a_failure_while_writing_rolls_everything_back(session, user, monkeypatch):
    calls = []

    def broken_to_utc(*args):
        calls.append(args)
        raise RuntimeError("boom")

    monkeypatch.setattr(clock, "to_utc", broken_to_utc)
    past = str(today() - dt.timedelta(days=1))
    text = payload(goal("A"), goal("B", [task(due_date=past, done=True)]))
    with pytest.raises(RuntimeError):
        run(session, USER_EMAIL, text, True)
    assert calls and nothing_written(session, user)


# --- The command line -----------------------------------------------------------------


def test_main_dry_run_and_apply(engine, session, user, monkeypatch, capsys):
    monkeypatch.setattr("app.db.engine", engine)
    text = payload(goal())
    assert import_learning.main(["--email", USER_EMAIL], io.StringIO(text)) == 0
    assert "Dry run" in capsys.readouterr().out
    assert nothing_written(session, user)
    assert import_learning.main(["--email", USER_EMAIL, "--apply"], io.StringIO(text)) == 0
    out = capsys.readouterr().out
    assert "Imported" in out and USER_EMAIL not in out
    session.expire_all()
    assert len(goals_of(session, user)) == 1


def test_main_reports_problems_and_exits_with_1(engine, session, user, monkeypatch, capsys):
    monkeypatch.setattr("app.db.engine", engine)
    code = import_learning.main(["--email", USER_EMAIL, "--apply"], io.StringIO("{"))
    assert code == 1
    assert "Nothing was imported" in capsys.readouterr().err
    assert nothing_written(session, user)


# --- Security review fixes ------------------------------------------------------------


def test_the_summary_shows_what_the_account_has_before(session, user):
    run(session, USER_EMAIL, payload(goal("Old", [task(), task()])), True)
    out = run(session, USER_EMAIL, payload(goal("New")), False)
    assert f"for user #{user.id} (before: 1 goals, 2 tasks):" in out


def test_a_goal_differing_only_in_case_stops_the_import(session, user):
    run(session, USER_EMAIL, payload(goal("Phase 1")), True)
    with pytest.raises(ImportFailed, match="already have"):
        run(session, USER_EMAIL, payload(goal("PHASE 1")), True)
    with pytest.raises(ImportFailed, match="more than once"):
        run(session, USER_EMAIL, payload(goal("A b"), goal("a B")), False)


@pytest.mark.parametrize("value", [20261102, 2026.11, True, ["2026-11-02"]])
def test_a_date_that_isnt_text_is_an_error(session, user, value):
    with pytest.raises(ImportFailed, match="YYYY-MM-DD"):
        run(session, USER_EMAIL, payload(goal(tasks=[task(due_date=value)])), True)
    assert nothing_written(session, user)


@pytest.mark.parametrize("value", [False, 0, []])
def test_a_link_that_isnt_text_is_an_error(session, user, value):
    with pytest.raises(ImportFailed, match="link must be text"):
        run(session, USER_EMAIL, payload(goal(tasks=[task(url=value)])), True)


@pytest.mark.parametrize("value", ["", None])
def test_an_empty_link_is_no_link(session, user, value):
    run(session, USER_EMAIL, payload(goal(tasks=[task(url=value)])), True)
    assert tasks_of(session, user)[0].url is None


def test_a_deeply_nested_file_is_a_clean_error(session, user):
    text = '{"goals": ' + "[" * 100_000 + "]" * 100_000 + "}"
    with pytest.raises(ImportFailed, match="nested too deeply"):
        run(session, USER_EMAIL, text, True)


def test_main_refuses_a_file_that_isnt_utf8(engine, session, user, monkeypatch, capsys):
    monkeypatch.setattr("app.db.engine", engine)
    stdin = io.TextIOWrapper(io.BytesIO(b'{"goals": "\xff\xfe"}'), encoding="utf-8")
    assert import_learning.main(["--email", USER_EMAIL, "--apply"], stdin) == 1
    assert "not UTF-8" in capsys.readouterr().err
    assert nothing_written(session, user)
