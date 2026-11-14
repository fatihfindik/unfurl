"""Shared test setup.

The environment variables are set before `app` is imported, because the settings
and the database engine are created at import time.
"""

import os

os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory SQLite, nothing written to disk
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["EMAIL_BACKEND"] = "console"  # never SMTP from tests, whatever .env says

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import email_limiter, limiter, login_lockout  # noqa: E402
from app.services import email_service  # noqa: E402
from app.services.auth_service import create_user  # noqa: E402
from tests.helpers import USER_EMAIL, USER_PASSWORD, log_in  # noqa: E402


@pytest.fixture(autouse=True)
def reset_limits():
    """Limits live in memory for the whole test run; start every test with none used."""
    login_lockout.reset()
    email_limiter.reset()
    limiter.reset()


@pytest.fixture
def engine():
    """A fresh, empty in-memory database for each test.

    StaticPool keeps one connection open, so every session sees the same
    in-memory database (each new connection would otherwise get an empty one).
    The tables are created from the models here, in tests only; the app itself
    only gets tables from Alembic migrations (test_migrations.py checks that
    the models and the migrations match).
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """A database session for preparing and checking data in a test."""
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    """An HTTP client that calls the app directly, without a running server.

    Routes get their database session from the test database instead of the real one.
    """

    def test_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def user(session) -> User:
    """A saved user on the free plan. Logs in with USER_EMAIL / USER_PASSWORD."""
    return create_user(session, email=USER_EMAIL, password=USER_PASSWORD, full_name="Jana Novak")


@pytest.fixture
def auth_client(client, user) -> TestClient:
    """A client that is logged in as `user`."""
    assert log_in(client).status_code == 303
    return client


@pytest.fixture
def outbox(monkeypatch) -> list[email_service.Email]:
    """Catch emails instead of sending them. Returns the list of "sent" emails.

    Routes call email_service.send_email, so replacing it on the module is enough.
    TestClient runs background tasks before it returns the response, so the list
    is already filled when the test checks it.
    """
    sent: list[email_service.Email] = []

    async def fake_send_email(email: email_service.Email) -> None:
        sent.append(email)

    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    return sent
