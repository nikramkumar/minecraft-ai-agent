from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from agent.agent import AgentError, IncidentResponseAgent
from api.dependencies import db_session_dependency, get_agent
from api.schemas.investigation import InvestigationCreateRequest, InvestigationOut
from database.repositories.investigation_repo import get_investigation, get_or_create_server, save_investigation

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", response_model=InvestigationOut)
def create_investigation(
    request: InvestigationCreateRequest,
    agent: IncidentResponseAgent = Depends(get_agent),
    db: Session = Depends(db_session_dependency),
) -> InvestigationOut:
    try:
        result = agent.investigate(request.question)
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=f"Agent could not complete investigation: {exc}") from exc

    server = get_or_create_server(db, name=request.server_name)
    investigation = save_investigation(db, server.id, result)
    return investigation


@router.get("/{investigation_id}", response_model=InvestigationOut)
def read_investigation(investigation_id: int, db: Session = Depends(db_session_dependency)) -> InvestigationOut:
    investigation = get_investigation(db, investigation_id)
    if investigation is None:
        raise HTTPException(status_code=404, detail=f"Investigation {investigation_id} not found")
    return investigation
