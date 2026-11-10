"""Alembic environment: connects Alembic to Unfurl's settings and models.

Usage:
  uv run alembic revision --autogenerate -m "add user table"   # new migration from model changes
  uv run alembic upgrade head                                   # bring the database up to date
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import app.models  # noqa: F401  (registers all tables on SQLModel.metadata)
from app.config import get_settings

# The Alembic Config object: gives access to the values in alembic.ini.
config = context.config

# Set up logging from alembic.ini. disable_existing_loggers=False keeps the app's
# loggers working when migrations run inside the app process (tests, Docker start).
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# The database URL comes from the app settings (.env), unless the caller already set one
# (tests point Alembic at a temporary database this way).
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

# The tables Alembic compares the database against for --autogenerate.
target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Write the SQL to the screen instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run the migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't change most columns with ALTER TABLE; batch mode copies
            # the table instead. On Postgres it behaves like normal ALTER TABLE.
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
