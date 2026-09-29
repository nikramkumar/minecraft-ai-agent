"""
Minecraft server monitoring.

This module talks to a real Minecraft/Paper server through three distinct
mechanisms, each with different capabilities and failure modes:

1. **Server List Ping** (via `mcstatus`) -- the same unauthenticated protocol
   the Minecraft client uses to show server info in the multiplayer list.
   No password needed, but it only gives status/version/player count, and
   often only a *sample* of player names, not TPS or plugins.

2. **RCON** (via `rcon.RconClient`) -- an authenticated console protocol.
   Required for anything vanilla ping can't provide: TPS, MSPT, and the
   installed plugin list. This requires `rcon.password` to be set and
   `enable-rcon=true` in server.properties.

3. **Direct file access** -- server.properties and the log files are just
   files on disk; no protocol needed, just correct parsing (see
   parsers/log_parser.py).

Each capability is exposed as its own method so a caller (a tool) can fail
independently -- e.g. RCON being misconfigured shouldn't block reading logs.
"""

from __future__ import annotations

import re

from mcstatus import JavaServer
from mcstatus.responses import JavaStatusResponse

from monitoring.models import (
    LogLines,
    MsptSnapshot,
    OnlinePlayers,
    PluginInfo,
    ServerProperties,
    ServerStatus,
    TpsSnapshot,
)
from monitoring.parsers.log_parser import (
    count_matching_lines,
    parse_server_properties,
    read_recent_lines,
    search_lines,
)
from monitoring.rcon import RconClient, RconError

# server.properties keys that must never be surfaced to a tool caller / LLM.
SENSITIVE_PROPERTY_KEYS = {"rcon.password", "query.password"}

# Minecraft log lines / RCON text use section-sign (§) color codes.
_COLOR_CODE_RE = re.compile(r"§.")


class MinecraftUnreachableError(Exception):
    """Raised when the server does not respond to a status ping, or RCON fails."""


class RconNotConfiguredError(Exception):
    """Raised when an RCON-dependent method is called without a password configured."""


def _strip_color_codes(text: str) -> str:
    return _COLOR_CODE_RE.sub("", text)


