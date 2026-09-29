"""
Tests for agent.tools -- the security boundary between the LLM and the
monitoring layer.

Key things under test:
  * Tool.run() never raises -- failures become structured error dicts.
  * ToolRegistry.execute() handles unknown tool names gracefully (an LLM
    hallucinating a tool name must not crash the agent loop).
  * Input validation rejects bad arguments before they reach real I/O.
  * No tool anywhere accepts a raw shell/RCON command string as an argument.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent.tools import build_tool_registry
from agent.tools.base import Tool, ToolRegistry
from agent.tools.minecraft import ToolInputError, build_minecraft_tools
from monitoring.linux_monitor import LinuxMonitor
from monitoring.minecraft_monitor import MinecraftMonitor


class TestToolRun:
    def test_successful_handler_returns_dict(self):
        tool = Tool(
            name="ping",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=lambda: {"pong": True},
        )
        assert tool.run() == {"pong": True}

    def test_pydantic_model_result_is_serialized(self):
        from monitoring.models import MemoryUsage

        tool = Tool(
            name="mem",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=lambda: MemoryUsage(used_gb=1.0, total_gb=2.0, percent=50.0),
        )
        result = tool.run()
        assert result == {"used_gb": 1.0, "total_gb": 2.0, "percent": 50.0}

    def test_exception_becomes_structured_error_not_a_crash(self):
        def boom():
            raise RuntimeError("something broke")

        tool = Tool(name="boom", description="test", input_schema={"type": "object", "properties": {}}, handler=boom)
        result = tool.run()
        assert result["error"] == "something broke"
        assert result["error_type"] == "RuntimeError"

    def test_missing_required_argument_becomes_structured_error(self):
        tool = Tool(
            name="needs_arg",
            description="test",
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            handler=lambda x: {"x": x},
        )
        # Simulates the LLM forgetting a required argument -- must not raise.
        result = tool.run()
        assert "error" in result


class TestToolRegistry:
    def test_execute_unknown_tool_returns_error_not_exception(self):
        registry = ToolRegistry()
        result = registry.execute("does_not_exist", {})
        assert result["error_type"] == "UnknownToolError"

    def test_register_duplicate_name_raises(self):
        registry = ToolRegistry()
        tool = Tool(name="dup", description="d", input_schema={"type": "object", "properties": {}}, handler=lambda: {})
        registry.register(tool)
        with pytest.raises(ValueError):
            registry.register(tool)

    def test_to_anthropic_schemas_shape(self):
        registry = ToolRegistry()
        registry.register(
            Tool(
                name="foo",
                description="does foo",
                input_schema={"type": "object", "properties": {}},
                handler=lambda: {},
            )
        )
        schemas = registry.to_anthropic_schemas()
        assert schemas == [{"name": "foo", "description": "does foo", "input_schema": {"type": "object", "properties": {}}}]


class TestNoArbitraryExecution:
    """
    The project's core security requirement: no tool anywhere exposes a
    generic "run this command" capability -- every input_schema's
    properties must be scoped, typed fields, never a free-form command
    string routed to a shell or RCON console.
    """

    FORBIDDEN_PARAM_NAMES = {"command", "cmd", "shell_command", "rcon_command", "exec"}

    def test_no_tool_accepts_a_raw_command_parameter(self):
        linux_monitor = MagicMock(spec=LinuxMonitor)
        minecraft_monitor = MagicMock(spec=MinecraftMonitor)
        registry = build_tool_registry(linux_monitor, minecraft_monitor)

        for tool in registry.all_tools():
            param_names = set(tool.input_schema.get("properties", {}).keys())
            overlap = param_names & self.FORBIDDEN_PARAM_NAMES
            assert not overlap, f"Tool '{tool.name}' exposes a forbidden raw-command parameter: {overlap}"

    def test_registry_contains_expected_read_only_tools(self):
        linux_monitor = MagicMock(spec=LinuxMonitor)
        minecraft_monitor = MagicMock(spec=MinecraftMonitor)
        registry = build_tool_registry(linux_monitor, minecraft_monitor)
        names = {tool.name for tool in registry.all_tools()}

        expected = {
            "get_cpu_usage",
            "get_memory_usage",
            "get_disk_usage",
            "get_system_uptime",
            "get_server_status",
            "get_player_count",
            "get_online_players",
            "get_server_version",
            "get_plugins",
            "get_server_properties",
            "get_recent_logs",
            "search_logs",
            "get_tps",
            "get_mspt",
        }
        assert expected.issubset(names)


class TestMinecraftToolInputValidation:
    def test_search_logs_rejects_empty_query(self):
        monitor = MagicMock(spec=MinecraftMonitor)
        tools = {t.name: t for t in build_minecraft_tools(monitor)}
        result = tools["search_logs"].run(query="")
        assert "error" in result
        monitor.search_logs.assert_not_called()

    def test_search_logs_rejects_non_string_query(self):
        monitor = MagicMock(spec=MinecraftMonitor)
        tools = {t.name: t for t in build_minecraft_tools(monitor)}
        result = tools["search_logs"].run(query=123)
        assert "error" in result

    def test_search_logs_caps_max_results(self):
        monitor = MagicMock(spec=MinecraftMonitor)
        monitor.search_logs.return_value = MagicMock(model_dump=lambda mode: {})
        tools = {t.name: t for t in build_minecraft_tools(monitor)}
        tools["search_logs"].run(query="error", max_results=999999)
        _, kwargs = monitor.search_logs.call_args
        assert kwargs["max_results"] <= 200

    def test_get_recent_logs_rejects_negative_max_lines(self):
        monitor = MagicMock(spec=MinecraftMonitor)
        tools = {t.name: t for t in build_minecraft_tools(monitor)}
        result = tools["get_recent_logs"].run(max_lines=-5)
        assert "error" in result
        monitor.get_recent_logs.assert_not_called()

    def test_get_recent_logs_caps_max_lines(self):
        monitor = MagicMock(spec=MinecraftMonitor)
        monitor.get_recent_logs.return_value = MagicMock(model_dump=lambda mode: {})
        tools = {t.name: t for t in build_minecraft_tools(monitor)}
        tools["get_recent_logs"].run(max_lines=999999)
        _, kwargs = monitor.get_recent_logs.call_args
        assert kwargs["max_lines"] <= 500
