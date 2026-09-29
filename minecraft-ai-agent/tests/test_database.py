"""Tests for database.models and database.repositories.investigation_repo,
run against a real (in-memory SQLite) database, not mocks."""

from __future__ import annotations

from datetime import datetime, timezone

from agent.models import InvestigationResult, ToolCallRecord
from database.repositories.investigation_repo import (
    find_similar_investigations,
    get_investigation,
    get_or_create_server,
    save_investigation,
)


def make_result(question="Why is it lagging?", tps_value=13.2, cpu_value=96.0) -> InvestigationResult:
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        question=question,
        answer="Diagnosis text",
        tool_calls=[
            ToolCallRecord(
                tool_name="get_tps",
                arguments={},
                result={"tps_1m": tps_value, "tps_5m": tps_value, "tps_15m": tps_value, "raw_response": "..."},
            ),
            ToolCallRecord(
                tool_name="get_cpu_usage",
                arguments={},
                result={"percent": cpu_value, "core_count": 8},
            ),
        ],
        iterations_used=2,
        started_at=now,
        completed_at=now,
    )


class TestGetOrCreateServer:
    def test_creates_new_server(self, db_session):
        server = get_or_create_server(db_session, "survival-1", minecraft_version="1.20.4")
        assert server.id is not None
        assert server.name == "survival-1"

    def test_returns_existing_server_without_duplicating(self, db_session):
        first = get_or_create_server(db_session, "survival-1")
        second = get_or_create_server(db_session, "survival-1")
        assert first.id == second.id


class TestSaveInvestigation:
    def test_saves_investigation_with_tool_calls_and_observations(self, db_session):
        server = get_or_create_server(db_session, "survival-1")
        result = make_result()

        investigation = save_investigation(db_session, server.id, result, severity="high")

        assert investigation.id is not None
        assert investigation.diagnosis == "Diagnosis text"
        assert investigation.severity == "high"
        assert len(investigation.tool_calls) == 2
        # tps_1m and percent (namespaced) should both be indexed as observations
        metrics = {obs.metric for obs in investigation.observations}
        assert "get_tps.tps_1m" in metrics
        assert "get_cpu_usage.percent" in metrics

    def test_get_investigation_retrieves_saved_record(self, db_session):
        server = get_or_create_server(db_session, "survival-1")
        saved = save_investigation(db_session, server.id, make_result())

        fetched = get_investigation(db_session, saved.id)

        assert fetched is not None
        assert fetched.id == saved.id

    def test_get_investigation_returns_none_for_missing_id(self, db_session):
        assert get_investigation(db_session, 999999) is None


class TestFindSimilarInvestigations:
    def test_finds_past_investigation_with_similar_metric_value(self, db_session):
        server = get_or_create_server(db_session, "survival-1")
        past = save_investigation(db_session, server.id, make_result(tps_value=11.8, cpu_value=97.0))

        similar = find_similar_investigations(
            db_session, server.id, metric="get_tps.tps_1m", value=12.1, tolerance_percent=15.0
        )

        assert past.id in {inv.id for inv in similar}

    def test_excludes_current_investigation(self, db_session):
        server = get_or_create_server(db_session, "survival-1")
        current = save_investigation(db_session, server.id, make_result(tps_value=12.0))

        similar = find_similar_investigations(
            db_session,
            server.id,
            metric="get_tps.tps_1m",
            value=12.0,
            exclude_investigation_id=current.id,
        )

        assert current.id not in {inv.id for inv in similar}

    def test_does_not_match_values_outside_tolerance(self, db_session):
        server = get_or_create_server(db_session, "survival-1")
        save_investigation(db_session, server.id, make_result(tps_value=20.0))

        similar = find_similar_investigations(
            db_session, server.id, metric="get_tps.tps_1m", value=5.0, tolerance_percent=10.0
        )

        assert similar == []

    def test_does_not_match_other_servers(self, db_session):
        server_a = get_or_create_server(db_session, "survival-a")
        server_b = get_or_create_server(db_session, "survival-b")
        save_investigation(db_session, server_a.id, make_result(tps_value=12.0))

        similar = find_similar_investigations(db_session, server_b.id, metric="get_tps.tps_1m", value=12.0)

        assert similar == []
