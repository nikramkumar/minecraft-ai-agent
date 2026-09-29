from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from agent.agent import AgentError, IncidentResponseAgent
from api.dependencies import db_session_dependency, get_agent
from api.schemas.chat import ChatRequest, ChatResponse
from database.repositories.investigation_repo import get_or_create_server, save_investigation

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest,
    agent: IncidentResponseAgent = Depends(get_agent),
    db: Session = Depends(db_session_dependency),
) -> ChatResponse:
    try:
        result = agent.investigate(request.message)
    except AgentError as exc:
        # The LLM itself failed (network issue, invalid key, rate limit).
        # This is an upstream failure, not a bug in our API -- 502 fits.
        raise HTTPException(status_code=502, detail=f"Agent could not complete investigation: {exc}") from exc

    server = get_or_create_server(db, name="default")
    investigation = save_investigation(db, server.id, result)

    return ChatResponse(
        investigation_id=investigation.id,
        question=result.question,
        answer=result.answer,
        tool_calls=result.tool_calls,
        iterations_used=result.iterations_used,
        hit_max_iterations=result.hit_max_iterations,
    )
