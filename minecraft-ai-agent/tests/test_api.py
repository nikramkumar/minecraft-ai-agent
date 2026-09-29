"""
Tests for the FastAPI layer.

These do NOT test "does it return 200" in isolation -- each test asserts on
actual response content, on error status codes for real failure modes
(offline Minecraft server, missing log file, LLM failure), and on the
side effect of persisting an investigation to the database.

Every external dependency (monitors, the agent/LLM, the DB session) is
swapped via `app.dependency_overrides` for a fake or an in-memory SQLite
session -- no real network, no real Minecraft server, no real Anthropic
API call happens anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent.agent import AgentError
from agent.models import InvestigationResult, ToolCallRecord
from api.dependencies import db_session_dependency, get_agent, get_linux_monitor, get_minecraft_monitor
from api.main import app
from database.database import create_db_engine, init_db, make_session_factory
from monitoring.minecraft_monitor import MinecraftUnreachableError, RconNotConfiguredError
from monitoring.models import CpuUsage, DiskUsage, MemoryUsage, OnlinePlayers, ServerStatus, SystemUptime
from monitoring.parsers.log_parser import LogFileNotFoundError


# --- Fakes ---------------------------------------------------------------


class FakeAgent:
    """A fake IncidentResponseAgent that returns a scripted InvestigationResult
    or raises AgentError, without ever calling a real LLM."""

    def __init__(self, result: InvestigationResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.last_question: str | None = None

    def investigate(self, question: str) -> InvestigationResult:
        self.last_question = question
        if self._error is not None:
            raise self._error
        return self._result


def make_investigation_result(question="Why is my server lagging?") -> InvestigationResult:
    now = datetime.now(timezone.utc)
    return InvestigationResult(
        question=question,
        answer="CPU is at 96%, which is the likely cause of the lag.",
        tool_calls=[
            ToolCallRecord(tool_name="get_cpu_usage", arguments={}, result={"percent": 96.0, "core_count": 8})
        ],
        iterations_used=2,
        hit_max_iterations=False,
        started_at=now,
        completed_at=now,
    )


@pytest.fixture
def client():
    """
    A TestClient with every external dependency overridden. Each test further
    overrides get_agent / monitors as needed for its scenario; this fixture
    just guarantees a clean, isolated DB per test and resets overrides after.
    """
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = make_session_factory(engine)

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_session_dependency] = override_db

    yield TestClient(app)

    app.dependency_overrides.clear()
    engine.dispose()


# --- POST /chat ------------------------------------------------------------


class TestChatEndpoint:
    def test_successful_investigation_returns_answer_and_tool_calls(self, client):
        app.dependency_overrides[get_agent] = lambda: FakeAgent(result=make_investigation_result())

        response = client.post("/chat", json={"message": "Why is my server lagging?"})

        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "CPU is at 96%, which is the likely cause of the lag."
        assert len(body["tool_calls"]) == 1
        assert body["tool_calls"][0]["tool_name"] == "get_cpu_usage"
        assert "investigation_id" in body

    def test_investigation_is_persisted_to_database(self, client):
        result = make_investigation_result()
        app.dependency_overrides[get_agent] = lambda: FakeAgent(result=result)

        chat_response = client.post("/chat", json={"message": result.question})
        investigation_id = chat_response.json()["investigation_id"]

        fetch_response = client.get(f"/investigations/{investigation_id}")
        assert fetch_response.status_code == 200
        assert fetch_response.json()["diagnosis"] == result.answer

    def test_agent_error_returns_502_not_500_crash(self, client):
        app.dependency_overrides[get_agent] = lambda: FakeAgent(error=AgentError("LLM call failed: timeout"))

        response = client.post("/chat", json={"message": "Why is my server lagging?"})

        assert response.status_code == 502
        assert "timeout" in response.json()["detail"]

    def test_empty_message_is_rejected_by_validation(self, client):
        # Override the agent so this test isolates body validation, not
        # unrelated LLM-configuration state.
        app.dependency_overrides[get_agent] = lambda: FakeAgent(result=make_investigation_result())
        response = client.post("/chat", json={"message": ""})
        assert response.status_code == 422

    def test_missing_message_field_is_rejected(self, client):
        app.dependency_overrides[get_agent] = lambda: FakeAgent(result=make_investigation_result())
        response = client.post("/chat", json={})
        assert response.status_code == 422


# --- GET /server/status ------------------------------------------------------


class TestServerStatusEndpoint:
    def test_running_server_status(self, client):
        monitor = MagicMock()
        monitor.get_server_status.return_value = ServerStatus(
            running=True, version="1.20.4 (Paper)", player_count=3, max_players=20
        )
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/status")

        assert response.status_code == 200
        assert response.json()["running"] is True
        assert response.json()["player_count"] == 3

    def test_offline_server_status_is_still_a_200_with_running_false(self, client):
        """
        An offline Minecraft server is expected, routine state -- not an API
        error. The endpoint should describe it, not fail.
        """
        monitor = MagicMock()
        monitor.get_server_status.return_value = ServerStatus(running=False)
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/status")

        assert response.status_code == 200
        assert response.json()["running"] is False
        assert response.json()["player_count"] is None


# --- GET /server/metrics ------------------------------------------------------


class TestServerMetricsEndpoint:
    def test_full_metrics_when_everything_available(self, client):
        linux = MagicMock()
        linux.get_cpu_usage.return_value = CpuUsage(percent=50.0, core_count=8)
        linux.get_memory_usage.return_value = MemoryUsage(used_gb=4.0, total_gb=16.0, percent=25.0)
        linux.get_disk_usage.return_value = DiskUsage(path="/", used_gb=100.0, total_gb=500.0, percent=20.0)
        linux.get_system_uptime.return_value = SystemUptime(
            boot_time=datetime.now(timezone.utc), uptime_seconds=3600.0
        )
        app.dependency_overrides[get_linux_monitor] = lambda: linux

        minecraft = MagicMock()
        from monitoring.models import MsptSnapshot, TpsSnapshot

        minecraft.get_tps.return_value = TpsSnapshot(tps_1m=20.0, tps_5m=20.0, tps_15m=20.0, raw_response="...")
        minecraft.get_mspt.return_value = MsptSnapshot(mspt_avg=10.0, mspt_min=5.0, mspt_max=20.0, raw_response="...")
        app.dependency_overrides[get_minecraft_monitor] = lambda: minecraft

        response = client.get("/server/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["cpu_percent"] == 50.0
        assert body["tps_1m"] == 20.0
        assert body["performance_error"] is None

    def test_metrics_degrades_gracefully_when_rcon_not_configured(self, client):
        linux = MagicMock()
        linux.get_cpu_usage.return_value = CpuUsage(percent=50.0, core_count=8)
        linux.get_memory_usage.return_value = MemoryUsage(used_gb=4.0, total_gb=16.0, percent=25.0)
        linux.get_disk_usage.return_value = DiskUsage(path="/", used_gb=100.0, total_gb=500.0, percent=20.0)
        linux.get_system_uptime.return_value = SystemUptime(
            boot_time=datetime.now(timezone.utc), uptime_seconds=3600.0
        )
        app.dependency_overrides[get_linux_monitor] = lambda: linux

        minecraft = MagicMock()
        minecraft.get_tps.side_effect = RconNotConfiguredError("RCON password is not configured")
        app.dependency_overrides[get_minecraft_monitor] = lambda: minecraft

        response = client.get("/server/metrics")

        assert response.status_code == 200
        body = response.json()
        assert body["tps_1m"] is None
        assert "RCON" in body["performance_error"]


# --- GET /server/players -----------------------------------------------------


class TestServerPlayersEndpoint:
    def test_returns_online_players(self, client):
        monitor = MagicMock()
        monitor.get_online_players.return_value = OnlinePlayers(count=2, names=["Alice", "Bob"])
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/players")

        assert response.status_code == 200
        assert response.json()["names"] == ["Alice", "Bob"]

    def test_unreachable_server_returns_503(self, client):
        monitor = MagicMock()
        monitor.get_online_players.side_effect = MinecraftUnreachableError("Could not reach Minecraft server")
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/players")

        assert response.status_code == 503


# --- GET /server/logs ---------------------------------------------------------


class TestServerLogsEndpoint:
    def test_returns_recent_logs_by_default(self, client):
        from monitoring.models import LogLines

        monitor = MagicMock()
        monitor.get_recent_logs.return_value = LogLines(lines=["line1", "line2"], source_file="latest.log")
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/logs")

        assert response.status_code == 200
        assert response.json()["lines"] == ["line1", "line2"]
        monitor.get_recent_logs.assert_called_once()
        monitor.search_logs.assert_not_called()

    def test_search_query_param_triggers_search_logs(self, client):
        from monitoring.models import LogLines

        monitor = MagicMock()
        monitor.search_logs.return_value = LogLines(lines=["error line"], source_file="latest.log")
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/logs", params={"query": "error"})

        assert response.status_code == 200
        monitor.search_logs.assert_called_once()

    def test_missing_log_file_returns_404(self, client):
        monitor = MagicMock()
        monitor.get_recent_logs.side_effect = LogFileNotFoundError("Minecraft log file not found")
        app.dependency_overrides[get_minecraft_monitor] = lambda: monitor

        response = client.get("/server/logs")

        assert response.status_code == 404

    def test_max_lines_over_cap_is_rejected_by_validation(self, client):
        response = client.get("/server/logs", params={"max_lines": 99999})
        assert response.status_code == 422


# --- Investigations -----------------------------------------------------------


class TestInvestigationsEndpoints:
    def test_create_and_read_investigation(self, client):
        result = make_investigation_result("Are we running out of memory?")
        app.dependency_overrides[get_agent] = lambda: FakeAgent(result=result)

        create_response = client.post(
            "/investigations", json={"question": result.question, "server_name": "survival-1"}
        )
        assert create_response.status_code == 200
        investigation_id = create_response.json()["id"]

        read_response = client.get(f"/investigations/{investigation_id}")
        assert read_response.status_code == 200
        assert read_response.json()["user_question"] == result.question

    def test_read_nonexistent_investigation_returns_404(self, client):
        response = client.get("/investigations/999999")
        assert response.status_code == 404


# --- Health --------------------------------------------------------------------


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
