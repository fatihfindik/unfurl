"""Checks that only mean something on PostgreSQL (production's database).

The other tests use SQLite, which ignores `SELECT ... FOR UPDATE`, lets only one
writer in at a time and handles foreign keys a little differently. These run only
when TEST_POSTGRES_URL points to an empty Postgres database, e.g.

    TEST_POSTGRES_URL=postgresql+psycopg://postgres:test@127.0.0.1:55432/unfurl_test \\
        uv run pytest tests/test_postgres.py

CI runs them on pull requests to main (the postgres-tests job). The schema comes
from the Alembic migrations, like in production, and is removed again afterwards.
"""

import datetime as dt
import os
import threading
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

from app.models import Experience, LearningGoal, Notification, User
from app.services import application_service as apps
from app.services import auth_service, document_service, profile_service
from app.services import learning_service as learning

URL = os.environ.get("TEST_POSTGRES_URL", "")
ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL is not set")


@pytest.fixture(scope="module")
def pg_engine():
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", URL)
    command.upgrade(config, "head")
    engine = create_engine(URL)
    yield engine
    engine.dispose()
    command.downgrade(config, "base")


@pytest.fixture
def pg(pg_engine):
    """A session on Postgres; every table is emptied after the test."""
    with Session(pg_engine) as session:
        yield session
    with pg_engine.begin() as connection:
        tables = [t for t in inspect(connection).get_table_names() if t != "alembic_version"]
        connection.execute(text(f"TRUNCATE {', '.join(f'"{t}"' for t in tables)} CASCADE"))


def new_user(session, email="jana@example.com") -> User:
    return auth_service.create_user(session, email=email, password="x" * 12, full_name="Jana")


# --- Deleting a user deletes everything of theirs (D-034, needed by M13b) -------------------


def test_deleting_a_user_cascades_to_every_table(pg):
    user = new_user(pg)
    other = new_user(pg, "other@example.com")
    profile = profile_service.get_or_create_profile(pg, user)
    profile_service.add_item(
        pg,
        profile,
        Experience,
        {"title": "PM", "company": "Acme", "start_date": dt.date(2020, 1, 1)},
    )
    document_service.create_cv(pg, user, "My CV", "en", "classic")
    job = apps.create(pg, user, company="Acme", job_title="PM")
    apps.move(pg, user, job, "applied")
    goal = LearningGoal(user_id=user.id, title="Python")
    pg.add(goal)
    pg.commit()
    task = learning.add_task(
        pg, user, goal, title="Week 1", due_date=dt.date(2026, 11, 18), minutes=30
    )
    learning.set_done(pg, user, task, True)
    learning.preferences(pg, user)
    pg.add(
        Notification(
            user_id=user.id,
            application_id=job.id,
            type="follow_up",
            title="t",
            body="b",
            link_url="/",
            dedupe_key="k",
        )
    )
    pg.commit()
    user_id = user.id

    pg.delete(user)
    pg.commit()

    with pg.bind.connect() as connection:
        leftovers = {}
        for table in inspect(connection).get_table_names():
            columns = {column["name"] for column in inspect(connection).get_columns(table)}
            if "user_id" in columns:
                count = connection.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE user_id = :id'), {"id": user_id}
                ).scalar()
                if count:
                    leftovers[table] = count
        experiences = connection.execute(text("SELECT count(*) FROM experience")).scalar()
    assert leftovers == {}
    assert experiences == 0  # profile rows go with the profile
    assert pg.get(User, other.id) is not None  # nobody else's data


# --- The profile's 50-row limit holds when two adds come at once (#22, D-121) ---------------


def test_two_adds_at_the_same_moment_respect_the_limit(pg, pg_engine):
    user = new_user(pg)
    profile = profile_service.get_or_create_profile(pg, user)
    for number in range(profile_service.MAX_ITEMS_PER_SECTION - 1):
        pg.add(
            Experience(
                profile_id=profile.id,
                title=f"Job {number}",
                company="A",
                start_date=dt.date(2020, 1, 1),
                sort_order=number,
            )
        )
    pg.commit()

    start = threading.Barrier(2)
    results = []

    def add(name):
        with Session(pg_engine) as session:
            mine = session.get(type(profile), profile.id)
            start.wait()
            try:
                data = {"title": name, "company": "A", "start_date": dt.date(2020, 1, 1)}
                profile_service.add_item(session, mine, Experience, data)
                results.append("added")
            except profile_service.TooManyItems:
                results.append("full")

    threads = [threading.Thread(target=add, args=(name,)) for name in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == ["added", "full"]
    count = pg.exec(select(Experience).where(Experience.profile_id == profile.id)).all()
    assert len(count) == profile_service.MAX_ITEMS_PER_SECTION


# --- A reset link sets the password once (#16, D-121) ---------------------------------------


def test_two_resets_at_the_same_moment_set_one_password(pg, pg_engine):
    user = new_user(pg)
    start = threading.Barrier(2)
    results = []

    def reset(password):
        with Session(pg_engine) as session:
            mine = session.get(User, user.id)  # both read the old hash
            start.wait()
            results.append(auth_service.reset_password(session, mine, password))

    threads = [threading.Thread(target=reset, args=(p,)) for p in ("first password", "second one!")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(results) == [False, True]
    pg.expire_all()
    assert pg.get(User, user.id).session_version == 1
