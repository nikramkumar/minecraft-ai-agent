"""
Database engine and session management.

Kept deliberately small: this module's only job is turning a database URL
into a working SQLAlchemy engine and a session factory. Table creation
(`init_db`) is separate from engine creation so tests can call it against an
in-memory SQLite engine without touching Postgres at all.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        # SQLite-specific gotcha: an in-memory SQLite database
        # ("sqlite:///:memory:") normally gets a fresh, independent database
        # per connection. Since we open a new connection per request (via
        # sessionmaker), that would silently give every request an empty,
        # tableless database. StaticPool forces all connections through the
        # SAME underlying connection, so tests see one consistent database.
        # (Not used for Postgres, which doesn't have this behavior.)
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(database_url)


def init_db(engine: Engine) -> None:
    """Create all tables. Fine for this project's scope; a real production
    system would use Alembic migrations instead (see README roadmap)."""
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, always closes it afterward."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
