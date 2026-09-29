"""
Repository layer for investigation history.

Keeping this separate from the SQLAlchemy models (models.py) and from the
agent means the agent never imports SQLAlchemy directly -- it produces a
plain `InvestigationResult` (agent/models.py), and this module is
responsible for translating that into rows and back.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.models import InvestigationResult, ToolCallRecord
from database.models import Investigation, Observation, Server, ToolCallLog

# Numeric fields inside tool results worth indexing as Observations, so
# "have we seen this before?" queries can search across them without
# parsing raw JSON at query time. Extend this list as new tools are added.
OBSERVABLE_METRIC_KEYS = {
    "percent",  # appears in cpu/memory/disk results; disambiguated by tool name below
    "tps_1m",
    "tps_5m",
    "tps_15m",
    "mspt_avg",
    "player_count",
}


def get_or_create_server(session: Session, name: str, minecraft_version: str | None = None) -> Server:
    server = session.execute(select(Server).where(Server.name == name)).scalar_one_or_none()
    if server is not None:
        return server

    server = Server(name=name, minecraft_version=minecraft_version)
    session.add(server)
    session.commit()
    session.refresh(server)
    return server


def _extract_observations(tool_calls: list[ToolCallRecord]) -> list[tuple[str, str]]:
    """
    Pull a flat list of (metric_name, value) pairs out of raw tool results.

    Metric names are namespaced by tool (e.g. "get_cpu_usage.percent") so
    "percent" from CPU, memory, and disk tools never collide with each other.
    """
    observations: list[tuple[str, str]] = []
    for call in tool_calls:
        for key, value in call.result.items():
            if key in OBSERVABLE_METRIC_KEYS and value is not None:
                observations.append((f"{call.tool_name}.{key}", str(value)))
    return observations


def save_investigation(
    session: Session,
    server_id: int,
    result: InvestigationResult,
    severity: str | None = None,
    resolved: bool = False,
) -> Investigation:
    """Persist a completed InvestigationResult as an Investigation row, plus
    its tool_calls and derived observations, in one transaction."""
    investigation = Investigation(
        server_id=server_id,
        user_question=result.question,
        started_at=result.started_at,
        completed_at=result.completed_at,
        diagnosis=result.answer,
        severity=severity,
        resolved=resolved,
    )
    session.add(investigation)
    session.flush()  # assigns investigation.id without committing yet

    for call in result.tool_calls:
        session.add(
            ToolCallLog(
                investigation_id=investigation.id,
                tool_name=call.tool_name,
                arguments=call.arguments,
                result=call.result,
                timestamp=call.timestamp,
            )
        )

    for metric, value in _extract_observations(result.tool_calls):
        session.add(Observation(investigation_id=investigation.id, metric=metric, value=value))

    session.commit()
    session.refresh(investigation)
    return investigation


def get_investigation(session: Session, investigation_id: int) -> Investigation | None:
    return session.get(Investigation, investigation_id)


def find_similar_investigations(
    session: Session,
    server_id: int,
    metric: str,
    value: float,
    tolerance_percent: float = 15.0,
    exclude_investigation_id: int | None = None,
    limit: int = 5,
) -> list[Investigation]:
    """
    Find past investigations on this server with an observation for `metric`
    within `tolerance_percent` of `value`.

    This is a similarity heuristic, not a proof of shared root cause -- the
    agent's system prompt requires it to say so explicitly when using this.
    """
    low = value * (1 - tolerance_percent / 100)
    high = value * (1 + tolerance_percent / 100)

    stmt = (
        select(Investigation)
        .join(Observation, Observation.investigation_id == Investigation.id)
        .where(Investigation.server_id == server_id)
        .where(Observation.metric == metric)
    )
    if exclude_investigation_id is not None:
        stmt = stmt.where(Investigation.id != exclude_investigation_id)

    candidates = session.execute(stmt).scalars().all()

    matches = []
    for inv in candidates:
        for obs in inv.observations:
            if obs.metric != metric:
                continue
            try:
                obs_value = float(obs.value)
            except ValueError:
                continue
            if low <= obs_value <= high:
                matches.append(inv)
                break

    matches.sort(key=lambda inv: inv.started_at, reverse=True)
    return matches[:limit]
