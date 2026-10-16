import json
import uuid
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import agent_service as service
from app.agent_schemas import AgentCreate, AgentDelete, AgentExport, AgentName, LANGUAGES, Languages, NetworkCreate, Personalization, Revision, RoleSettings, TesterMessage, Voice
from app.auth_service import current_session, require_csrf
from app.core.security import token_hash, utcnow
from app.database import get_db
from app.models import Agent, AgentChange, AgentConnection, AgentConversation, AgentMessage, AgentVersion, RateLimit, RolePermission, User

router = APIRouter(prefix="/api", tags=["AI agents"])


def access(request, db, workspace_id, mutation=False, agent_id=None, lock=True):
    session = current_session(request, db)
    if mutation:
        require_csrf(request, session)
    workspace, role = service.workspace_for(db, workspace_id, session.user_id, manage=mutation, lock=mutation and lock)
    agent = service.agent_for(db, workspace_id, agent_id, lock=mutation and lock) if agent_id else None
    return session, workspace, role, agent


@router.get("/agents/options")
def options(request: Request, db: Session = Depends(get_db)):
    current_session(request, db)
    return {"languages": [{"code": code, "name": name} for code, name in LANGUAGES],
        "avatar_max_bytes": service.MAX_AVATAR_BYTES, "import_format": "petrix-agent", "import_schema_version": 1,
        "tester_available": request.app.state.llm_tester.available, "voice_runtime_available": False}


@router.get("/workspaces/{workspace_id}/agents")
def list_agents(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, workspace, role, _ = access(request, db, workspace_id)
    agents = db.scalars(select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.created_at, Agent.id)).all()
    return {"agents": [service.agent_payload(agent) for agent in agents], "can_manage": service.can_manage(db, workspace_id, role),
        "usage": {"count": len(agents), "agent_limit": workspace.agent_limit, "language_limit": workspace.language_limit, "plan_name": workspace.plan_name}}


@router.post("/workspaces/{workspace_id}/agents", status_code=201)
async def create_agent(workspace_id: uuid.UUID, request: Request, name: str = Form(...), default_language: str = Form("en"),
    avatar: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True, lock=False)
    try:
        data = AgentCreate(name=name, default_language=default_language)
    except ValidationError:
        raise HTTPException(422, "Enter an agent name of 1–100 characters and select a supported default language.") from None
    image = service.sanitize_avatar(await avatar.read(service.MAX_AVATAR_BYTES + 1)) if avatar else None
    session, workspace, _, _ = access(request, db, workspace_id, mutation=True)
    agent = service.create_agent(db, workspace, session.user_id, data.name, data.default_language, avatar=image)
    db.commit()
    return service.agent_payload(agent)


@router.post("/workspaces/{workspace_id}/agents/import", status_code=201)
async def import_agent(workspace_id: uuid.UUID, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True, lock=False)
    content = await file.read(service.MAX_IMPORT_BYTES + 1)
    if len(content) > service.MAX_IMPORT_BYTES:
        raise HTTPException(413, "Choose a Petrix agent export smaller than 15 MB.")
    try:
        package = AgentExport.model_validate_json(content)
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, "This file is not a supported Petrix AI agent export. Use format petrix-agent, schema version 1.") from None
    session, workspace, _, _ = access(request, db, workspace_id, mutation=True)
    agent = service.create_agent(db, workspace, session.user_id, package.agent.name, config=package.agent, action="imported")
    db.commit()
    return service.agent_payload(agent)


