import uuid
from datetime import timedelta
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.security import token_hash, utcnow
from app.database import get_db
from app.models import AgentAttachment, AgentConversation, AgentMessage, RateLimit
from app.routes.agents import access
from app.workflows import runtime, service
from app.workflows.actions.registry import ACTIONS
from app.workflows.actions.ask_survey import handler as survey
from app.workflows import attachments
from app.workflows.numbering import walk
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
    return process_event(workspace_id, agent_id, body, request, db)


def process_event(workspace_id, agent_id, body, request, db, upload_payloads=None):
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
    pending = previous.get("survey")
    if body.event in {"message_received", "attachment_received"}:
        if body.survey_answer_token and previous.get("workflow_revision") != document.revision:
            raise HTTPException(409, "Workflows changed during this survey. Restart the tester before answering.")
        if pending and previous.get("workflow_revision") == document.revision:
            if str(body.survey_answer_token or "") != pending.get("answer_token"):
                raise HTTPException(409, "This answer belongs to an earlier survey question. Answer the current question.")
        elif body.survey_answer_token:
            raise HTTPException(409, "There is no active survey question for this answer.")
    event_context = body.model_dump(mode="json")
    event_context["conversation_id"] = str(conversation.id)
    file_metadata = []
    if upload_payloads is not None:
        survey_node = next((node for node in walk(document.nodes) if node["id"] == (pending or {}).get("node_id")), None)
        if not survey_node or previous.get("workflow_revision") != document.revision:
            raise HTTPException(409, "There is no active survey upload question. Restart the tester if the workflow changed.")
        field = survey.active_field(survey_node, previous)
        if field["answer_type"] not in {"attachment", "attachments"}:
            raise HTTPException(409, "The current survey question expects a typed answer.")
        if field["answer_type"] == "attachment" and len(upload_payloads) != 1:
            raise HTTPException(422, "This survey question accepts exactly one file.")
        for payload in upload_payloads:
            file = AgentAttachment(conversation_id=conversation.id, **payload)
            db.add(file); db.flush()
            file_metadata.append(attachments.metadata(file, workspace_id, agent_id))
        event_context["attachments"] = file_metadata
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
        db.add(AgentMessage(conversation_id=conversation.id, role="user",
            content="Uploaded: " + ", ".join(file["name"] for file in file_metadata) if file_metadata else "Attachment test: " + body.attachment.name,
            message_data={"event": body.event, "attachment": body.attachment.model_dump(), "attachments": file_metadata}))
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
    active_node = next((node for node in walk(document.nodes) if node["id"] == state.get("survey", {}).get("node_id")), None)
    return {"conversation_id": str(conversation.id), "messages": messages, "trace": trace,
        "workflow_revision": document.revision, "language": state["language"], "closed": bool(state.get("closed")),
        "feedback_submitted": bool(state.get("feedback_submitted")), "waiting": bool(state["stack"]) or bool(state.get("survey")), "variables": state.get("variables", {}),
        "survey": survey.status(active_node, state) if active_node else None, "attachments": file_metadata,
        "matching": matching.report,
        "notice": "" if output or matched else "No workflow matched this event. Add a matching trigger or enable a fallback."}


@router.post("/tester/attachments")
async def upload_survey_answer(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request,
    conversation_id: uuid.UUID = Form(...), survey_answer_token: uuid.UUID = Form(...),
    files: List[UploadFile] = File(...), language: Optional[str] = Form(None), db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    exists = db.scalar(select(AgentConversation.id).where(AgentConversation.id == conversation_id,
        AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
        AgentConversation.user_id == session.user_id))
    if not exists:
        raise HTTPException(404, "Conversation not found.")
    payloads = await attachments.read_uploads(files)
    body = TesterEvent(event="attachment_received", conversation_id=conversation_id, language=language,
        survey_answer_token=survey_answer_token, attachment={"name": payloads[0]["file_name"], "media_type": "file"})
    return process_event(workspace_id, agent_id, body, request, db, upload_payloads=payloads)


@router.get("/tester/attachments/{attachment_id}")
def download_survey_answer(workspace_id: uuid.UUID, agent_id: uuid.UUID, attachment_id: uuid.UUID,
    request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = access(request, db, workspace_id, agent_id=agent_id)
    file = db.scalar(select(AgentAttachment).join(AgentConversation).where(AgentAttachment.id == attachment_id,
        AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
        AgentConversation.user_id == session.user_id))
    if not file:
        raise HTTPException(404, "Attachment not found.")
    return Response(file.data, media_type="application/octet-stream", headers={"Cache-Control": "private, no-store",
        "Content-Disposition": "attachment; filename=\"attachment\"; filename*=UTF-8''" + quote(file.file_name, safe="")})
