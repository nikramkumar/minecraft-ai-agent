"""Structured types describing an agent investigation, shared by agent.py,
the API layer (for the response body), and the database layer (for
persistence) -- one shape, three consumers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ToolCallRecord(BaseModel):
    """One tool call the agent made during an investigation."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InvestigationResult(BaseModel):
    """The full outcome of one agent investigation."""

    question: str
    answer: str
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    iterations_used: int
    hit_max_iterations: bool = False
    started_at: datetime
    completed_at: datetime
