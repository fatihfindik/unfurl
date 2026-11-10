"""Database engine and the per-request session dependency.

The schema is managed only by Alembic migrations; this module never creates tables.
"""

from collections.abc import Iterator

from sqlmodel import Session, create_engine

from app.config import get_settings

settings = get_settings()

# SQLite allows a connection to be used only by the thread that created it by default.
# FastAPI may run a request on a different thread, so this check is turned off for SQLite.
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)


def get_session() -> Iterator[Session]:
    """Give a route one database session and close it when the request is done.

    Usage in a route: `session: Session = Depends(get_session)`
    """
    with Session(engine) as session:
        yield session
