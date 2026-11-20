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
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import create_engine, inspect, text
from sqlmodel import Session, select

from app.db import get_session
from app.main import app
from app.models import ApiToken, Experience, User
from app.services import auth_service, profile_service, token_service
from tests.account_data import fill_account
from tests.helpers import USER_EMAIL, USER_PASSWORD, csrf_token, log_in

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


# --- Deleting a user deletes everything of theirs (D-034, D-131) -----------------------------


def counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        tables = [t for t in inspect(connection).get_table_names() if t != "alembic_version"]
        return {t: connection.execute(text(f'SELECT count(*) FROM "{t}"')).scalar() for t in tables}


def test_deleting_a_user_cascades_to_every_table(pg, pg_engine):
    other = new_user(pg, "other@example.com")
    fill_account(pg, other)
    only_other = counts(pg_engine)
    user = new_user(pg)
    fill_account(pg, user)

    pg.delete(user)
    pg.commit()

    # Every table is back to the other user's rows: nothing of the user is left,
    # links between tables (a CV of a job, a task of a resource, …) included.
    assert counts(pg_engine) == only_other


def test_delete_my_account_works_on_postgres(pg, pg_engine):
    """The route, with the app's database session on Postgres."""
    other = new_user(pg, "other@example.com")
    fill_account(pg, other)
    only_other = counts(pg_engine)
    user = auth_service.create_user(pg, email=USER_EMAIL, password=USER_PASSWORD, full_name="Jana")
    fill_account(pg, user)

    def pg_session():
        with Session(pg_engine) as session:
            yield session

    app.dependency_overrides[get_session] = pg_session
    try:
        client = TestClient(app)
        assert log_in(client).status_code == 303
        response = client.post(
            "/settings/delete-account",
            data={"password": USER_PASSWORD, "csrf_token": csrf_token(client)},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.clear()

    assert response.headers["location"] == "/login?deleted=1"
    assert counts(pg_engine) == only_other


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


# --- Two accounts can't end up with the same email (#56, D-134) -----------------------------


def test_an_email_change_that_loses_the_race_is_rolled_back(pg, monkeypatch):
    user = new_user(pg)
    new_user(pg, "taken@example.com")
    # The "is it free?" check ran before the other account took the address.
    monkeypatch.setattr(auth_service, "email_in_use", lambda *args: False)

    result = auth_service.change_email(pg, user, "taken@example.com")

    assert result == "taken"
    pg.expire_all()
    assert pg.get(User, user.id).email == "jana@example.com"


# --- Extension tokens (D-136) ----------------------------------------------------------------


def test_extension_tokens_expire_and_are_cleaned_up_on_postgres(pg):
    user = new_user(pg)
    with freeze_time("2026-01-01 12:00:00"):
        old_raw, old = token_service.issue(pg, user)
        old_id = old.id
        assert token_service.check(pg, old_raw) is not None
    with freeze_time("2026-04-02 12:00:00"):  # 91 days later
        assert token_service.check(pg, old_raw) is None
        raw, _ = token_service.issue(pg, user)  # deletes the expired one in SQL
        assert token_service.check(pg, raw) is not None

    assert pg.get(ApiToken, old_id) is None
