"""
Structured data models returned by the monitoring layer.

These models are the *contract* between the monitoring layer and everything
that consumes it later (agent tools, the FastAPI layer, the database layer).
Keeping them as explicit Pydantic models -- instead of passing raw dicts
around -- means:

  * Every consumer gets type checking and IDE autocomplete.
  * A typo like `cpu_pct` instead of `cpu_percent` fails fast, at the model
    boundary, instead of silently producing `None` deep inside the agent.
  * We can add `.model_dump()` -> JSON for the API layer for free.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MemoryUsage(BaseModel):
    """Snapshot of system memory usage, in GB and percent."""

    used_gb: float = Field(..., description="Memory currently in use, in GB")
    total_gb: float = Field(..., description="Total physical memory, in GB")
    percent: float = Field(..., ge=0, le=100, description="Percent of memory used")


class CpuUsage(BaseModel):
    """Snapshot of CPU utilization."""

    percent: float = Field(..., ge=0, le=100, description="Overall CPU utilization percent")
    core_count: int = Field(..., gt=0, description="Number of logical CPU cores")
    load_average: tuple[float, float, float] | None = Field(
        default=None,
        description="1/5/15 minute load averages, if the OS provides them (Linux/macOS only)",
    )


class DiskUsage(BaseModel):
    """Snapshot of disk usage for a specific mount path."""

    path: str = Field(..., description="Filesystem path this measurement is for")
    used_gb: float = Field(..., description="Disk space used, in GB")
    total_gb: float = Field(..., description="Total disk space, in GB")
    percent: float = Field(..., ge=0, le=100, description="Percent of disk used")


class SystemUptime(BaseModel):
    """How long the host has been running."""

    boot_time: datetime = Field(..., description="UTC timestamp the system booted")
    uptime_seconds: float = Field(..., ge=0, description="Seconds since boot")


class SystemReport(BaseModel):
    """A full snapshot of Linux host health at one point in time."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cpu: CpuUsage
    memory: MemoryUsage
    disk: DiskUsage
    uptime: SystemUptime


# ---------------------------------------------------------------------------
# Minecraft models
# ---------------------------------------------------------------------------


class ServerStatus(BaseModel):
    """Whether the Minecraft server is reachable, and basic identity info."""

    running: bool = Field(..., description="Whether the server responded to a status ping")
    version: str | None = Field(default=None, description="Reported Minecraft/Paper version string")
    player_count: int | None = Field(default=None, description="Online players, from the ping response")
    max_players: int | None = Field(default=None, description="Configured max player slots")
    motd: str | None = Field(default=None, description="Server's message of the day")
    latency_ms: float | None = Field(default=None, description="Round-trip ping latency")


class OnlinePlayers(BaseModel):
    """Named list of currently online players."""

    count: int = Field(..., ge=0)
    names: list[str] = Field(default_factory=list)
    names_available: bool = Field(
        default=True,
        description=(
            "False when the server only reports a count, not names (vanilla Server List "
            "Ping without 'query' enabled often omits or truncates the sample list)."
        ),
    )


class PluginInfo(BaseModel):
    """Installed plugins, as reported by the Paper/Bukkit 'plugins' console command."""

    count: int = Field(..., ge=0)
    names: list[str] = Field(default_factory=list)


class ServerProperties(BaseModel):
    """
    A curated subset of server.properties.

    We deliberately do NOT expose every key: fields like rcon.password and
    query.password are secrets and are excluded even though they live in the
    same file, so a tool call can never leak them to the LLM or the API.
    """

    values: dict[str, str] = Field(default_factory=dict)
    excluded_keys: list[str] = Field(
        default_factory=list, description="Sensitive keys present in the file but withheld"
    )


class LogLines(BaseModel):
    """A batch of raw Minecraft server log lines."""

    lines: list[str] = Field(default_factory=list)
    source_file: str
    truncated: bool = Field(
        default=False, description="True if more matching lines existed than were returned"
    )


class TpsSnapshot(BaseModel):
    """
    Ticks-per-second, as reported by Paper's '/tps' console command via RCON.

    Ideal is 20.0. Sustained values well below 20 indicate the main thread
    cannot keep up with the tick rate.
    """

    tps_1m: float | None = None
    tps_5m: float | None = None
    tps_15m: float | None = None
    raw_response: str = Field(..., description="Unparsed RCON response, kept for debugging")


class MsptSnapshot(BaseModel):
    """
    Mean milliseconds-per-tick, as reported by Paper's '/tps' command (recent
    Paper versions include tick-time alongside TPS in the same response).

    20 TPS requires each tick to complete in <= 50ms, so MSPT is often a more
    direct signal of headroom than TPS alone.
    """

    mspt_avg: float | None = None
    mspt_min: float | None = None
    mspt_max: float | None = None
    raw_response: str = Field(..., description="Unparsed RCON response, kept for debugging")


class MinecraftReport(BaseModel):
    """Combined Minecraft snapshot, mirroring SystemReport for the Linux side."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: ServerStatus
    players: OnlinePlayers | None = None
    plugins: PluginInfo | None = None
    tps: TpsSnapshot | None = None
    mspt: MsptSnapshot | None = None
