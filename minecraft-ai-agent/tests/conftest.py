"""
Shared pytest fixtures.

`db_session` gives every test module a fresh, isolated in-memory SQLite
database that uses the EXACT SAME SQLAlchemy models as production Postgres.
This keeps the test suite fast and dependency-free (no real Postgres needed
to run `pytest`) while still exercising real ORM/query behavior.
"""

from __future__ import annotations

import pytest

from database.database import create_db_engine, init_db, make_session_factory


@pytest.fixture
def db_session():
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
