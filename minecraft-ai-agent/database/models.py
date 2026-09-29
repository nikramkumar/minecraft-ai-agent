"""
SQLAlchemy ORM models for incident history.

Schema (mirrors the spec):
  servers        -- one row per Minecraft server instance this system watches
  investigations -- one row per agent.investigate() call
  observations   -- key metrics extracted from an investigation, for fast
                     historical comparison ("have we seen TPS this low before?")
  tool_calls     -- full audit trail of every tool the agent invoked

We use SQLAlchemy's generic `JSON` type (not Postgres-specific `JSONB`) for
tool arguments/results so the exact same models work against both real
PostgreSQL (production) and SQLite (fast, dependency-free tests). See
tests/conftest.py for how tests wire this up.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    minecraft_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    investigations: Mapped[list["Investigation"]] = relationship(back_populates="server")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id"))
    user_question: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved: Mapped[bool] = mapped_column(default=False)

    server: Mapped["Server"] = relationship(back_populates="investigations")
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["ToolCallLog"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )


class Observation(Base):
    """
    A single named metric extracted from an investigation (e.g. metric="tps",
    value="13.2"). Stored as a flat metric/value pair (not one column per
    metric) so new metric types never require a schema migration, and so a
    "find similar past incidents" query can search across all metrics
    uniformly.
    """

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"))
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)

    investigation: Mapped["Investigation"] = relationship(back_populates="observations")


class ToolCallLog(Base):
    """Audit record of one tool invocation made during an investigation."""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"))
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    timestamp: Mapped[datetime] = mapped_column(default=_utcnow)

    investigation: Mapped["Investigation"] = relationship(back_populates="tool_calls")
