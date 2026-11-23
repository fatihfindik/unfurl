"""The demo data script (D-173): only a separate demo.db, never a database with users,
and the data it writes is complete enough for the screenshots."""

import importlib.util
from pathlib import Path

import pytest
from sqlmodel import select

from app.models import Application, ApplicationDocument, LearningGoal, Notification, User

SCRIPT = Path(__file__).parent.parent / "scripts" / "seed_demo.py"


@pytest.fixture
def seed_demo():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///./unfurl.db",
        "sqlite:///./demo.db.bak",
        "sqlite:///./not-demo.db",
        "postgresql+psycopg://u:p@db:5432/demo.db",
    ],
)
def test_it_refuses_any_database_but_demo_db(seed_demo, monkeypatch, capsys, url):
    monkeypatch.setenv("DATABASE_URL", url)

    assert seed_demo.demo_database_url() is None
    assert seed_demo.main() == 1
    assert "Refused" in capsys.readouterr().out


@pytest.mark.parametrize("url", ["sqlite:///./demo.db", "sqlite:////tmp/x/demo.db"])
def test_a_demo_db_url_is_accepted(seed_demo, monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", url)

    assert seed_demo.demo_database_url() == url


def test_the_data_is_fictional_and_complete(seed_demo, session):
    seed_demo.seed(session, "a-test-password-123")

    (user,) = session.exec(select(User)).all()
    assert (user.email, user.full_name, user.is_verified) == (
        "jana.novak@example.com",
        "Jana Novak",
        True,
    )
    statuses = {job.status for job in session.exec(select(Application))}
    assert statuses == {"saved", "applied", "interview", "offer", "rejected"}
    assert session.exec(select(ApplicationDocument)).first() is not None  # sent and locked
    assert len(session.exec(select(LearningGoal)).all()) == 2
    assert session.exec(select(Notification)).first() is not None


def test_it_stops_when_the_database_has_users(seed_demo, monkeypatch, session, user, capsys):
    import app.db

    monkeypatch.setenv("DATABASE_URL", "sqlite:///./demo.db")
    monkeypatch.setattr(app.db, "engine", session.get_bind())

    assert seed_demo.main() == 1
    assert "already has users" in capsys.readouterr().out
    assert len(session.exec(select(User)).all()) == 1  # nothing added
