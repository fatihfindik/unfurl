"""Database engine and the per-request session dependency.

The schema is managed only by Alembic migrations; this module never creates tables.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.config import get_settings


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Make SQLite check foreign keys and run ON DELETE CASCADE, like Postgres (D-034).

    SQLite skips both unless every new connection runs `PRAGMA foreign_keys=ON`.
    Only call this for the app engine and the test engine, never for Alembic
    (alembic/env.py) and never on the Engine class itself: SQLite migrations rebuild
    a table by copying it and dropping the old one, and with foreign keys on that
    drop would also delete every row that points to it (e.g. all profiles).
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def turn_on_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


settings = get_settings()

# SQLite allows a connection to be used only by the thread that created it by default.
# FastAPI may run a request on a different thread, so this check is turned off for SQLite.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

# hide_parameters: an error message never contains the values of a query (company
# names, notes …), so a logged database error holds no personal data (M11a review, M1).
engine = create_engine(settings.database_url, connect_args=connect_args, hide_parameters=True)
enable_sqlite_foreign_keys(engine)


def get_session() -> Iterator[Session]:
    """Give a route one database session and close it when the request is done.

    Usage in a route: `session: Session = Depends(get_session)`
    """
    with Session(engine) as session:
        yield session
