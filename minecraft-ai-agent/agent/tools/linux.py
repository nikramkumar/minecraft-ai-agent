"""
Linux monitoring tools exposed to the agent.

Each `build_*_tools` function in this package takes an already-constructed
monitor and returns a list of `Tool` objects. Keeping construction (in
api/dependencies.py) separate from tool wrapping (here) means tests can pass
in a fake or mocked monitor without touching real config/env vars.
"""

from __future__ import annotations

from monitoring.linux_monitor import LinuxMonitor

from agent.tools.base import Tool

EMPTY_SCHEMA = {"type": "object", "properties": {}}


def build_linux_tools(monitor: LinuxMonitor) -> list[Tool]:
    return [
        Tool(
            name="get_cpu_usage",
            description=(
                "Get current CPU utilization percentage and core count for the host machine "
                "running the Minecraft server. Takes ~0.5s to measure."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_cpu_usage,
        ),
        Tool(
            name="get_memory_usage",
            description="Get current physical memory (RAM) usage in GB and percent for the host machine.",
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_memory_usage,
        ),
        Tool(
            name="get_disk_usage",
            description="Get current disk usage in GB and percent for the host's configured disk path.",
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_disk_usage,
        ),
        Tool(
            name="get_system_uptime",
            description="Get how long the host machine has been running since its last boot.",
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_system_uptime,
        ),
    ]
