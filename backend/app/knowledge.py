"""Small, explicit agent knowledge documents; text only in this release."""
import uuid
from typing import List

from fastapi import HTTPException
from pydantic import Field, StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.agent_schemas import StrictModel
from app.core.security import utcnow
from app.models import AgentChange, AgentKnowledge


class KnowledgeSource(StrictModel):
    id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    enabled: StrictBool = True


class SaveKnowledge(StrictModel):
    expected_revision: int = Field(ge=1)
    sources: List[KnowledgeSource] = Field(max_length=20)

    @model_validator(mode="after")
    def bounded_document(self):
        if len({source.id for source in self.sources}) != len(self.sources): raise ValueError("Each knowledge source needs a unique ID.")
        if sum(len((source.name + source.content).encode("utf-8")) for source in self.sources) > 64 * 1024:
            raise ValueError("Knowledge text supports up to 64 KB per agent.")
        return self


def document_payload(document, agent_id):
    return {"agent_id": str(agent_id), "revision": document.revision if document else 1,
        "sources": document.sources if document else [], "updated_at": document.updated_at.isoformat() if document else None}


def get_document(db, agent_id):
    return db.get(AgentKnowledge, agent_id)


def save_document(db, agent, actor_id, body):
    db.execute(insert(AgentKnowledge).values(agent_id=agent.id, revision=1, sources=[], updated_at=utcnow()).on_conflict_do_nothing(index_elements=[AgentKnowledge.agent_id]))
    document = db.scalar(select(AgentKnowledge).where(AgentKnowledge.agent_id == agent.id).with_for_update().execution_options(populate_existing=True))
    if document.revision != body.expected_revision: raise HTTPException(409, "Knowledge changed in another session. Reload before saving.")
    sources = [source.model_dump(mode="json") for source in body.sources]
    if sources != document.sources:
        document.sources = sources; document.revision += 1; document.updated_at = utcnow()
        db.add(AgentChange(workspace_id=agent.workspace_id, agent_id=agent.id, agent_name=agent.name, actor_id=actor_id,
            action="knowledge_saved", summary="Knowledge revision {} saved.".format(document.revision)))
    db.commit()
    return document_payload(document, agent.id)


def enabled_sources(db, agent_id):
    document = get_document(db, agent_id)
    return [source for source in document.sources if source.get("enabled")] if document else []
