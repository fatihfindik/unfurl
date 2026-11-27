"""Shared test setup.

The environment variables are set before `app` is imported, because the settings
and the database engine are created at import time.
"""

import os

os.environ["APP_ENV"] = "development"
os.environ["DATABASE_URL"] = "sqlite://"  # in-memory SQLite, nothing written to disk
os.environ["SCHEDULER_ENABLED"] = "false"
# Production checks read it from the real environment, like uvicorn (D-199).
os.environ["FORWARDED_ALLOW_IPS"] = "127.0.0.1"
os.environ["EMAIL_BACKEND"] = "console"  # never SMTP from tests, whatever .env says
# The real Impressum details live in .env; tests never read them (D-133).
for name in ("LEGAL_OPERATOR_NAME", "LEGAL_ADDRESS", "LEGAL_EMAIL"):
    os.environ[name] = ""
os.environ["LEGAL_REVIEWED"] = "false"
# The extension's origin in tests (D-137); .env may hold the real one.
os.environ["EXTENSION_ORIGINS"] = "chrome-extension://testextensionid"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pwdlib import PasswordHash  # noqa: E402
from pwdlib.hashers.argon2 import Argon2Hasher  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app import security  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import enable_sqlite_foreign_keys, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import limiter  # noqa: E402
from app.services import auth_service, email_service  # noqa: E402
from app.services.auth_service import create_user  # noqa: E402
from tests.helpers import USER_EMAIL, USER_PASSWORD, log_in  # noqa: E402

# The app's real password hashing, kept before any test swaps it (test_auth.py checks
# its settings).
PRODUCTION_HASHER = auth_service.password_hasher
PRODUCTION_DUMMY_HASH = auth_service._DUMMY_HASH

# Still argon2id, but cheap: a hash takes well under a millisecond instead of ~60 ms,
# and most tests create a user or log in (D-145). FOR TESTS ONLY, never in app/.
CHEAP_HASHER = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
CHEAP_DUMMY_HASH = CHEAP_HASHER.hash("not-a-real-password")


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    monkeypatch.setattr(auth_service, "password_hasher", CHEAP_HASHER)
    monkeypatch.setattr(auth_service, "_DUMMY_HASH", CHEAP_DUMMY_HASH)


@pytest.fixture(autouse=True)
def reset_limits():
    """slowapi's limits live in memory for the whole test run; start every test with
    none used. The lockout and email counters are in each test's own database (the
    engine fixture); without it they use app.db.engine, an empty in-memory database
    here, so a test that forgot the fixture fails instead of reaching real data."""
    limiter.reset()


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    """Uploaded files (profile photos) go to a temporary folder, never into the repo."""
    folder = tmp_path / "uploads"
    monkeypatch.setattr(get_settings(), "upload_dir", str(folder))
    return folder


@pytest.fixture
def engine(monkeypatch):
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
    enable_sqlite_foreign_keys(engine)  # like the app (D-034)
    SQLModel.metadata.create_all(engine)
    # The sign-in lockout and email limits keep their counts here (D-198).
    # monkeypatch puts the old value back after the test. With StaticPool the counter
    # session shares the route's connection, so these tests can't catch a caller that
    # has uncommitted writes when it calls the lockout (rule in app/security.py, D-198).
    monkeypatch.setattr(security._DbCounters, "engine", engine)
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
