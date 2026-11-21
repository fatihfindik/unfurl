from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).parent.parent


@pytest.fixture
def alembic_config(tmp_path) -> Config:
    """Alembic pointed at a fresh SQLite file that is deleted after the test."""
    config = Config(ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'migrations.db'}")
    return config


def current_revision(url: str) -> str | None:
    engine = create_engine(url)
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return None
        return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_upgrade_and_downgrade_run_cleanly(alembic_config):
    url = alembic_config.get_main_option("sqlalchemy.url")

    command.upgrade(alembic_config, "head")
    assert current_revision(url) == "0018"

    command.downgrade(alembic_config, "base")
    assert current_revision(url) is None

    # Running the chain again must also work (no leftovers from the downgrade).
    command.upgrade(alembic_config, "head")
    assert current_revision(url) == "0018"


def test_models_and_migrations_are_in_sync(alembic_config):
    # Fails if a model was changed without generating a migration for it.
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_0015_fills_in_canonical_links_like_url_service(alembic_config):
    # The migration keeps its own copy of canonical_url (D-138); both must agree.
    import importlib.util

    from tests.test_url_service import EXAMPLES

    path = ROOT / "alembic/versions/0015_add_application_canonical_url.py"
    spec = importlib.util.spec_from_file_location("migration_0015", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    for link, expected in EXAMPLES:
        assert migration.canonical_url(link) == expected, link

    # Saved jobs that exist before 0015 get their canonical link. (This plain engine
    # doesn't turn on SQLite's foreign keys, so no user row is needed.)
    url = alembic_config.get_main_option("sqlalchemy.url")
    command.upgrade(alembic_config, "0014")
    engine = create_engine(url)
    with engine.begin() as connection:
        for row_id, link in [(1, "https://www.karriere.at/jobs/7654321?x=1"), (2, None)]:
            connection.execute(
                text(
                    "INSERT INTO application (id, user_id, company, job_title, remote_type,"
                    " job_url, source, description_raw, status, position, archived,"
                    " next_action_manual, last_activity_at, created_at, updated_at)"
                    " VALUES (:id, 1, 'Acme', 'Dev', 'unknown', :link, 'manual', '', 'saved',"
                    " 0, 0, 0, '2026-01-01', '2026-01-01', '2026-01-01')"
                ),
                {"id": row_id, "link": link},
            )
    command.upgrade(alembic_config, "0015")
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id, canonical_url FROM application ORDER BY id")
        ).all()
    assert rows == [(1, "https://karriere.at/jobs/7654321"), (2, None)]
