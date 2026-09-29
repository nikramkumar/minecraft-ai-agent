"""
Tests for monitoring.minecraft_monitor.MinecraftMonitor.

We mock the two network dependencies (mcstatus ping, RCON) so this test
suite runs fully offline -- exactly the "server is offline / unreachable"
scenarios the project spec calls out are tested here using real code paths,
not skipped.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from monitoring.minecraft_monitor import (
    MinecraftMonitor,
    MinecraftUnreachableError,
    RconNotConfiguredError,
)
from monitoring.rcon import RconConnectionError


def make_monitor(tmp_path, rcon_password: str = "testpass") -> MinecraftMonitor:
    log_path = tmp_path / "latest.log"
    log_path.write_text("[INFO]: Server started\n")
    props_path = tmp_path / "server.properties"
    props_path.write_text("max-players=20\nrcon.password=supersecret\n")

    return MinecraftMonitor(
        host="localhost",
        port=25565,
        rcon_host="localhost",
        rcon_port=25575,
        rcon_password=rcon_password,
        log_path=str(log_path),
        properties_path=str(props_path),
    )


def fake_status_response(online: int = 3, max_players: int = 20, sample_names: list[str] | None = None):
    """Build a fake object shaped like mcstatus's JavaStatusResponse."""
    sample = [SimpleNamespace(name=n, id="uuid") for n in (sample_names or [])]
    return SimpleNamespace(
        version=SimpleNamespace(name="1.20.4 (Paper)"),
        players=SimpleNamespace(online=online, max=max_players, sample=sample),
        motd=SimpleNamespace(to_plain=lambda: "A Minecraft Server"),
        latency=12.34,
    )


class TestGetServerStatus:
    def test_running_server_returns_populated_status(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_ping", return_value=fake_status_response(online=5)):
            status = monitor.get_server_status()

        assert status.running is True
        assert status.player_count == 5
        assert status.version == "1.20.4 (Paper)"
        assert status.motd == "A Minecraft Server"

    def test_unreachable_server_returns_running_false(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_ping", side_effect=ConnectionRefusedError("no server")):
            status = monitor.get_server_status()

        assert status.running is False
        assert status.player_count is None


class TestGetOnlinePlayers:
    def test_returns_names_when_sample_present(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(
            monitor, "_ping", return_value=fake_status_response(online=2, sample_names=["Alice", "Bob"])
        ):
            players = monitor.get_online_players()

        assert players.count == 2
        assert players.names == ["Alice", "Bob"]
        assert players.names_available is True

    def test_flags_names_unavailable_when_count_positive_but_sample_empty(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_ping", return_value=fake_status_response(online=4, sample_names=[])):
            players = monitor.get_online_players()

        assert players.count == 4
        assert players.names == []
        assert players.names_available is False

    def test_zero_players_is_not_flagged_as_unavailable(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_ping", return_value=fake_status_response(online=0, sample_names=[])):
            players = monitor.get_online_players()

        assert players.names_available is True

    def test_unreachable_server_raises(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_ping", side_effect=OSError("offline")):
            with pytest.raises(MinecraftUnreachableError):
                monitor.get_online_players()


class TestGetTps:
    def test_parses_standard_paper_tps_response(self, tmp_path):
        monitor = make_monitor(tmp_path)
        raw = "§aTPS from last 1m, 5m, 15m: §a20.0, §a19.87, §a20.0"
        with patch.object(monitor, "_run_rcon_command", return_value=raw):
            tps = monitor.get_tps()

        assert tps.tps_1m == 20.0
        assert tps.tps_5m == 19.87
        assert tps.tps_15m == 20.0

    def test_missing_password_raises_before_any_network_call(self, tmp_path):
        monitor = make_monitor(tmp_path, rcon_password="")
        with pytest.raises(RconNotConfiguredError):
            monitor.get_tps()

    def test_unparseable_response_returns_none_fields_not_crash(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_run_rcon_command", return_value="unexpected garbage"):
            tps = monitor.get_tps()

        assert tps.tps_1m is None
        assert tps.raw_response == "unexpected garbage"

    def test_rcon_connection_failure_is_translated(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch(
            "monitoring.minecraft_monitor.RconClient",
            side_effect=RconConnectionError("connection refused"),
        ):
            with pytest.raises(MinecraftUnreachableError):
                monitor.get_tps()


class TestGetMspt:
    def test_parses_tick_time_triplet(self, tmp_path):
        monitor = make_monitor(tmp_path)
        raw = "Server tick times (avg/min/max) from last 5s, 10s: 12.3/8.1/45.6ms, 11.0/7.9/40.0ms"
        with patch.object(monitor, "_run_rcon_command", return_value=raw):
            mspt = monitor.get_mspt()

        assert mspt.mspt_avg == 12.3
        assert mspt.mspt_min == 8.1
        assert mspt.mspt_max == 45.6

    def test_older_paper_without_mspt_returns_none_fields(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_run_rcon_command", return_value="TPS from last 1m: 20.0"):
            mspt = monitor.get_mspt()

        assert mspt.mspt_avg is None


class TestGetPlugins:
    def test_parses_plugin_list(self, tmp_path):
        monitor = make_monitor(tmp_path)
        raw = "§aPlugins (3): §aEssentialsX, §aWorldEdit, §aVault"
        with patch.object(monitor, "_run_rcon_command", return_value=raw):
            plugins = monitor.get_plugins()

        assert plugins.count == 3
        assert plugins.names == ["EssentialsX", "WorldEdit", "Vault"]

    def test_zero_plugins(self, tmp_path):
        monitor = make_monitor(tmp_path)
        with patch.object(monitor, "_run_rcon_command", return_value="Plugins (0): "):
            plugins = monitor.get_plugins()

        assert plugins.count == 0


class TestGetServerProperties:
    def test_filters_sensitive_keys(self, tmp_path):
        monitor = make_monitor(tmp_path)
        props = monitor.get_server_properties()

        assert props.values["max-players"] == "20"
        assert "rcon.password" not in props.values
        assert "rcon.password" in props.excluded_keys


class TestLogMethods:
    def test_get_recent_logs_reads_file(self, tmp_path):
        monitor = make_monitor(tmp_path)
        result = monitor.get_recent_logs(max_lines=10)
        assert "Server started" in result.lines[0]

    def test_search_logs_delegates_to_parser(self, tmp_path):
        monitor = make_monitor(tmp_path)
        result = monitor.search_logs("started")
        assert len(result.lines) == 1
