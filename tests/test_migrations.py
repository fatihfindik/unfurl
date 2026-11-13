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
    assert current_revision(url) == "0001"

    command.downgrade(alembic_config, "base")
    assert current_revision(url) is None

    # Running the chain again must also work (no leftovers from the downgrade).
    command.upgrade(alembic_config, "head")
    assert current_revision(url) == "0001"


def test_models_and_migrations_are_in_sync(alembic_config):
    # Fails if a model was changed without generating a migration for it.
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)
