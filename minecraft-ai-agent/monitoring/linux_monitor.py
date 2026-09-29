"""
Linux host monitoring.

This module knows nothing about Minecraft, LLMs, tools, or HTTP. It has one
job: read real state from the operating system via `psutil` and return it as
the typed models defined in `models.py`.

Every public method can raise `MonitoringError` (or a subclass) if the
underlying data cannot be collected. Callers (later: agent tools, API routes)
are responsible for deciding how to present that failure -- this layer never
fabricates a fallback value.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import psutil

from monitoring.models import CpuUsage, DiskUsage, MemoryUsage, SystemReport, SystemUptime


class MonitoringError(Exception):
    """Base class for errors raised by the monitoring layer."""


class DiskPathNotFoundError(MonitoringError):
    """Raised when the configured disk path does not exist on this host."""


BYTES_PER_GB = 1024 ** 3


class LinuxMonitor:
    """
    Collects real-time Linux host metrics.

    Parameters
    ----------
    disk_path:
        Filesystem path to measure disk usage for. Defaults to "/". Tests
        (or a config file later) can override this, e.g. to point at the
        Minecraft server's world directory instead of the root filesystem.
    """

    def __init__(self, disk_path: str = "/") -> None:
        self._disk_path = disk_path

    def get_cpu_usage(self) -> CpuUsage:
        """
        Return current CPU utilization.

        `psutil.cpu_percent(interval=0.5)` blocks for 0.5s to measure CPU
        over an interval -- a 0-interval call would return a meaningless
        value (often 0.0) on first invocation. This is a real, intentional
        latency cost, not a bug.
        """
        percent = psutil.cpu_percent(interval=0.5)
        core_count = psutil.cpu_count(logical=True) or 1

        load_average = None
        # getloadavg() exists on Linux/macOS but not on Windows.
        if hasattr(os, "getloadavg"):
            try:
                load_average = os.getloadavg()
            except OSError:
                # Can happen in some restricted containers even when the
                # attribute exists. Degrade gracefully -- this one field is
                # optional, unlike percent/core_count.
                load_average = None

        return CpuUsage(percent=percent, core_count=core_count, load_average=load_average)

    def get_memory_usage(self) -> MemoryUsage:
        """Return current physical memory usage."""
        vm = psutil.virtual_memory()
        return MemoryUsage(
            used_gb=round(vm.used / BYTES_PER_GB, 2),
            total_gb=round(vm.total / BYTES_PER_GB, 2),
            percent=vm.percent,
        )

    def get_disk_usage(self) -> DiskUsage:
        """
        Return disk usage for the configured path.

        Raises
        ------
        DiskPathNotFoundError
            If the configured path does not exist. We check explicitly
            rather than letting psutil's OSError bubble up, so callers get
            a clear, typed error instead of a generic exception.
        """
        if not os.path.exists(self._disk_path):
            raise DiskPathNotFoundError(f"Disk path does not exist: {self._disk_path}")

        usage = psutil.disk_usage(self._disk_path)
        return DiskUsage(
            path=self._disk_path,
            used_gb=round(usage.used / BYTES_PER_GB, 2),
            total_gb=round(usage.total / BYTES_PER_GB, 2),
            percent=usage.percent,
        )

    def get_system_uptime(self) -> SystemUptime:
        """Return system boot time and elapsed uptime."""
        boot_timestamp = psutil.boot_time()
        boot_time = datetime.fromtimestamp(boot_timestamp, tz=timezone.utc)
        uptime_seconds = datetime.now(timezone.utc).timestamp() - boot_timestamp
        return SystemUptime(boot_time=boot_time, uptime_seconds=uptime_seconds)

    def get_full_report(self) -> SystemReport:
        """
        Return a single combined snapshot of CPU, memory, disk, and uptime.

        This is the method the agent's tools will eventually wrap -- one
        call, one structured result, easy for an LLM to reason about.
        """
        return SystemReport(
            cpu=self.get_cpu_usage(),
            memory=self.get_memory_usage(),
            disk=self.get_disk_usage(),
            uptime=self.get_system_uptime(),
        )
