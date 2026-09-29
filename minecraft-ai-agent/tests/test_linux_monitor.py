"""
Tests for monitoring.linux_monitor.LinuxMonitor.

These tests run against the REAL host (via psutil), since this module's job
is literally "read the real machine." We can't assert exact values (CPU
usage isn't deterministic), so instead we assert:

  1. The returned data has the right shape and sane bounds.
  2. Error paths behave correctly (bad disk path -> our specific exception).

This is the "meaningful tests, not just HTTP 200" principle applied to a
non-HTTP module: we're checking behavior and contracts, not just "did it not
crash."
"""

from __future__ import annotations

import pytest

from monitoring.linux_monitor import DiskPathNotFoundError, LinuxMonitor
from monitoring.models import CpuUsage, DiskUsage, MemoryUsage, SystemReport, SystemUptime


@pytest.fixture
def monitor() -> LinuxMonitor:
    return LinuxMonitor(disk_path="/")


class TestCpuUsage:
    def test_returns_cpu_usage_model(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_cpu_usage()
        assert isinstance(result, CpuUsage)

    def test_percent_within_valid_bounds(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_cpu_usage()
        assert 0.0 <= result.percent <= 100.0

    def test_core_count_is_positive(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_cpu_usage()
        assert result.core_count >= 1

    def test_load_average_is_three_values_when_present(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_cpu_usage()
        # On Linux this should be populated; if it's None we still want to
        # know the type didn't drift.
        if result.load_average is not None:
            assert len(result.load_average) == 3
            assert all(isinstance(v, float) for v in result.load_average)


class TestMemoryUsage:
    def test_returns_memory_usage_model(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_memory_usage()
        assert isinstance(result, MemoryUsage)

    def test_used_does_not_exceed_total(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_memory_usage()
        assert result.used_gb <= result.total_gb

    def test_percent_within_valid_bounds(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_memory_usage()
        assert 0.0 <= result.percent <= 100.0

    def test_total_is_positive(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_memory_usage()
        assert result.total_gb > 0


class TestDiskUsage:
    def test_returns_disk_usage_for_root(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_disk_usage()
        assert isinstance(result, DiskUsage)
        assert result.path == "/"

    def test_used_does_not_exceed_total(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_disk_usage()
        assert result.used_gb <= result.total_gb

    def test_nonexistent_path_raises_specific_error(self) -> None:
        """
        This is the important error-handling test: a monitoring tool that
        silently returns 0% disk usage for a typo'd path is dangerous. It
        must fail loudly with a specific, catchable exception type.
        """
        broken_monitor = LinuxMonitor(disk_path="/this/path/does/not/exist/anywhere")
        with pytest.raises(DiskPathNotFoundError):
            broken_monitor.get_disk_usage()


class TestSystemUptime:
    def test_returns_system_uptime_model(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_system_uptime()
        assert isinstance(result, SystemUptime)

    def test_uptime_is_non_negative(self, monitor: LinuxMonitor) -> None:
        result = monitor.get_system_uptime()
        assert result.uptime_seconds >= 0

    def test_boot_time_is_in_the_past(self, monitor: LinuxMonitor) -> None:
        from datetime import datetime, timezone

        result = monitor.get_system_uptime()
        assert result.boot_time <= datetime.now(timezone.utc)


class TestFullReport:
    def test_combines_all_metrics(self, monitor: LinuxMonitor) -> None:
        report = monitor.get_full_report()
        assert isinstance(report, SystemReport)
        assert isinstance(report.cpu, CpuUsage)
        assert isinstance(report.memory, MemoryUsage)
        assert isinstance(report.disk, DiskUsage)
        assert isinstance(report.uptime, SystemUptime)

    def test_full_report_serializes_to_dict(self, monitor: LinuxMonitor) -> None:
        """
        This is the contract the future API layer depends on: the report
        must serialize cleanly, since FastAPI will return it as JSON.
        """
        report = monitor.get_full_report()
        data = report.model_dump(mode="json")
        assert "cpu" in data
        assert "memory" in data
        assert "disk" in data
        assert "uptime" in data
