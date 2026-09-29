"""
LLM client wrapper.

This is the ONLY module that imports the `anthropic` SDK directly. Every
other module (agent.py, and all tests) depends on the `LLMClient` Protocol
below, not on Anthropic specifically. That means:

  * Tests can inject a fake client and never touch the network or need an
    API key (see tests/test_agent.py).
  * Swapping providers later (or adding a second one) means writing one new
    class here, not touching the agent loop.
"""

from __future__ import annotations

from typing import Any, Protocol

import anthropic


class LLMError(Exception):
    """Raised when the LLM API call fails, so callers can distinguish this
    from a tool failure or an agent bug and return a clean error to the user
    instead of crashing the request."""


class LLMResponse(Protocol):
    """
    The subset of an Anthropic Message response the agent loop relies on.
    Both the real SDK response object and our test fakes satisfy this shape
    structurally (Python doesn't require inheritance for Protocols).
    """

    content: list[Any]
    stop_reason: str | None


class LLMClient(Protocol):
    def create_message(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse: ...


class AnthropicLLMClient:
    """Real LLM client backed by the Anthropic Messages API."""

    def __init__(self, api_key: str, model: str, max_tokens: int = 1500) -> None:
        if not api_key:
            raise ValueError("An Anthropic API key is required to construct AnthropicLLMClient")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    def create_message(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        try:
            return self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=messages,
                tools=tools if tools else anthropic.NOT_GIVEN,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic API call failed: {exc}") from exc
