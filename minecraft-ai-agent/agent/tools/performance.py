"""Minecraft tick-performance tools (TPS/MSPT), split out from general server tools
because they share a distinct dependency: RCON, not just Server List Ping."""

from __future__ import annotations

from monitoring.minecraft_monitor import MinecraftMonitor

from agent.tools.base import Tool

EMPTY_SCHEMA = {"type": "object", "properties": {}}


def build_performance_tools(monitor: MinecraftMonitor) -> list[Tool]:
    return [
        Tool(
            name="get_tps",
            description=(
                "Get the server's ticks-per-second (TPS) over the last 1/5/15 minutes via RCON. "
                "20.0 is ideal; sustained values well below 20 indicate the server can't keep up "
                "with the game's tick rate. Requires RCON to be configured."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_tps,
        ),
        Tool(
            name="get_mspt",
            description=(
                "Get mean/min/max milliseconds-per-tick (MSPT) via RCON. Each tick must complete "
                "within 50ms to sustain 20 TPS, so this is a direct measure of headroom. May "
                "return null fields if the running Paper version doesn't report tick times. "
                "Requires RCON to be configured."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_mspt,
        ),
    ]
