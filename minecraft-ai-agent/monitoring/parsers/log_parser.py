"""
Parsing utilities for Minecraft/Paper server log files.

Kept separate from minecraft_monitor.py because log parsing is pure text
processing with no network/RCON dependency, and is the easiest piece to
unit test exhaustively against fixture files.
"""

from __future__ import annotations

from pathlib import Path


class LogFileNotFoundError(Exception):
    """Raised when the configured Minecraft log file does not exist."""


def read_recent_lines(log_path: str | Path, max_lines: int = 100) -> list[str]:
    """
    Return up to `max_lines` most recent lines from a log file.

    Reads the whole file rather than seeking from the end, which is fine for
    Minecraft's daily-rotated latest.log (typically well under a few MB) and
    keeps the implementation simple and correct for multi-byte UTF-8 content.
    """
    path = Path(log_path)
    if not path.exists():
        raise LogFileNotFoundError(f"Minecraft log file not found: {path}")

    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    return [line.rstrip("\n") for line in lines[-max_lines:]]


def search_lines(log_path: str | Path, query: str, max_results: int = 50) -> tuple[list[str], bool]:
    """
    Case-insensitive substring search over a log file.

    Returns (matching_lines, truncated) where `truncated` is True if more
    matches existed than `max_results` allowed us to return -- callers
    (tools/API) should surface this so a user doesn't mistake a truncated
    result for "these are the only matches."
    """
    path = Path(log_path)
    if not path.exists():
        raise LogFileNotFoundError(f"Minecraft log file not found: {path}")

    query_lower = query.lower()
    matches: list[str] = []
    truncated = False

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if query_lower in line.lower():
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(line.rstrip("\n"))

    return matches, truncated


def count_matching_lines(log_path: str | Path, query: str) -> int:
    """Count all lines containing `query` (case-insensitive), without a cap."""
    path = Path(log_path)
    if not path.exists():
        raise LogFileNotFoundError(f"Minecraft log file not found: {path}")

    query_lower = query.lower()
    with path.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for line in f if query_lower in line.lower())


def parse_server_properties(properties_path: str | Path) -> dict[str, str]:
    """
    Parse a Minecraft server.properties file into a dict.

    server.properties is Java .properties format: `key=value` lines, with
    `#`-prefixed comments and blank lines ignored. We do NOT filter secrets
    here -- that's the caller's responsibility (see ServerProperties model),
    so this function stays a pure, reusable parser.
    """
    path = Path(properties_path)
    if not path.exists():
        raise LogFileNotFoundError(f"server.properties not found: {path}")

    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()

    return values