@router.get("/workspaces/{workspace_id}/agents/{agent_id}")
def get_agent(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, _, _, agent = access(request, db, workspace_id, agent_id=agent_id)
    return service.agent_payload(agent)


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/avatar")
def avatar(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, _, _, agent = access(request, db, workspace_id, agent_id=agent_id)
    if not agent.avatar_data:
        raise HTTPException(404, "Avatar not found.")
    return Response(agent.avatar_data, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/export")
def export_agent(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, _, role, agent = access(request, db, workspace_id, agent_id=agent_id)
    if not service.can_manage(db, workspace_id, role):
        raise HTTPException(403, "You need the Manage AI Agents permission to export an agent.")
    package = {"format": "petrix-agent", "schema_version": 1, "agent": service.configuration(agent)}
    return Response(json.dumps(package, ensure_ascii=False, indent=2), media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="petrix-agent-{}.json"'.format(agent.id)})


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/personalization")
def personalization(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: Personalization, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, body.expected_revision)
    before = service.configuration(agent)
    agent.llm_enabled, agent.persona = body.llm_enabled, body.persona
    return service.save_agent(db, agent, session.user_id, "personalization", "Personalization settings saved.", before)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/languages")
def languages(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: Languages, request: Request, db: Session = Depends(get_db)):
    session, workspace, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, body.expected_revision)
    service.check_languages(workspace, body.languages)
    before = service.configuration(agent)
    agent.languages, agent.default_language = body.languages, body.default_language
    return service.save_agent(db, agent, session.user_id, "languages", "Languages and default language saved.", before)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/identity")
async def identity(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, name: str = Form(...),
    expected_revision: int = Form(..., ge=1), remove_avatar: bool = Form(False), avatar: Optional[UploadFile] = File(None), db: Session = Depends(get_db)):
    access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    try:
        data = AgentName(name=name)
    except ValidationError:
        raise HTTPException(422, "Enter an agent name of 1–100 characters.") from None
    if remove_avatar and avatar:
        raise HTTPException(422, "Choose either uploading or removing an avatar.")
    image = service.sanitize_avatar(await avatar.read(service.MAX_AVATAR_BYTES + 1)) if avatar else None
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, expected_revision)
    before = service.configuration(agent)
    agent.name = data.name
    if avatar:
        agent.avatar_data = image
    elif remove_avatar:
        agent.avatar_data = None
    return service.save_agent(db, agent, session.user_id, "identity", "Agent name and avatar saved.", before)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/voice")
def voice(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: Voice, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, body.expected_revision)
    before = service.configuration(agent)
    agent.voice_enabled, agent.voice_name, agent.voice_speed = body.voice_enabled, body.voice_name, body.voice_speed
    return service.save_agent(db, agent, session.user_id, "voice", "Voice preferences saved.", before)


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/delete", status_code=204)
def delete_agent(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: AgentDelete, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, body.expected_revision)
    if body.confirmation_name != agent.name:
        raise HTTPException(422, "Type the agent's exact name to confirm permanent deletion.")
    db.add(AgentChange(workspace_id=workspace_id, agent_name=agent.name, actor_id=session.user_id,
        action="deleted", summary="AI agent permanently deleted; conversation history retained.", revision=agent.revision))
    db.delete(agent)
    db.commit()


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/versions")
def versions(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    rows = db.execute(select(AgentVersion, User.email).outerjoin(User, User.id == AgentVersion.actor_id)
        .where(AgentVersion.agent_id == agent_id).order_by(AgentVersion.number.desc()).offset(offset).limit(51)).all()
    return {"versions": [{"number": version.number, "section": version.section, "actor": email or "System",
        "created_at": version.created_at.isoformat()} for version, email in rows[:50]], "has_more": len(rows) > 50}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/versions/{number}")
def version_detail(workspace_id: uuid.UUID, agent_id: uuid.UUID, number: int, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent_id, AgentVersion.number == number))
    if not version:
        raise HTTPException(404, "Version not found.")
    snapshot = {key: value for key, value in version.snapshot.items() if key != "avatar_base64"}
    return {"number": version.number, "configuration": snapshot, "has_avatar": bool(version.snapshot.get("avatar_base64"))}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/versions/{number}/restore")
def restore_version(workspace_id: uuid.UUID, agent_id: uuid.UUID, number: int, body: Revision, request: Request, db: Session = Depends(get_db)):
    from app.agent_schemas import AgentConfiguration
    session, workspace, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    service.check_revision(agent, body.expected_revision)
    version = db.scalar(select(AgentVersion).where(AgentVersion.agent_id == agent_id, AgentVersion.number == number))
    if not version:
        raise HTTPException(404, "Version not found.")
    before = service.configuration(agent)
    service.apply_configuration(agent, AgentConfiguration.model_validate(version.snapshot), workspace)
    return service.save_agent(db, agent, session.user_id, "restored", "Restored settings from version {}.".format(number), before)


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/changes")
def changes(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    rows = db.execute(select(AgentChange, User.email).outerjoin(User, User.id == AgentChange.actor_id)
        .where(AgentChange.agent_id == agent_id).order_by(AgentChange.created_at.desc(), AgentChange.id.desc()).offset(offset).limit(51)).all()
    return {"changes": [{"id": str(change.id), "action": change.action, "summary": change.summary, "revision": change.revision,
        "actor": email or "System", "created_at": change.created_at.isoformat()} for change, email in rows[:50]], "has_more": len(rows) > 50}


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/network")
def network(workspace_id: uuid.UUID, agent_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    access(request, db, workspace_id, agent_id=agent_id)
    agents = db.scalars(select(Agent).where(Agent.workspace_id == workspace_id).order_by(Agent.name, Agent.id)).all()
    edges = db.scalars(select(AgentConnection).where(AgentConnection.workspace_id == workspace_id)).all()
    return {"nodes": [{"id": str(agent.id), "name": agent.name, "avatar_url": service.agent_payload(agent)["avatar_url"]} for agent in agents],
        "edges": [{"id": str(edge.id), "source_id": str(edge.source_id), "target_id": str(edge.target_id)} for edge in edges]}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/network", status_code=201)
def connect_agent(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: NetworkCreate, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    target = service.agent_for(db, workspace_id, body.target_id)
    if target.id == agent.id:
        raise HTTPException(422, "Choose another agent to connect.")
    existing = db.scalar(select(AgentConnection).where(AgentConnection.source_id == agent_id, AgentConnection.target_id == target.id))
    if existing:
        raise HTTPException(409, "These agents are already connected.")
    connection = AgentConnection(workspace_id=workspace_id, source_id=agent_id, target_id=target.id)
    db.add(connection)
    service.record_change(db, agent, session.user_id, "network_connected", "Connected to {}.".format(target.name))
    db.commit()
    return {"id": str(connection.id), "source_id": str(agent_id), "target_id": str(target.id)}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/network/{connection_id}/delete", status_code=204)
def disconnect_agent(workspace_id: uuid.UUID, agent_id: uuid.UUID, connection_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id)
    connection = db.scalar(select(AgentConnection).where(AgentConnection.id == connection_id, AgentConnection.workspace_id == workspace_id, AgentConnection.source_id == agent_id))
    if not connection:
        raise HTTPException(404, "Connection not found.")
    db.delete(connection)
    service.record_change(db, agent, session.user_id, "network_disconnected", "Agent connection removed.")
    db.commit()


@router.get("/workspaces/{workspace_id}/roles")
def get_roles(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _, workspace, role, _ = access(request, db, workspace_id)
    if role not in {"owner", "admin"}:
        raise HTTPException(403, "Only workspace administrators can manage roles and permissions.")
    return {"roles": [{"role": value, "manage_ai_agents": service.can_manage(db, workspace_id, value), "locked": value == "owner"} for value in ("owner", "admin", "member")],
        "subscription": {"plan_name": workspace.plan_name, "agent_limit": workspace.agent_limit, "language_limit": workspace.language_limit}}


@router.post("/workspaces/{workspace_id}/roles")
def save_roles(workspace_id: uuid.UUID, body: RoleSettings, request: Request, db: Session = Depends(get_db)):
    session = current_session(request, db)
    require_csrf(request, session)
    workspace, role = service.workspace_for(db, workspace_id, session.user_id, lock=True)
    if role not in {"owner", "admin"}:
        raise HTTPException(403, "Only workspace administrators can manage roles and permissions.")
    for role_name, enabled in body.model_dump().items():
        permission = db.scalar(select(RolePermission).where(RolePermission.workspace_id == workspace_id, RolePermission.role == role_name))
        if not permission:
            permission = RolePermission(workspace_id=workspace_id, role=role_name)
            db.add(permission)
        permission.manage_ai_agents = enabled
    db.add(AgentChange(workspace_id=workspace_id, agent_name=workspace.name, actor_id=session.user_id,
        action="roles_updated", summary="Manage AI Agents role permissions updated."))
    db.commit()
    return {"message": "Roles and permissions saved."}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/tester/messages")
def tester_message(workspace_id: uuid.UUID, agent_id: uuid.UUID, body: TesterMessage, request: Request, db: Session = Depends(get_db)):
    session, _, _, agent = access(request, db, workspace_id, mutation=True, agent_id=agent_id, lock=False)
    if not agent.llm_enabled:
        raise HTTPException(409, "Enable the large language model in Personalization before testing replies.")
    if not request.app.state.llm_tester.available:
        raise HTTPException(503, "Connect a language model before testing generative replies.")
    now = utcnow()
    key = token_hash("tester:{}:{}".format(session.user_id, int(now.timestamp()) // 60))
    statement = insert(RateLimit).values(key=key, count=1, expires_at=now + timedelta(minutes=2))
    count = db.execute(statement.on_conflict_do_update(index_elements=[RateLimit.key], set_={"count": RateLimit.count + 1}).returning(RateLimit.count)).scalar_one()
    db.commit()
    if count > request.app.state.settings.llm_tester_requests_per_minute:
        raise HTTPException(429, "Too many tester messages. Wait a minute and try again.")
    if body.conversation_id:
        conversation = db.scalar(select(AgentConversation).where(AgentConversation.id == body.conversation_id,
            AgentConversation.agent_id == agent_id, AgentConversation.workspace_id == workspace_id,
            AgentConversation.user_id == session.user_id).with_for_update())
        if not conversation:
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation = AgentConversation(workspace_id=workspace_id, agent_id=agent_id, user_id=session.user_id, agent_name=agent.name)
        db.add(conversation)
        db.flush()
    recent = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == conversation.id)
        .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc()).limit(20)).all()
    history = [{"role": message.role, "content": message.content} for message in reversed(recent)]
    history.append({"role": "user", "content": body.content})
    content = request.app.state.llm_tester.reply(agent, history)
    db.add(AgentMessage(conversation_id=conversation.id, role="user", content=body.content))
    db.flush()
    reply = AgentMessage(conversation_id=conversation.id, role="assistant", content=content)
    db.add(reply)
    db.commit()
    return {"conversation_id": str(conversation.id), "message": {"id": str(reply.id), "role": "assistant", "content": content, "created_at": reply.created_at.isoformat()}}
