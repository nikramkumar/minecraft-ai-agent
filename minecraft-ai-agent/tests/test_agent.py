"""
Tests for agent.agent.IncidentResponseAgent.

These tests NEVER call a real LLM API -- they inject a `FakeLLMClient` that
returns a scripted sequence of responses, shaped exactly like real Anthropic
Message objects (content blocks with .type/.text/.name/.input/.id). This
lets us test the loop's actual control flow: does it call the right tool,
does it feed results back correctly, does it stop, does it survive tool and
LLM failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.agent import AgentError, IncidentResponseAgent
from agent.llm_client import LLMError
from agent.tools.base import Tool, ToolRegistry


# --- Fakes shaped like the real Anthropic SDK's response objects -----------


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list[Any]
    stop_reason: str | None = None


class FakeLLMClient:
    """Returns pre-scripted responses in order; records every call for assertions."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create_message(self, messages, system, tools):
        self.calls.append({"messages": messages, "system": system, "tools": tools})
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses")
        return self._responses.pop(0)


class RaisingLLMClient:
    def create_message(self, messages, system, tools):
        raise LLMError("simulated API outage")


# --- Helpers -----------------------------------------------------------------


def make_registry_with_fake_tool(return_value=None, side_effect=None) -> ToolRegistry:
    registry = ToolRegistry()

    def handler():
        if side_effect is not None:
            raise side_effect
        return return_value or {"cpu_percent": 42.0}

    registry.register(
        Tool(name="get_cpu_usage", description="d", input_schema={"type": "object", "properties": {}}, handler=handler)
    )
    return registry


# --- Tests --------------------------------------------------------------------


class TestNoToolsNeeded:
    def test_immediate_text_response_returns_answer_directly(self):
        llm = FakeLLMClient([FakeResponse(content=[FakeTextBlock(text="Everything looks healthy.")])])
        agent = IncidentResponseAgent(llm, ToolRegistry())

        result = agent.investigate("Is my server healthy?")

        assert result.answer == "Everything looks healthy."
        assert result.tool_calls == []
        assert result.iterations_used == 1
        assert result.hit_max_iterations is False


class TestToolCallLoop:
    def test_agent_calls_tool_then_produces_final_answer(self):
        llm = FakeLLMClient(
            [
                FakeResponse(
                    content=[FakeToolUseBlock(id="tu_1", name="get_cpu_usage", input={})]
                ),
                FakeResponse(content=[FakeTextBlock(text="CPU is at 42%, which is normal.")]),
            ]
        )
        registry = make_registry_with_fake_tool(return_value={"cpu_percent": 42.0})
        agent = IncidentResponseAgent(llm, registry)

        result = agent.investigate("Why is my server lagging?")

        assert result.answer == "CPU is at 42%, which is normal."
        assert result.iterations_used == 2
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "get_cpu_usage"
        assert result.tool_calls[0].result == {"cpu_percent": 42.0}

    def test_tool_result_is_fed_back_as_next_user_message(self):
        llm = FakeLLMClient(
            [
                FakeResponse(content=[FakeToolUseBlock(id="tu_1", name="get_cpu_usage", input={})]),
                FakeResponse(content=[FakeTextBlock(text="done")]),
            ]
        )
        registry = make_registry_with_fake_tool(return_value={"cpu_percent": 99.9})
        agent = IncidentResponseAgent(llm, registry)

        agent.investigate("question")

        second_call_messages = llm.calls[1]["messages"]
        last_message = second_call_messages[-1]
        assert last_message["role"] == "user"
        assert last_message["content"][0]["type"] == "tool_result"
        assert last_message["content"][0]["tool_use_id"] == "tu_1"
        assert "99.9" in last_message["content"][0]["content"]

    def test_multiple_tool_calls_in_one_turn_are_all_executed(self):
        llm = FakeLLMClient(
            [
                FakeResponse(
                    content=[
                        FakeToolUseBlock(id="tu_1", name="get_cpu_usage", input={}),
                        FakeToolUseBlock(id="tu_2", name="get_cpu_usage", input={}),
                    ]
                ),
                FakeResponse(content=[FakeTextBlock(text="done")]),
            ]
        )
        registry = make_registry_with_fake_tool()
        agent = IncidentResponseAgent(llm, registry)

        result = agent.investigate("question")

        assert len(result.tool_calls) == 2


class TestMalformedOrFailingTools:
    def test_tool_exception_is_recorded_as_error_and_loop_continues(self):
        llm = FakeLLMClient(
            [
                FakeResponse(content=[FakeToolUseBlock(id="tu_1", name="get_cpu_usage", input={})]),
                FakeResponse(content=[FakeTextBlock(text="Could not determine CPU usage.")]),
            ]
        )
        registry = make_registry_with_fake_tool(side_effect=RuntimeError("psutil exploded"))
        agent = IncidentResponseAgent(llm, registry)

        result = agent.investigate("question")

        assert result.tool_calls[0].result["error"] == "psutil exploded"
        assert result.answer == "Could not determine CPU usage."

    def test_unknown_tool_name_from_llm_does_not_crash_agent(self):
        llm = FakeLLMClient(
            [
                FakeResponse(content=[FakeToolUseBlock(id="tu_1", name="hallucinated_tool", input={})]),
                FakeResponse(content=[FakeTextBlock(text="done")]),
            ]
        )
        agent = IncidentResponseAgent(llm, ToolRegistry())

        result = agent.investigate("question")

        assert result.tool_calls[0].result["error_type"] == "UnknownToolError"


class TestMaxIterations:
    def test_hits_max_iterations_and_forces_a_final_answer(self):
        # Every scripted response requests another tool call -- the agent
        # should give up after max_tool_iterations and force a final answer
        # with tools disabled.
        tool_use_response = FakeResponse(content=[FakeToolUseBlock(id="tu", name="get_cpu_usage", input={})])
        forced_final_response = FakeResponse(content=[FakeTextBlock(text="Best guess given limited data.")])
        llm = FakeLLMClient([tool_use_response, tool_use_response, tool_use_response, forced_final_response])
        registry = make_registry_with_fake_tool()
        agent = IncidentResponseAgent(llm, registry, max_tool_iterations=3)

        result = agent.investigate("question")

        assert result.hit_max_iterations is True
        assert result.answer == "Best guess given limited data."
        assert result.iterations_used == 3
        # The forced final call must have been made with tools disabled.
        assert llm.calls[-1]["tools"] == []


class TestLLMFailure:
    def test_llm_error_raises_agent_error_not_a_crash(self):
        agent = IncidentResponseAgent(RaisingLLMClient(), ToolRegistry())
        with pytest.raises(AgentError):
            agent.investigate("question")
