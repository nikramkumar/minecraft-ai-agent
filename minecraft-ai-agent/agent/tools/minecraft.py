"""Minecraft monitoring tools exposed to the agent."""

from __future__ import annotations

from monitoring.minecraft_monitor import MinecraftMonitor
from monitoring.models import LogLines, ServerStatus

from agent.tools.base import Tool

EMPTY_SCHEMA = {"type": "object", "properties": {}}

MAX_LOG_LINES_CAP = 500
MAX_SEARCH_RESULTS_CAP = 200


class ToolInputError(ValueError):
    """Raised when a tool receives invalid input; surfaced as a structured error, not a crash."""


def build_minecraft_tools(monitor: MinecraftMonitor) -> list[Tool]:
    def get_player_count() -> dict:
        status: ServerStatus = monitor.get_server_status()
        return {"running": status.running, "player_count": status.player_count}

    def get_server_version() -> dict:
        status: ServerStatus = monitor.get_server_status()
        return {"running": status.running, "version": status.version}

    def get_recent_logs(max_lines: int = 100) -> LogLines:
        if not isinstance(max_lines, int) or max_lines <= 0:
            raise ToolInputError("max_lines must be a positive integer")
        max_lines = min(max_lines, MAX_LOG_LINES_CAP)
        return monitor.get_recent_logs(max_lines=max_lines)

    def search_logs(query: str, max_results: int = 50) -> LogLines:
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("query must be a non-empty string")
        if not isinstance(max_results, int) or max_results <= 0:
            raise ToolInputError("max_results must be a positive integer")
        max_results = min(max_results, MAX_SEARCH_RESULTS_CAP)
        return monitor.search_logs(query=query, max_results=max_results)

    return [
        Tool(
            name="get_server_status",
            description=(
                "Check whether the Minecraft server is running and reachable. Returns running "
                "status, version, player count, max players, MOTD, and ping latency."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_server_status,
        ),
        Tool(
            name="get_player_count",
            description="Get just the number of players currently online (and whether the server is running).",
            input_schema=EMPTY_SCHEMA,
            handler=get_player_count,
        ),
        Tool(
            name="get_online_players",
            description=(
                "Get the names of players currently online, when the server exposes them. Note: "
                "some servers only report a count, not names -- check 'names_available' in the result."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_online_players,
        ),
        Tool(
            name="get_server_version",
            description="Get the Minecraft/Paper server version string.",
            input_schema=EMPTY_SCHEMA,
            handler=get_server_version,
        ),
        Tool(
            name="get_plugins",
            description=(
                "Get the list of installed plugins via RCON. Requires RCON to be configured; "
                "returns an error if it is not."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_plugins,
        ),
        Tool(
            name="get_server_properties",
            description=(
                "Get the server's configuration from server.properties (e.g. difficulty, "
                "max-players, view-distance). Secret values like RCON/query passwords are withheld."
            ),
            input_schema=EMPTY_SCHEMA,
            handler=monitor.get_server_properties,
        ),
        Tool(
            name="get_recent_logs",
            description="Get the most recent lines from the Minecraft server log file.",
            input_schema={
                "type": "object",
                "properties": {
                    "max_lines": {
                        "type": "integer",
                        "description": f"Number of recent lines to return (max {MAX_LOG_LINES_CAP}). Defaults to 100.",
                        "default": 100,
                    }
                },
            },
            handler=get_recent_logs,
        ),
        Tool(
            name="search_logs",
            description=(
                "Search the Minecraft server log for lines containing a specific substring "
                "(case-insensitive). Useful for finding errors, warnings, or specific events."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Substring to search for"},
                    "max_results": {
                        "type": "integer",
                        "description": f"Maximum matching lines to return (max {MAX_SEARCH_RESULTS_CAP}). Defaults to 50.",
                        "default": 50,
                    },
                },
                "required": ["query"],
            },
            handler=search_logs,
        ),
    ]
