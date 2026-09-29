"""
Tool abstraction layer.

This is the security boundary between the LLM and the rest of the system.
Every tool the agent can call is:

  * Narrowly scoped (one specific piece of data, e.g. get_cpu_usage()) --
    never a generic "run this command" tool.
  * Declared with a JSON Schema for its inputs, which the LLM provider uses
    to constrain what arguments it can even attempt to pass.
  * Wrapped so that ANY exception raised while running it becomes a
    structured {"error": "..."} result instead of crashing the agent loop
    or leaking a Python traceback to the LLM.

`Tool` and `ToolRegistry` are provider-agnostic; `to_anthropic_schema()` is
the one place that knows about a specific LLM API's tool-calling format, so
swapping providers later only touches this one function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """
    A single callable tool exposed to the agent.

    Parameters
    ----------
    name:
        Unique identifier, also what the LLM uses to request this tool.
    description:
        Sent to the LLM verbatim -- this is how it decides *when* to use the
        tool, so it should be specific about what data it returns and any
        preconditions (e.g. "requires RCON to be configured").
    input_schema:
        JSON Schema describing the tool's parameters, e.g.
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}.
        Use {"type": "object", "properties": {}} for no-argument tools.
    handler:
        The actual Python callable. Must accept keyword arguments matching
        input_schema and return a JSON-serializable value (a dict, ideally
        a `.model_dump(mode="json")` of a Pydantic model).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def to_anthropic_schema(self) -> dict[str, Any]:
        """Format this tool for the Anthropic Messages API `tools` parameter."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def run(self, **kwargs: Any) -> dict[str, Any]:
        """
        Execute the tool, guaranteeing a JSON-serializable dict result.

        Every exception is caught here -- a tool failing (offline server,
        missing file, bad RCON password) is expected, routine behavior, not
        a bug to propagate as a 500 error. The agent's system prompt
        explicitly tells the LLM to treat `{"error": ...}` as evidence, not
        to fabricate data around it.
        """
        try:
            result = self.handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 - intentionally broad; see docstring
            logger.warning("Tool '%s' raised %s: %s", self.name, type(exc).__name__, exc)
            return {"error": str(exc), "error_type": type(exc).__name__}

        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return {"result": result}


@dataclass
class ToolRegistry:
    """A named collection of tools, queryable by name and exportable for the LLM."""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def to_anthropic_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_anthropic_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """
        Execute a tool by name, returning a structured error for unknown
        tool names instead of raising -- an LLM hallucinating a tool name
        it wasn't given is a real failure mode this needs to survive.
        """
        tool = self.get(name)
        if tool is None:
            logger.warning("Agent requested unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}", "error_type": "UnknownToolError"}
        return tool.run(**arguments)