class MinecraftMonitor:
    """
    Collects real-time Minecraft server metrics.

    Parameters mirror `config.Settings` fields so the API/agent layer can
    construct this directly from `get_settings()`.
    """

    def __init__(
        self,
        host: str,
        port: int,
        rcon_host: str,
        rcon_port: int,
        rcon_password: str,
        log_path: str,
        properties_path: str,
    ) -> None:
        self._host = host
        self._port = port
        self._rcon_host = rcon_host
        self._rcon_port = rcon_port
        self._rcon_password = rcon_password
        self._log_path = log_path
        self._properties_path = properties_path

    # -- Server List Ping ----------------------------------------------------

    def _ping(self) -> JavaStatusResponse:
        server = JavaServer(self._host, self._port, timeout=3)
        return server.status()

    def get_server_status(self) -> ServerStatus:
        """
        Return whether the server is reachable, plus version/player-count/MOTD.

        A failed ping means "not running or not reachable" -- we don't try to
        distinguish "server process is down" from "firewalled" from "wrong
        port", because from monitoring's point of view they're the same
        observable fact: we cannot currently reach the server.
        """
        try:
            status = self._ping()
        except Exception:
            return ServerStatus(running=False)

        motd = status.motd.to_plain() if status.motd is not None else None

        return ServerStatus(
            running=True,
            version=status.version.name,
            player_count=status.players.online,
            max_players=status.players.max,
            motd=motd,
            latency_ms=round(status.latency, 2),
        )

    def get_online_players(self) -> OnlinePlayers:
        """
        Return online player names, when the server exposes them.

        Vanilla Server List Ping only returns a *sample* of players (often
        capped, and some servers disable the sample list entirely for
        privacy). If the sample is empty but the count is > 0, we say so
        explicitly via `names_available=False` rather than implying nobody
        is online.
        """
        try:
            status = self._ping()
        except Exception as exc:
            raise MinecraftUnreachableError(f"Could not reach Minecraft server: {exc}") from exc

        count = status.players.online
        sample = status.players.sample or []
        names = [p.name for p in sample]

        return OnlinePlayers(
            count=count,
            names=names,
            names_available=bool(names) or count == 0,
        )

    # -- RCON-dependent -------------------------------------------------------

    def _require_rcon_password(self) -> None:
        if not self._rcon_password:
            raise RconNotConfiguredError(
                "RCON password is not configured (set MINECRAFT_RCON_PASSWORD)"
            )

    def get_tps(self) -> TpsSnapshot:
        """
        Return TPS via Paper's built-in '/tps' console command over RCON.

        Paper's exact output format has changed across versions, e.g.:
            "TPS from last 1m, 5m, 15m: 20.0, 19.98, 20.0"
        We parse with a permissive regex and leave `raw_response` populated
        so a caller can always fall back to the raw text if parsing misses.
        """
        self._require_rcon_password()
        raw = self._run_rcon_command("tps")
        clean = _strip_color_codes(raw)

        numbers = re.findall(r"\d+\.\d+", clean)
        tps_1m = float(numbers[0]) if len(numbers) > 0 else None
        tps_5m = float(numbers[1]) if len(numbers) > 1 else None
        tps_15m = float(numbers[2]) if len(numbers) > 2 else None

        return TpsSnapshot(tps_1m=tps_1m, tps_5m=tps_5m, tps_15m=tps_15m, raw_response=clean)

    def get_mspt(self) -> MsptSnapshot:
        """
        Return mean/min/max milliseconds-per-tick.

        Modern Paper includes tick-time in the same '/tps' response, in a
        line like:
            "Server tick times (avg/min/max) from last 5s, 10s, ...: 12.3/8.1/45.6ms, ..."
        We take the first avg/min/max triplet found. If the running Paper
        version doesn't report this (older versions, or a plugin like Spark
        is needed instead), all fields come back None with the raw text
        preserved so the caller can see why.
        """
        self._require_rcon_password()
        raw = self._run_rcon_command("tps")
        clean = _strip_color_codes(raw)

        match = re.search(r"(\d+\.\d+)\s*/\s*(\d+\.\d+)\s*/\s*(\d+\.\d+)\s*ms", clean)
        if not match:
            return MsptSnapshot(mspt_avg=None, mspt_min=None, mspt_max=None, raw_response=clean)

        return MsptSnapshot(
            mspt_avg=float(match.group(1)),
            mspt_min=float(match.group(2)),
            mspt_max=float(match.group(3)),
            raw_response=clean,
        )

    def get_plugins(self) -> PluginInfo:
        """
        Return installed plugins via Bukkit/Paper's 'plugins' console command.

        Typical response: "Plugins (3): PluginA, PluginB, PluginC"
        (colors indicate enabled/disabled and are stripped here).
        """
        self._require_rcon_password()
        raw = self._run_rcon_command("plugins")
        clean = _strip_color_codes(raw)

        match = re.search(r"Plugins\s*\(\d+\)\s*:\s*(.*)", clean)
        if not match:
            return PluginInfo(count=0, names=[])

        names = [name.strip() for name in match.group(1).split(",") if name.strip()]
        return PluginInfo(count=len(names), names=names)

    def _run_rcon_command(self, cmd: str) -> str:
        """
        Run exactly one hardcoded command over RCON.

        This is the ONLY place RCON commands are issued, and every caller in
        this file passes a fixed string ("tps", "plugins") -- never a value
        that came from a tool argument, the LLM, or an HTTP request body.
        """
        try:
            with RconClient(self._rcon_host, self._rcon_port, self._rcon_password) as rcon:
                return rcon.command(cmd)
        except RconError as exc:
            raise MinecraftUnreachableError(f"RCON command '{cmd}' failed: {exc}") from exc

    # -- File-based ------------------------------------------------------------

    def get_server_properties(self) -> ServerProperties:
        """Return server.properties, with secret keys withheld."""
        all_values = parse_server_properties(self._properties_path)
        excluded = [k for k in all_values if k in SENSITIVE_PROPERTY_KEYS]
        filtered = {k: v for k, v in all_values.items() if k not in SENSITIVE_PROPERTY_KEYS}
        return ServerProperties(values=filtered, excluded_keys=excluded)

    def get_recent_logs(self, max_lines: int = 100) -> LogLines:
        """Return the most recent lines from the server's latest.log."""
        lines = read_recent_lines(self._log_path, max_lines=max_lines)
        return LogLines(lines=lines, source_file=self._log_path, truncated=False)

    def search_logs(self, query: str, max_results: int = 50) -> LogLines:
        """Search the server log for lines containing `query` (case-insensitive)."""
        matches, truncated = search_lines(self._log_path, query, max_results=max_results)
        return LogLines(lines=matches, source_file=self._log_path, truncated=truncated)

    def count_log_matches(self, query: str) -> int:
        """Count all matching log lines (used e.g. to count warning occurrences)."""
        return count_matching_lines(self._log_path, query)
