import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.security import token_hash, utcnow
from app.database import get_db
from app.models import AgentConversation, AgentMessage, RateLimit
from app.routes.agents import access
from app.workflows import runtime, service
from app.workflows.actions.registry import ACTIONS
from app.workflows.schemas import SaveWorkflows, TesterEvent

router = APIRouter(prefix="/api/workspaces/{workspace_id}/agents/{agent_id}", tags=["Workflows"])


@router.get("/workflows")
def get_workflows(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    return {**service.payload(service.get_document(db, agent_id)), "triggers": service.TRIGGERS, "actions": list(ACTIONS.values())}


@router.post("/workflows")
def save_workflows(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: SaveWorkflows, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    return service.save(db, agent, session.user_id, body)


@router.post("/tester/events")
def tester_event(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: TesterEvent, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    now = utcnow()
    key = token_hash("workflow-tester:{}:{}".format(session.user_id, int(now.timestamp()) // 60))
    statement = insert(RateLimit).values(key=key, count=1, expires_at=now + timedelta(minutes=2))
    count = db.execute(statement.on_conflict_do_update(index_elements=[RateLimit.key], set_={"count": RateLimit.count + 1}).returning(RateLimit.count)).scalar_one()
    db.commit()
    if count > 60:
        raise HTTPException(429, "Too many tester events. Wait a minute and try again.")
    if body.language and body.language not in agent.languages:
        raise HTTPException(422, "Choose a language configured for this agent.")
    document = service.get_document(db, agent_id)
    if body.conversation_id:
        conversation = db.scalar(select(AgentConversation).where(AgentConversation.id == body.conversation_id,
            AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
            AgentConversation.user_id == session.user_id).with_for_update().execution_options(populate_existing=True))
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation = AgentConversation(workspace_id=workspace_id, agent_id=agent_id, user_id=session.user_id,
            agent_name=agent.name, workflow_state={})
        db.add(conversation)
        db.flush()
    previous = conversation.workflow_state or {}
    if previous.get("closed") and body.event != "collect_feedback":
        raise HTTPException(409, "This chat is closed. Restart the tester to begin a new conversation.")
    if body.event == "collect_feedback":
        if not previous.get("closed"):
            raise HTTPException(409, "Close the chat before submitting CSAT feedback.")
        if previous.get("feedback_submitted"):
            raise HTTPException(409, "Feedback has already been submitted for this chat.")
    event_context = body.model_dump(mode="json")
    event_context["conversation_id"] = str(conversation.id)
    history = []
    if body.event == "message_received":
        recent = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(6)).all()
        history = [{"role": item.role, "content": item.content} for item in reversed(recent)]

    def reserve_llm_request():
        llm_key = token_hash("tester:{}:{}".format(session.user_id, int(now.timestamp()) // 60))
        limit = insert(RateLimit).values(key=llm_key, count=1, expires_at=now + timedelta(minutes=2))
        llm_count = db.execute(limit.on_conflict_do_update(index_elements=[RateLimit.key],
            set_={"count": RateLimit.count + 1}).returning(RateLimit.count)).scalar_one()
        if llm_count > request.app.state.settings.llm_tester_requests_per_minute:
            raise HTTPException(429, "Too many model requests. Wait a minute and try again.")

    matching = request.app.state.intent_matcher.session(document, agent, history, reserve_llm_request)
    state, output, trace, matched = runtime.execute(document, agent, previous, event_context, matching)
    if body.event == "message_received":
        if not matched and agent.llm_enabled and request.app.state.llm_tester.available:
            reserve_llm_request()
            recent = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
                .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(20)).all()
            history = [{"role": item.role, "content": item.content} for item in reversed(recent)]
            history.append({"role": "user", "content": body.content})
            # Keep nested-trigger context if a provider fails; the whole turn rolls back.
            content = request.app.state.llm_tester.reply(agent, history)
            output.append({"content": content, "quick_replies": [], "source": "llm", "node_id": None})
            trace.append({"node_id": None, "node_number": None, "name": "Generative reply", "type": "llm", "outcome": "executed"})
        db.add(AgentMessage(conversation_id=conversation.id, role="user", content=body.content))
    elif body.event == "attachment_received":
        db.add(AgentMessage(conversation_id=conversation.id, role="user", content="Attachment test: " + body.attachment.name,
            message_data={"event": body.event, "attachment": body.attachment.model_dump()}))
    if body.event in {"chat_closed", "chat_timeout"}:
        state["closed"] = True
    if body.event == "collect_feedback":
        state["feedback_submitted"] = True
        state["csat_rating"] = body.rating
    conversation.workflow_state = state
    messages = []
    for result in output:
        message = AgentMessage(conversation_id=conversation.id, role="assistant", content=result["content"],
            message_data={key: value for key, value in result.items() if key != "content"})
        db.add(message)
        db.flush()
        messages.append({"id": str(message.id), "role": message.role, "content": message.content,
            "created_at": message.created_at.isoformat(), **message.message_data})
    db.commit()
    return {"conversation_id": str(conversation.id), "messages": messages, "trace": trace,
        "workflow_revision": document.revision, "language": state["language"], "closed": bool(state.get("closed")),
        "feedback_submitted": bool(state.get("feedback_submitted")), "waiting": bool(state["stack"]), "variables": state.get("variables", {}),
        "matching": matching.report,
        "notice": "" if output or matched else "No workflow matched this event. Add a matching trigger or enable a fallback."}
