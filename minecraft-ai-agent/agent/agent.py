"""
The AI agent loop: reason -> select tool -> execute -> observe -> repeat ->
final diagnosis.

This module has no knowledge of FastAPI, PostgreSQL, or which specific LLM
provider is in use (see llm_client.py for that). It depends only on:
  * `LLMClient` (a Protocol) -- so tests can inject a scripted fake.
  * `ToolRegistry` -- so tests can inject fake/mocked monitors underneath.

That isolation is what makes it possible to test the *looping logic itself*
(does it call tools, does it stop, does it feed results back correctly)
without ever calling a real LLM API or touching a real server.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from agent.llm_client import LLMClient, LLMError
from agent.models import InvestigationResult, ToolCallRecord
from agent.prompts import SYSTEM_PROMPT
from agent.tools.base import ToolRegistry

logger = logging.getLogger(__name__)


class AgentError(Exception):
    """Raised when the agent cannot complete an investigation (e.g. LLM unavailable)."""


class IncidentResponseAgent:
    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        system_prompt: str = SYSTEM_PROMPT,
        max_tool_iterations: int = 8,
    ) -> None:
        self._llm = llm_client
        self._tools = tool_registry
        self._system_prompt = system_prompt
        self._max_iterations = max_tool_iterations

    def investigate(self, question: str) -> InvestigationResult:
        """
        Run the full reason/tool/observe loop for one user question and
        return a complete, structured record of what happened.
        """
        started_at = datetime.now(timezone.utc)
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        tool_call_log: list[ToolCallRecord] = []

        for iteration in range(1, self._max_iterations + 1):
            response = self._call_llm(messages)

            tool_use_blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]

            if not tool_use_blocks:
                answer = self._extract_text(response)
                return InvestigationResult(
                    question=question,
                    answer=answer,
                    tool_calls=tool_call_log,
                    iterations_used=iteration,
                    hit_max_iterations=False,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                )

            # The LLM asked for one or more tools. Record the assistant turn
            # verbatim (required by the API so tool_result blocks can
            # reference the correct tool_use_id), execute each tool, and
            # feed results back as the next user turn.
            messages.append({"role": "assistant", "content": response.content})

            tool_result_blocks = []
            for block in tool_use_blocks:
                result = self._tools.execute(block.name, block.input)
                tool_call_log.append(
                    ToolCallRecord(tool_name=block.name, arguments=dict(block.input), result=result)
                )
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_result_blocks})

        # Exhausted max_tool_iterations without a final text answer. Rather
        # than returning nothing, force one last call with no tools so the
        # model must synthesize its best answer from evidence gathered so far.
        logger.warning("Agent hit max_tool_iterations (%d) without a final answer", self._max_iterations)
        final_answer = self._force_final_answer(messages)
        return InvestigationResult(
            question=question,
            answer=final_answer,
            tool_calls=tool_call_log,
            iterations_used=self._max_iterations,
            hit_max_iterations=True,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
        )

    def _call_llm(self, messages: list[dict[str, Any]]):
        try:
            return self._llm.create_message(
                messages=messages,
                system=self._system_prompt,
                tools=self._tools.to_anthropic_schemas(),
            )
        except LLMError as exc:
            raise AgentError(f"LLM call failed: {exc}") from exc

    def _force_final_answer(self, messages: list[dict[str, Any]]) -> str:
        closing_prompt = (
            "You have reached the maximum number of tool calls for this investigation. "
            "Based ONLY on the evidence already gathered above, give your best final "
            "diagnosis now, following the required structure, and clearly state what "
            "remains uncertain."
        )
        messages = messages + [{"role": "user", "content": closing_prompt}]
        try:
            response = self._llm.create_message(messages=messages, system=self._system_prompt, tools=[])
        except LLMError as exc:
            raise AgentError(f"LLM call failed while forcing final answer: {exc}") from exc
        return self._extract_text(response)

    @staticmethod
    def _extract_text(response: Any) -> str:
        text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        return "\n".join(text_blocks).strip()
