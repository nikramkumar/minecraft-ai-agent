from __future__ import annotations

from pydantic import BaseModel

from monitoring.models import DiskUsage, MemoryUsage, SystemUptime


class SystemMetricsResponse(BaseModel):
    """Combined Linux + Minecraft-performance metrics for GET /server/metrics."""

    cpu_percent: float
    memory: MemoryUsage
    disk: DiskUsage
    uptime: SystemUptime
    tps_1m: float | None = None
    mspt_avg: float | None = None
    performance_error: str | None = None


class LogsQueryParams(BaseModel):
    query: str | None = None
    max_lines: int = 100
