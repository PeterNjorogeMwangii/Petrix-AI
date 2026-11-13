import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app import knowledge
from app.routes.agents import access

router = APIRouter(prefix="/api/workspaces/{workspace_id}/agents/{agent_id}/knowledge", tags=["Knowledge"])


@router.get("")
def get_knowledge(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    return knowledge.document_payload(knowledge.get_document(db, agent_id), agent_id)


@router.post("")
def save_knowledge(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: knowledge.SaveKnowledge, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    return knowledge.save_document(db, agent, session.user_id, body)
