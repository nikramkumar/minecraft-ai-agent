"""Tests for monitoring.parsers.log_parser -- pure file parsing, no network."""

from __future__ import annotations

import pytest

from monitoring.parsers.log_parser import (
    LogFileNotFoundError,
    count_matching_lines,
    parse_server_properties,
    read_recent_lines,
    search_lines,
)


@pytest.fixture
def sample_log(tmp_path):
    log_file = tmp_path / "latest.log"
    lines = [f"[12:00:{i:02d}] [Server thread/INFO]: Line {i}" for i in range(10)]
    lines.append("[12:00:10] [Server thread/WARN]: Can't keep up! Is the server overloaded?")
    lines.append("[12:00:11] [Server thread/WARN]: Can't keep up! Is the server overloaded?")
    log_file.write_text("\n".join(lines) + "\n")
    return log_file


class TestReadRecentLines:
    def test_reads_last_n_lines(self, sample_log):
        result = read_recent_lines(sample_log, max_lines=3)
        assert len(result) == 3
        assert "overloaded" in result[-1]

    def test_missing_file_raises_specific_error(self, tmp_path):
        with pytest.raises(LogFileNotFoundError):
            read_recent_lines(tmp_path / "does_not_exist.log")

    def test_max_lines_larger_than_file_returns_whole_file(self, sample_log):
        result = read_recent_lines(sample_log, max_lines=1000)
        assert len(result) == 12


class TestSearchLines:
    def test_finds_matching_lines_case_insensitive(self, sample_log):
        matches, truncated = search_lines(sample_log, "OVERLOADED")
        assert len(matches) == 2
        assert truncated is False

    def test_no_matches_returns_empty(self, sample_log):
        matches, truncated = search_lines(sample_log, "nonexistent-string-xyz")
        assert matches == []
        assert truncated is False

    def test_truncates_when_over_max_results(self, sample_log):
        matches, truncated = search_lines(sample_log, "Line", max_results=2)
        assert len(matches) == 2
        assert truncated is True

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(LogFileNotFoundError):
            search_lines(tmp_path / "nope.log", "x")


class TestCountMatchingLines:
    def test_counts_all_matches_uncapped(self, sample_log):
        assert count_matching_lines(sample_log, "overloaded") == 2

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(LogFileNotFoundError):
            count_matching_lines(tmp_path / "nope.log", "x")


class TestParseServerProperties:
    def test_parses_key_value_pairs(self, tmp_path):
        props_file = tmp_path / "server.properties"
        props_file.write_text(
            "#Minecraft server properties\n"
            "max-players=20\n"
            "motd=A Minecraft Server\n"
            "rcon.password=supersecret\n"
            "\n"
            "enable-rcon=true\n"
        )
        result = parse_server_properties(props_file)
        assert result["max-players"] == "20"
        assert result["motd"] == "A Minecraft Server"
        assert result["enable-rcon"] == "true"
        # This function itself does NOT filter secrets -- that's the caller's job.
        assert result["rcon.password"] == "supersecret"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        props_file = tmp_path / "server.properties"
        props_file.write_text("# comment\n\nlevel-name=world\n")
        result = parse_server_properties(props_file)
        assert result == {"level-name": "world"}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(LogFileNotFoundError):
            parse_server_properties(tmp_path / "nope.properties")
