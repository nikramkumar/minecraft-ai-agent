from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_linux_monitor, get_minecraft_monitor
from api.schemas.server import SystemMetricsResponse
from monitoring.linux_monitor import DiskPathNotFoundError, LinuxMonitor
from monitoring.minecraft_monitor import (
    MinecraftMonitor,
    MinecraftUnreachableError,
    RconNotConfiguredError,
)
from monitoring.models import LogLines, OnlinePlayers, ServerStatus
from monitoring.parsers.log_parser import LogFileNotFoundError

router = APIRouter(prefix="/server", tags=["server"])


@router.get("/status", response_model=ServerStatus)
def get_server_status(minecraft: MinecraftMonitor = Depends(get_minecraft_monitor)) -> ServerStatus:
    return minecraft.get_server_status()


@router.get("/metrics", response_model=SystemMetricsResponse)
def get_server_metrics(
    linux: LinuxMonitor = Depends(get_linux_monitor),
    minecraft: MinecraftMonitor = Depends(get_minecraft_monitor),
) -> SystemMetricsResponse:
    cpu = linux.get_cpu_usage()
    memory = linux.get_memory_usage()
    uptime = linux.get_system_uptime()

    try:
        disk = linux.get_disk_usage()
    except DiskPathNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # TPS/MSPT require RCON, which is optional/separately configured -- their
    # unavailability is not a reason to fail the whole metrics endpoint.
    tps_1m: float | None = None
    mspt_avg: float | None = None
    performance_error: str | None = None
    try:
        tps_1m = minecraft.get_tps().tps_1m
        mspt_avg = minecraft.get_mspt().mspt_avg
    except (MinecraftUnreachableError, RconNotConfiguredError) as exc:
        performance_error = str(exc)

    return SystemMetricsResponse(
        cpu_percent=cpu.percent,
        memory=memory,
        disk=disk,
        uptime=uptime,
        tps_1m=tps_1m,
        mspt_avg=mspt_avg,
        performance_error=performance_error,
    )


@router.get("/players", response_model=OnlinePlayers)
def get_server_players(minecraft: MinecraftMonitor = Depends(get_minecraft_monitor)) -> OnlinePlayers:
    try:
        return minecraft.get_online_players()
    except MinecraftUnreachableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/logs", response_model=LogLines)
def get_server_logs(
    query: str | None = Query(default=None, description="Optional substring to search for"),
    max_lines: int = Query(default=100, le=500, description="Max lines to return"),
    minecraft: MinecraftMonitor = Depends(get_minecraft_monitor),
) -> LogLines:
    try:
        if query:
            return minecraft.search_logs(query=query, max_results=max_lines)
        return minecraft.get_recent_logs(max_lines=max_lines)
    except LogFileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
