import base64
import binascii
import io
import uuid
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.agent_schemas import AgentConfiguration
from app.core.security import utcnow
from app.models import ChatAssignment, Agent, AgentChange, AgentVersion, Membership, RolePermission, Workspace

MAX_AVATAR_BYTES = 10 * 1024 * 1024
MAX_IMPORT_BYTES = 15 * 1024 * 1024
DEFAULT_PERSONA = "You are a helpful assistant for this workspace. Be clear, professional and concise. Ask for clarification when needed. Do not invent facts."


def can_manage(db, workspace_id, role):
    from app.workspace_settings.permissions import allowed
    return allowed(db, workspace_id, 'manage_ai_agents', role=role)


def workspace_for(db, workspace_id, user_id, manage=False, lock=False):
    db.info["actor_id"] = user_id
    query = select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id).where(
        Workspace.id == workspace_id, Membership.user_id == user_id
    )
    if lock:
        query = query.with_for_update(of=Workspace).execution_options(populate_existing=True)
    row = db.execute(query).first()
    if not row:
        raise HTTPException(404, "Workspace not found.")
    workspace, role = row
    if not workspace.setup_completed_at:
        raise HTTPException(409, "Complete workspace setup before managing agents.")
    if manage and not can_manage(db, workspace_id, role):
        raise HTTPException(403, "You need the Manage AI Agents permission to make this change.")
    return workspace, role


def agent_for(db, workspace_id, agent_id, lock=False):
    query = select(Agent).where(Agent.id == agent_id, Agent.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    agent = db.scalar(query)
    if not agent:
        raise HTTPException(404, "AI agent not found.")
    return agent


def check_revision(agent, expected):
    if agent.revision != expected:
        raise HTTPException(409, "This agent changed in another session. Reload the agent before saving.")


def check_languages(workspace, languages):
    if len(languages) > workspace.language_limit:
        raise HTTPException(422, "Your workspace plan allows up to {} languages per agent.".format(workspace.language_limit))


def sanitize_avatar(data):
    if not data or len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(422, "Choose a PNG, JPEG, or GIF avatar up to 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "GIF"} or image.width * image.height > 16_000_000:
                    raise ValueError
                image.seek(0)
                image.load()
                rendered = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGBA"), (256, 256), method=Image.Resampling.LANCZOS)
                output = io.BytesIO()
                rendered.save(output, format="PNG")
                return output.getvalue()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "Upload a valid PNG, JPEG, or GIF image. GIF avatars use their first frame.") from None


def avatar_from_base64(value):
    if value is None:
        return None
    try:
        return sanitize_avatar(base64.b64decode(value, validate=True))
    except (ValueError, binascii.Error):
        raise HTTPException(422, "The imported avatar is not valid base64 image data.") from None


def configuration(agent):
    return {
        "name": agent.name, "llm_enabled": agent.llm_enabled, "persona": agent.persona,
        "languages": agent.languages, "default_language": agent.default_language,
        "speech_to_text_enabled": agent.speech_to_text_enabled, "speech_languages": agent.speech_languages, "include_audio_transcript": agent.include_audio_transcript,
        "voice_enabled": agent.voice_enabled, "voice_name": agent.voice_name, "voice_speed": agent.voice_speed,
        "avatar_base64": base64.b64encode(agent.avatar_data).decode("ascii") if agent.avatar_data else None,
    }


def agent_payload(agent):
    payload = configuration(agent)
    payload.pop("avatar_base64")
    payload.update({
        "id": str(agent.id), "teamspace_id": str(agent.teamspace_id) if agent.teamspace_id else None, "workspace_id": str(agent.workspace_id), "revision": agent.revision,
        "avatar_url": "/api/workspaces/{}/agents/{}/avatar?v={}".format(agent.workspace_id, agent.id, agent.revision) if agent.avatar_data else None,
        "created_at": agent.created_at.isoformat(), "updated_at": agent.updated_at.isoformat(),
    })
    return payload


def record_change(db, agent, actor_id, action, summary):
    db.add(AgentChange(workspace_id=agent.workspace_id, agent_id=agent.id, agent_name=agent.name,
        actor_id=actor_id, action=action, summary=summary, revision=agent.revision))


def record_version(db, agent, actor_id, section, summary):
    db.add(AgentVersion(agent_id=agent.id, number=agent.revision, section=section, actor_id=actor_id, snapshot=configuration(agent)))
    record_change(db, agent, actor_id, section, summary)


def create_agent(db, workspace, actor_id, name, language="en", avatar=None, config=None, action="created"):
    agent = Agent(workspace_id=workspace.id, name=name, default_language=language, languages=[language],
        persona=DEFAULT_PERSONA, llm_enabled=True, voice_enabled=False, voice_name="", voice_speed=1.0, revision=1, avatar_data=avatar)
    if config:
        apply_configuration(agent, config, workspace)
    if not agent.teamspace_id:
        from app.workspace_settings.permissions import default_space
        agent.teamspace_id = default_space(db, workspace.id)
    db.add(agent)
    db.flush()
    from app.workflows.service import initialize
    initialize(db, agent)
    record_version(db, agent, actor_id, action, "AI agent {}.".format(action))
    return agent


def initialize_workspace(db, workspace, actor_id):
    from app.workspace_settings.permissions import bootstrap
    bootstrap(db,workspace.id)
    db.add(ChatAssignment(workspace_id=workspace.id,mode="manual",max_active_chats=5,revision=1))
    for role in ("owner", "admin", "member"):
        db.add(RolePermission(workspace_id=workspace.id, role=role, manage_ai_agents=role != "member"))
    create_agent(db, workspace, actor_id, "Default AI Agent")


def apply_configuration(agent, config, workspace):
    check_languages(workspace, config.languages)
    for key, value in config.model_dump(exclude={"avatar_base64"}).items():
        setattr(agent, key, value)
    agent.avatar_data = avatar_from_base64(config.avatar_base64)


def save_agent(db, agent, actor_id, section, summary, before):
    if agent.languages != before["languages"]:
        from app.models import WorkflowDocument
        from app.workflows.actions.switch_language.validation import validate_targets
        document = db.get(WorkflowDocument, agent.id)
        if document:
            validate_targets(document.nodes, agent.languages)
    if configuration(agent) != before:
        agent.revision += 1
        agent.updated_at = utcnow()
        record_version(db, agent, actor_id, section, summary)
    db.commit()
    return agent_payload(agent)
