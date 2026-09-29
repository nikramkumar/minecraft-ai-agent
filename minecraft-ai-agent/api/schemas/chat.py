from __future__ import annotations

from pydantic import BaseModel, Field

from agent.models import ToolCallRecord


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The administrator's question, e.g. 'Why is my server lagging?'")


class ChatResponse(BaseModel):
    investigation_id: int
    question: str
    answer: str
    tool_calls: list[ToolCallRecord]
    iterations_used: int
    hit_max_iterations: bool
