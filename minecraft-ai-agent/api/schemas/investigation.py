from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class InvestigationCreateRequest(BaseModel):
    question: str
    server_name: str = "default"


class ToolCallOut(BaseModel):
    tool_name: str
    arguments: dict
    result: dict
    timestamp: datetime

    model_config = {"from_attributes": True}


class ObservationOut(BaseModel):
    metric: str
    value: str
    timestamp: datetime

    model_config = {"from_attributes": True}


class InvestigationOut(BaseModel):
    id: int
    server_id: int
    user_question: str
    started_at: datetime
    completed_at: datetime | None
    diagnosis: str | None
    severity: str | None
    resolved: bool
    tool_calls: list[ToolCallOut]
    observations: list[ObservationOut]

    model_config = {"from_attributes": True}
