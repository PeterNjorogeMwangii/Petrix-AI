import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Identity as SQLIdentity, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.security import utcnow


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), default="My AI Workspace")
    industry: Mapped[Optional[str]] = mapped_column(String(80))
    country_code: Mapped[Optional[str]] = mapped_column(String(2))
    website: Mapped[Optional[str]] = mapped_column(String(2048))
    avatar_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    setup_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    plan_name: Mapped[str] = mapped_column(String(60), default="Starter", server_default="Starter")
    # Legacy schema field retained for upgrades; agent creation is unlimited.
    agent_limit: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    language_limit: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    __table_args__ = (CheckConstraint("agent_limit >= 1 AND language_limit >= 1", name="ck_workspace_agent_limits"),)


class Membership(Base):
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_member"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_membership_role"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")
    custom_role_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('workspace_roles.id', ondelete='SET NULL'))
    teamspace_ids: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Identity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "issuer", "subject", name="uq_provider_identity"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingIdentity(Base):
    __tablename__ = "pending_identities"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(20))
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(200))
    email_hint: Mapped[Optional[str]] = mapped_column(String(320))
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MagicLink(Base):
    __tablename__ = "magic_links"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    pending_identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("pending_identities.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    active_workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("workspaces.id", ondelete="SET NULL"))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimit(Base):
    __tablename__ = "auth_rate_limits"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RolePermission(Base):
    __tablename__ = "workspace_role_permissions"
    __table_args__ = (UniqueConstraint("workspace_id", "role", name="uq_workspace_role_permission"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    manage_ai_agents: Mapped[bool] = mapped_column(Boolean, default=False)


class Agent(Base):
    __tablename__ = "ai_agents"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_agent_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    teamspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('teamspaces.id', ondelete='RESTRICT'))
    name: Mapped[str] = mapped_column(String(100))
    avatar_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    llm_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    persona: Mapped[str] = mapped_column(Text, default="")
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    default_language: Mapped[str] = mapped_column(String(16), default="en")
    voice_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    speech_to_text_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    speech_languages: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    include_audio_transcript: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    voice_name: Mapped[str] = mapped_column(String(100), default="")
    voice_speed: Mapped[float] = mapped_column(Float, default=1.0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "number", name="uq_agent_version_number"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentKnowledge(Base):
    __tablename__ = "agent_knowledge"
    __table_args__ = (CheckConstraint("revision >= 1", name="ck_agent_knowledge_revision"),)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentKnowledgeSource(Base):
    __tablename__ = "agent_knowledge_sources"
    __table_args__ = (CheckConstraint("kind IN ('snippet','file','url') AND status IN ('queued','processing','ready','failed') AND revision>=1 AND text_bytes>=0 AND size_bytes>=0", name="ck_knowledge_source_state"), Index('ix_knowledge_source_queue','status','updated_at'))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(10))
    legacy: Mapped[bool] = mapped_column(Boolean,default=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    status: Mapped[str] = mapped_column(String(12),default='queued')
    content: Mapped[str] = mapped_column(Text,default='')
    chunks: Mapped[list] = mapped_column(JSON,default=list)
    source_url: Mapped[str] = mapped_column(String(2048),default='')
    resolved_url: Mapped[str] = mapped_column(String(2048),default='')
    filename: Mapped[str] = mapped_column(String(250),default='')
    media_type: Mapped[str] = mapped_column(String(100),default='')
    file_data: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer,default=0)
    text_bytes: Mapped[int] = mapped_column(Integer,default=0)
    error_code: Mapped[str] = mapped_column(String(60),default='')
    revision: Mapped[int] = mapped_column(Integer,default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)


class AgentKnowledgePolicy(Base):
    __tablename__ = "agent_knowledge_policies"
    __table_args__ = (CheckConstraint('revision>=1',name='ck_knowledge_policy_revision'),)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),primary_key=True)
    master_prompt: Mapped[str] = mapped_column(Text,default='')
    use_for_replies: Mapped[bool] = mapped_column(Boolean,default=True)
    fallback_message: Mapped[str] = mapped_column(String(500),default="Sorry, I can't respond to that request.")
    revision: Mapped[int] = mapped_column(Integer,default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class AgentChange(Base):
    __tablename__ = "agent_changes"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_agents.id", ondelete="SET NULL"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(String(500))
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revision: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentConnection(Base):
    __tablename__ = "agent_connections"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", name="uq_agent_connection"),
        CheckConstraint("source_id <> target_id", name="ck_agent_connection_no_self"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupportTeam(Base):
    __tablename__ = 'support_teams'
    member_ids: Mapped[list] = mapped_column(JSON, default=list, server_default='[]')
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    role_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('workspace_roles.id', ondelete='SET NULL'))
    teamspace_ids: Mapped[Optional[list]] = mapped_column(JSON)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    alternative_names: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_support_team_revision'),)


class ConversationField(Base):
    __tablename__ = 'conversation_fields'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    key: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(24), default='conversation')
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    options: Mapped[list] = mapped_column(JSON, default=list)
    default_value: Mapped[Optional[object]] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('workspace_id','key',name='uq_conversation_field_key'),
        CheckConstraint("scope IN ('conversation','person')",name='ck_conversation_field_scope'),
        CheckConstraint("type IN ('text','number','toggle','dropdown','date','single_select_grouped')",name='ck_conversation_field_type'),
        CheckConstraint('revision >= 1',name='ck_conversation_field_revision'))


class SupportAgent(Base):
    __tablename__ = 'support_agents'
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ['en'])
    available: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    online_since: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_assigned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    presence_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('auth_sessions.id', ondelete='SET NULL'))
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_support_agent_revision'),)


class ChatAssignment(Base):
    __tablename__ = 'chat_assignments'
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    mode: Mapped[str] = mapped_column(String(24), default='manual')
    max_active_chats: Mapped[int] = mapped_column(Integer, default=5)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("mode IN ('manual','round_robin','least_busy','balanced','random','balanced_round_robin')",name='ck_chat_assignment_mode'),
        CheckConstraint('max_active_chats BETWEEN 1 AND 100 AND revision >= 1',name='ck_chat_assignment_limits'))


class AgentConversation(Base):
    message_sequence: Mapped[int] = mapped_column(BigInteger, server_default='0')
    live_description: Mapped[str] = mapped_column(String(4000), default="", server_default="")
    live_display_status: Mapped[str] = mapped_column(String(16), default="", server_default="")
    person_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_people.id',ondelete='SET NULL'),index=True)
    __tablename__ = "agent_conversations"
    live_status: Mapped[str] = mapped_column(String(16), default='none', server_default='none')
    live_assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    live_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    live_revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    live_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint('message_sequence >= 0', name='ck_conversation_message_sequence'),
        CheckConstraint("live_status IN ('none','pending','active','closed') AND live_revision >= 1",name='ck_conversation_live_status'),
        Index('ix_conversation_live_queue','workspace_id','live_status','live_updated_at'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_agents.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    workflow_state: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMessage(Base):
    author_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    __tablename__ = "agent_messages"
    sequence: Mapped[int] = mapped_column(BigInteger, server_default='0')
    __table_args__ = (CheckConstraint('sequence > 0', name='ck_agent_message_sequence'),
        Index('ix_agent_message_sequence', 'conversation_id', 'sequence', unique=True))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    message_data: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowDocument(Base):
    __tablename__ = "agent_workflows"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_workflow_revision"),
        CheckConstraint("next_node_number >= 1", name="ck_workflow_next_node_number"),
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    next_node_number: Mapped[int] = mapped_column(Integer, default=1)
    nodes: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatabaseConnection(Base):
    __tablename__ = "database_connections"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_database_connection_name"),
        CheckConstraint("revision >= 1", name="ck_database_connection_revision"),
        CheckConstraint("port >= 1 AND port <= 65535", name="ck_database_connection_port"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20), default="postgresql")
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=5432)
    database: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128))
    password_encrypted: Mapped[str] = mapped_column(Text)
    ssl_mode: Mapped[str] = mapped_column(String(20), default="verify-full")
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    last_error: Mapped[Optional[str]] = mapped_column(String(50))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentAttachment(Base):
    __tablename__ = "agent_attachments"
    __table_args__ = (CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520", name="ck_agent_attachment_size"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowAsset(Base):
    __tablename__ = "workflow_assets"
    __table_args__ = (CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520", name="ck_workflow_asset_size"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_agents.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalIntegration(Base):
    __tablename__ = 'external_integrations'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'))
    platform: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSON)
    credentials_ciphertext: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('workspace_id','platform',name='uq_external_workspace_platform'),
        CheckConstraint("platform IN ('zendesk','bitrix24') AND revision >= 1",name='ck_external_integration'))


class ExternalConversation(Base):
    __tablename__ = 'external_conversations'
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_conversations.id', ondelete='CASCADE'), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'))
    platform: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(100))
    account_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='linked')
    error: Mapped[str] = mapped_column(String(64), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('workspace_id','platform','account_key','external_id',name='uq_external_provider_conversation'),
        CheckConstraint("platform IN ('zendesk','bitrix24') AND status IN ('linked','sending','transferred','failed','uncertain') AND revision >= 1",name='ck_external_conversation'),
        Index('ix_external_workspace_status','workspace_id','status'))


class AINetworkCredential(Base):
    __tablename__='ai_network_credentials'
    agent_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),primary_key=True)
    actor_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    secret_hash: Mapped[str]=mapped_column(String(64))
    secret_ciphertext: Mapped[str]=mapped_column(Text)
    enabled: Mapped[bool]=mapped_column(Boolean,default=True)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    __table_args__=(CheckConstraint('revision >= 1',name='ck_ai_network_credential_revision'),)


class AITransferConnection(Base):
    __tablename__='ai_transfer_connections'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    source_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),index=True)
    target_id: Mapped[uuid.UUID]=mapped_column(Uuid)
    target_name: Mapped[str]=mapped_column(String(100))
    domain: Mapped[str]=mapped_column(String(250))
    secret_ciphertext: Mapped[str]=mapped_column(Text)
    verified_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(UniqueConstraint('source_id','target_id','domain',name='uq_ai_transfer_target'),)


class AIInboundSession(Base):
    __tablename__='ai_inbound_sessions'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),index=True)
    conversation_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),unique=True)
    transfer_id: Mapped[uuid.UUID]=mapped_column(Uuid)
    credential_revision: Mapped[int]=mapped_column(Integer)
    token_hash: Mapped[str]=mapped_column(String(64))
    token_ciphertext: Mapped[str]=mapped_column(Text)
    trail: Mapped[list]=mapped_column(JSON)
    start_digest: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(16),default='running')
    initial_error: Mapped[str]=mapped_column(String(300),default='')
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    __table_args__=(UniqueConstraint('agent_id','transfer_id',name='uq_ai_inbound_transfer'),
        CheckConstraint("status IN ('running','ready')",name='ck_ai_inbound_status'))


class AIInboundRequest(Base):
    __tablename__='ai_inbound_requests'
    session_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_inbound_sessions.id',ondelete='CASCADE'),primary_key=True)
    request_id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True)
    digest: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(16),default='running')
    response: Mapped[dict]=mapped_column(JSON,default=dict)
    __table_args__=(CheckConstraint("status IN ('running','completed')",name='ck_ai_inbound_request_status'),)


class AIOutgoingSession(Base):
    __tablename__='ai_outgoing_sessions'
    conversation_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),primary_key=True)
    connection_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('ai_transfer_connections.id',ondelete='SET NULL'))
    transfer_id: Mapped[uuid.UUID]=mapped_column(Uuid,default=uuid.uuid4,unique=True)
    target_id: Mapped[uuid.UUID]=mapped_column(Uuid)
    target_name: Mapped[str]=mapped_column(String(100))
    domain: Mapped[str]=mapped_column(String(250))
    status: Mapped[str]=mapped_column(String(16),default='starting')
    remote_session_id: Mapped[Optional[uuid.UUID]]=mapped_column(Uuid)
    token_ciphertext: Mapped[str]=mapped_column(Text,default='')
    start_payload: Mapped[dict]=mapped_column(JSON,default=dict)
    pending: Mapped[dict]=mapped_column(JSON,default=dict)
    last_result: Mapped[dict]=mapped_column(JSON,default=dict)
    error: Mapped[str]=mapped_column(String(300),default='')
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint("status IN ('starting','active','failed')",name='ck_ai_outgoing_status'),)


class TicketTagGroup(Base):
    __tablename__ = 'ticket_tag_groups'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_ticket_tag_group_revision'),)


class TicketTag(Base):
    __tablename__ = 'ticket_tags'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    group_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey('ticket_tag_groups.id', ondelete='SET NULL'))
    name: Mapped[str] = mapped_column(String(100))
    color: Mapped[str] = mapped_column(String(7), default='#d19a70')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_ticket_tag_revision'),)


class TicketEmailDomain(Base):
    __tablename__ = 'ticket_email_domains'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    from_name: Mapped[str] = mapped_column(String(100), default='Petrix Support')
    local_part: Mapped[str] = mapped_column(String(64), default='support')
    verification_method: Mapped[str] = mapped_column(String(8))
    challenge: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("verification_method IN ('cname','txt')", name='ck_ticket_domain_method'), CheckConstraint('revision >= 1', name='ck_ticket_domain_revision'))


class TicketEmailTemplate(Base):
    __tablename__ = 'ticket_email_templates'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(1000), default='')
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_ticket_template_revision'),)


class TicketCounter(Base):
    __tablename__ = 'ticket_counters'
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint('next_number >= 1', name='ck_ticket_counter_positive'),)


class SupportTicket(Base):
    teamspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('teamspaces.id',ondelete='RESTRICT'),index=True)
    assigned_team_ids: Mapped[list] = mapped_column(JSON,default=list,server_default='[]')
    sender_domain_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ticket_email_domains.id',ondelete='SET NULL'))
    origin: Mapped[str] = mapped_column(String(12),default='workflow',server_default='workflow')
    client_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    create_payload_hash: Mapped[str] = mapped_column(String(64),default='',server_default='')
    person_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_people.id',ondelete='SET NULL'),index=True)
    overdue_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    audience_team_ids: Mapped[list]=mapped_column(JSON,default=list,server_default='[]')
    __tablename__ = 'support_tickets'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey('agent_conversations.id', ondelete='SET NULL'), index=True)
    creator_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey('users.id', ondelete='SET NULL'))
    action_node_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    number: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(40))
    agent_name: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default='')
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str] = mapped_column(String(50), default='')
    status: Mapped[str] = mapped_column(String(16), default='open', index=True)
    assignee_type: Mapped[str] = mapped_column(String(8), default='none')
    assignee_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    assignee_name: Mapped[str] = mapped_column(String(200), default='Unassigned')
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('workspace_id','client_request_id',name='uq_ticket_manual_request'),UniqueConstraint('workspace_id','number',name='uq_support_ticket_number'), UniqueConstraint('conversation_id','action_node_id','execution_id',name='uq_ticket_action_execution'), CheckConstraint("status IN ('open','in_progress','resolved','closed','overdue')",name='ck_support_ticket_status'), CheckConstraint("assignee_type IN ('none','user','team')",name='ck_support_ticket_assignee'), CheckConstraint('revision >= 1',name='ck_support_ticket_revision'))


class TicketNote(Base):
    color: Mapped[str] = mapped_column(String(7),default='#fff0b3',server_default='#fff0b3')
    revision: Mapped[int] = mapped_column(Integer,default=1,server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,server_default=func.now())
    __tablename__ = 'ticket_notes'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('support_tickets.id', ondelete='CASCADE'), index=True)
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey('users.id', ondelete='SET NULL'))
    author_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8), default='note')
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint("kind IN ('note','event')",name='ck_ticket_note_kind'),)


class TicketEmail(Base):
    cc_recipients: Mapped[list] = mapped_column(JSON,default=list,server_default='[]')
    delivery_status: Mapped[str] = mapped_column(String(16),default='',server_default='')
    delivery_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __tablename__ = 'ticket_emails'
    kind: Mapped[str]=mapped_column(String(8),default='initial',server_default='initial')
    actor_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    client_request_id: Mapped[Optional[uuid.UUID]]=mapped_column(Uuid)
    persona: Mapped[dict]=mapped_column(JSON,default=dict,server_default='{}')
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey('support_tickets.id', ondelete='CASCADE'), index=True)
    domain_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, ForeignKey('ticket_email_domains.id', ondelete='SET NULL'))
    recipient_type: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(320))
    sender: Mapped[str] = mapped_column(String(320))
    sender_name: Mapped[str] = mapped_column(String(100))
    subject: Mapped[str] = mapped_column(String(250))
    body_html: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(16), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(250), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index('uq_ticket_initial_recipient','ticket_id','recipient_type','recipient',unique=True,postgresql_where=__import__('sqlalchemy').text("kind='initial'")),UniqueConstraint('ticket_id','client_request_id',name='uq_ticket_reply_request'),CheckConstraint("kind IN ('initial','reply')",name='ck_ticket_email_kind'), CheckConstraint("status IN ('queued','sending','sent','failed','uncertain')",name='ck_ticket_email_status'), CheckConstraint("recipient_type IN ('customer','assignee')",name='ck_ticket_email_recipient'), CheckConstraint('revision >= 1 AND attempts >= 0',name='ck_ticket_email_counters'))


class WorkspaceSettings(Base):
    __tablename__ = 'workspace_settings'
    timezone: Mapped[str]=mapped_column(String(64),default='Africa/Nairobi',server_default='Africa/Nairobi')
    analytics_started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,server_default=func.now())
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    ip_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    exempt_admins: Mapped[bool] = mapped_column(Boolean, default=True)
    ip_ranges: Mapped[list] = mapped_column(JSON, default=list)
    idle_minutes: Mapped[int] = mapped_column(Integer, default=0)
    main_logo: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    square_logo: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    colors: Mapped[dict] = mapped_column(JSON, default=dict)
    branding_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class WorkspaceRole(Base):
    __tablename__ = 'workspace_roles'
    __table_args__ = (UniqueConstraint('workspace_id', 'name_key', name='uq_workspace_custom_role_name'), CheckConstraint("kind IN ('workspace','team')", name='ck_custom_role_kind'))
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000), default='')
    kind: Mapped[str] = mapped_column(String(12), default='workspace')
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Teamspace(Base):
    __tablename__ = 'teamspaces'
    __table_args__ = (UniqueConstraint('workspace_id', 'name_key', name='uq_teamspace_name'),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class WorkspaceInvite(Base):
    __tablename__ = 'workspace_invites'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('workspace_roles.id', ondelete='SET NULL'))
    team_ids: Mapped[list] = mapped_column(JSON, default=list)
    teamspace_ids: Mapped[Optional[list]] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageEvent(Base):
    __tablename__ = 'workspace_usage_events'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    category: Mapped[str] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(40), default='workflow')
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ai_agents.id', ondelete='SET NULL'))
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('support_teams.id', ondelete='SET NULL'))
    teamspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('teamspaces.id', ondelete='SET NULL'))
    units: Mapped[float] = mapped_column(Float, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WorkspaceBilling(Base):
    __tablename__ = 'workspace_billing'
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    subscription_id: Mapped[Optional[str]] = mapped_column(String(100))
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    auto_topup: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class BillingOperation(Base):
    __tablename__ = 'workspace_billing_operations'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    provider_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    credits: Mapped[float] = mapped_column(Float, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillingWebhook(Base):
    __tablename__ = 'workspace_billing_webhooks'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TeamspaceOperations(Base):
    __tablename__='teamspace_operations'
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),primary_key=True)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    analysis: Mapped[dict]=mapped_column(JSON,default=dict)
    copilot: Mapped[dict]=mapped_column(JSON,default=dict)
    assignment: Mapped[dict]=mapped_column(JSON,default=dict)
    enforcement: Mapped[dict]=mapped_column(JSON,default=dict)
    overdue: Mapped[dict]=mapped_column(JSON,default=dict)
    analytics: Mapped[dict]=mapped_column(JSON,default=dict)
    profile_iframe: Mapped[dict]=mapped_column(JSON,default=dict)


class SupportPerson(Base):
    client_request_id: Mapped[Optional[uuid.UUID]]=mapped_column(Uuid)
    create_payload_hash: Mapped[str]=mapped_column(String(64),default='',server_default='')
    __tablename__='support_people'
    __table_args__=(UniqueConstraint('workspace_id','client_request_id',name='uq_person_manual_request'),UniqueConstraint('workspace_id','identity_key',name='uq_support_person_identity'),UniqueConstraint('workspace_id','external_id',name='uq_support_person_external_id'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('teamspaces.id',ondelete='RESTRICT'))
    team_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_teams.id',ondelete='SET NULL'))
    user_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    identity_key: Mapped[str]=mapped_column(String(200))
    external_id: Mapped[Optional[str]]=mapped_column(String(200))
    name: Mapped[str]=mapped_column(String(200),default='')
    email: Mapped[str]=mapped_column(String(320),default='')
    phone: Mapped[str]=mapped_column(String(50),default='')
    location: Mapped[str]=mapped_column(String(200),default='')
    country: Mapped[str]=mapped_column(String(2),default='')
    custom_fields: Mapped[dict]=mapped_column(JSON,default=dict)
    offences: Mapped[int]=mapped_column(Integer,default=0)
    ban_permanent: Mapped[bool]=mapped_column(Boolean,default=False)
    banned_until: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    ban_reason: Mapped[str]=mapped_column(String(200),default='')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class BanRule(Base):
    __tablename__='support_ban_rules'
    __table_args__=(UniqueConstraint('teamspace_id','offences',name='uq_ban_rule_offences'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    offences: Mapped[int]=mapped_column(Integer)
    duration: Mapped[int]=mapped_column(Integer,default=60)
    unit: Mapped[str]=mapped_column(String(12),default='minutes')
    revision: Mapped[int]=mapped_column(Integer,default=1)


class BanReason(Base):
    __tablename__='support_ban_reasons'
    __table_args__=(UniqueConstraint('teamspace_id','name_key',name='uq_ban_reason_name'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    name: Mapped[str]=mapped_column(String(200))
    name_key: Mapped[str]=mapped_column(String(400))
    description: Mapped[str]=mapped_column(String(2000),default='')
    revision: Mapped[int]=mapped_column(Integer,default=1)


class PersonBanEvent(Base):
    __tablename__='person_ban_events'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    person_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('support_people.id',ondelete='CASCADE'),index=True)
    actor_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    reason: Mapped[str]=mapped_column(String(200))
    permanent: Mapped[bool]=mapped_column(Boolean,default=False)
    until: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class CannedReply(Base):
    __tablename__='support_canned_replies'
    __table_args__=(UniqueConstraint('teamspace_id','name_key',name='uq_canned_reply_name'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    name: Mapped[str]=mapped_column(String(100))
    name_key: Mapped[str]=mapped_column(String(200))
    language: Mapped[str]=mapped_column(String(16),default='en')
    content: Mapped[str]=mapped_column(String(4000))
    revision: Mapped[int]=mapped_column(Integer,default=1)


class RecordLabels(Base):
    __tablename__='support_record_labels'
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True)
    kind: Mapped[str]=mapped_column(String(16),primary_key=True)
    record_id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True)
    tag_ids: Mapped[list]=mapped_column(JSON,default=list)
    group_ids: Mapped[list]=mapped_column(JSON,default=list)
    revision: Mapped[int]=mapped_column(Integer,default=1)


class TagGroupMembership(Base):
    __tablename__='support_tag_group_memberships'
    tag_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ticket_tags.id',ondelete='CASCADE'),primary_key=True)
    group_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('ticket_tag_groups.id',ondelete='CASCADE'),primary_key=True)


class CopilotKnowledge(Base):
    __tablename__='copilot_knowledge_sources'
    __table_args__=(UniqueConstraint('teamspace_id','name_key',name='uq_copilot_source_name'),)
    status: Mapped[str]=mapped_column(String(12),default='success',server_default='success')
    error: Mapped[str]=mapped_column(String(300),default='',server_default='')
    options: Mapped[dict]=mapped_column(JSON,default=dict,server_default='{}')
    next_refresh_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    name: Mapped[str]=mapped_column(String(200))
    name_key: Mapped[str]=mapped_column(String(400))
    kind: Mapped[str]=mapped_column(String(12))
    url: Mapped[str]=mapped_column(String(2048),default='')
    content: Mapped[str]=mapped_column(Text)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class SupportJob(Base):
    __tablename__='support_jobs'
    __table_args__=(UniqueConstraint('workspace_id','request_key',name='uq_support_job_request'),CheckConstraint("status IN ('pending','running','completed','failed')",name='ck_support_job_status'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    actor_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    actor_team_ids: Mapped[list]=mapped_column(JSON,default=list)
    kind: Mapped[str]=mapped_column(String(16))
    target_kind: Mapped[str]=mapped_column(String(16))
    target_id: Mapped[Optional[uuid.UUID]]=mapped_column(Uuid)
    request_key: Mapped[str]=mapped_column(String(200))
    status: Mapped[str]=mapped_column(String(16),default='pending',index=True)
    payload: Mapped[dict]=mapped_column(JSON,default=dict)
    output: Mapped[dict]=mapped_column(JSON,default=dict)
    file_data: Mapped[Optional[bytes]]=mapped_column(LargeBinary)
    error: Mapped[str]=mapped_column(String(300),default='')
    lease_id: Mapped[Optional[uuid.UUID]]=mapped_column(Uuid)
    lease_until: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    finished_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))


class ProfileAsset(Base):
    __tablename__='profile_assets'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    data: Mapped[bytes]=mapped_column(LargeBinary)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class UserProfile(Base):
    __tablename__='user_profiles'
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    first_name: Mapped[str]=mapped_column(String(100),default='')
    last_name: Mapped[str]=mapped_column(String(100),default='')
    avatar_asset_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('profile_assets.id',ondelete='SET NULL'))
    language: Mapped[str]=mapped_column(String(16),default='en')
    presence: Mapped[str]=mapped_column(String(10),default='online')
    theme: Mapped[str]=mapped_column(String(10),default='system')
    notifications: Mapped[dict]=mapped_column(JSON,default=dict)
    multichat: Mapped[bool]=mapped_column(Boolean,default=False)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    __table_args__=(CheckConstraint("presence IN ('online','away','offline') AND theme IN ('system','light','dark') AND revision>=1",name='ck_user_profile_preferences'),)


class UserAlias(Base):
    __tablename__='user_aliases'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    name: Mapped[str]=mapped_column(String(100))
    name_key: Mapped[str]=mapped_column(String(200))
    avatar_asset_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('profile_assets.id',ondelete='SET NULL'))
    is_default: Mapped[bool]=mapped_column(Boolean,default=False)
    signature_name: Mapped[str]=mapped_column(String(200),default='')
    signature_content: Mapped[str]=mapped_column(String(4000),default='')
    archived: Mapped[bool]=mapped_column(Boolean,default=False)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(Index('uq_user_alias_name','user_id','name_key',unique=True,postgresql_where=__import__('sqlalchemy').text('NOT archived')),Index('uq_user_default_alias','user_id',unique=True,postgresql_where=__import__('sqlalchemy').text('is_default AND NOT archived')),)


class ConversationPersona(Base):
    __tablename__='conversation_personas'
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True)
    kind: Mapped[str]=mapped_column(String(8),primary_key=True)
    record_id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True)
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    alias_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('user_aliases.id',ondelete='SET NULL'))
    revision: Mapped[int]=mapped_column(Integer,default=1)
    __table_args__=(CheckConstraint("kind IN ('chats','tickets') AND revision>=1",name='ck_conversation_persona'),)


class LivechatNote(Base):
    __tablename__ = 'livechat_notes'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_conversations.id', ondelete='CASCADE'), index=True)
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    author_name: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(String(4000))
    color: Mapped[str] = mapped_column(String(7), default='#fff0b3')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('revision >= 1', name='ck_livechat_note_revision'),)


class TicketInboundEmail(Base):
    __tablename__='ticket_inbound_emails'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('support_tickets.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[str] = mapped_column(String(320))
    sender: Mapped[str] = mapped_column(String(320))
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(250))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class TicketMailEvent(Base):
    __table_args__=(UniqueConstraint('workspace_id','message_key',name='uq_ticket_inbound_message'),)
    message_key: Mapped[Optional[str]] = mapped_column(String(64))
    __tablename__='ticket_mail_events'
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True)
    event_id: Mapped[str] = mapped_column(String(200),primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    ticket_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('support_tickets.id',ondelete='SET NULL'))
    email_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('ticket_emails.id',ondelete='SET NULL'))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class PersonNote(Base):
    __tablename__='person_notes'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    person_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('support_people.id',ondelete='CASCADE'),index=True)
    author_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    author_name: Mapped[str]=mapped_column(String(200))
    content: Mapped[str]=mapped_column(Text)
    color: Mapped[str]=mapped_column(String(7),default='#fff0b3')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint('revision >= 1',name='ck_person_note_revision'),)


class InboxView(Base):
    __tablename__='inbox_views'
    __table_args__=(CheckConstraint("scope IN ('private','teamspace') AND revision>=1",name='ck_inbox_view'),)
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    owner_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'))
    scope: Mapped[str]=mapped_column(String(12),default='private')
    name: Mapped[str]=mapped_column(String(100))
    filters: Mapped[dict]=mapped_column(JSON,default=dict)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class InboxPreference(Base):
    __tablename__='inbox_preferences'
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True)
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    pinned: Mapped[list]=mapped_column(JSON,default=list)
    sort: Mapped[str]=mapped_column(String(12),default='')
    direction: Mapped[str]=mapped_column(String(4),default='desc')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    __table_args__=(CheckConstraint("sort IN ('','created','latest','wait','handoff') AND direction IN ('asc','desc') AND revision>=1",name='ck_inbox_preference'),)


class InboxAttachment(Base):
    __tablename__='inbox_attachments'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    kind: Mapped[str]=mapped_column(String(8))
    record_id: Mapped[uuid.UUID]=mapped_column(Uuid,index=True)
    actor_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    file_name: Mapped[str]=mapped_column(String(255))
    content_type: Mapped[str]=mapped_column(String(120))
    size_bytes: Mapped[int]=mapped_column(Integer)
    data: Mapped[bytes]=mapped_column(LargeBinary)
    used: Mapped[bool]=mapped_column(Boolean,default=False)
    private: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__=(CheckConstraint("kind IN ('chats','tickets') AND size_bytes>0 AND size_bytes<=10485760",name='ck_inbox_attachment'),)


class AnalyticsActivity(Base):
    __tablename__='analytics_activity'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    user_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    session_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('auth_sessions.id',ondelete='CASCADE'))
    state: Mapped[str]=mapped_column(String(12))
    capacity: Mapped[int]=mapped_column(Integer,default=0)
    capacities: Mapped[dict]=mapped_column(JSON,default=dict,server_default='{}')
    started_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    ended_at: Mapped[Optional[datetime]]=mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    __table_args__=(CheckConstraint("state IN ('online','away','offline') AND capacity>=0 AND lease_until>=started_at",name='ck_analytics_activity'),Index('ix_analytics_activity_window','workspace_id','started_at','lease_until'),Index('ix_analytics_activity_session','workspace_id','session_id','ended_at','started_at'))


class AnalyticsRecordEvent(Base):
    __tablename__='analytics_record_events'
    id: Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    conversation_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),index=True)
    ticket_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_tickets.id',ondelete='CASCADE'),index=True)
    event: Mapped[str]=mapped_column(String(16))
    assigned_user_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    team_ids: Mapped[list]=mapped_column(JSON,default=list)
    status: Mapped[str]=mapped_column(String(16))
    detail: Mapped[dict]=mapped_column(JSON,default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    __table_args__=(CheckConstraint("event IN ('assignment','status','feedback') AND ((conversation_id IS NOT NULL AND ticket_id IS NULL) OR (ticket_id IS NOT NULL AND conversation_id IS NULL))",name='ck_analytics_record_event'),)


class PerceptionSettings(Base):
    __tablename__ = 'perception_settings'
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    revision: Mapped[int] = mapped_column(Integer,default=1)
    use_custom: Mapped[bool] = mapped_column(Boolean,default=True)
    use_teams: Mapped[bool] = mapped_column(Boolean,default=False)
    translate: Mapped[bool] = mapped_column(Boolean,default=False)
    target_language: Mapped[str] = mapped_column(String(12),default='en')
    excluded_languages: Mapped[list] = mapped_column(JSON,default=list)
    __table_args__ = (CheckConstraint('revision>=1',name='ck_perception_settings_revision'),)


class PerceptionKeyword(Base):
    __tablename__ = 'perception_keywords'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('support_teams.id',ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String(12),default='custom')
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    variations: Mapped[list] = mapped_column(JSON,default=list)
    tag_ids: Mapped[list] = mapped_column(JSON,default=list)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True)
    revision: Mapped[int] = mapped_column(Integer,default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__ = (UniqueConstraint('teamspace_id','kind','name_key',name='uq_perception_keyword_name'),UniqueConstraint('teamspace_id','team_id',name='uq_perception_team_keyword'),CheckConstraint("revision>=1 AND ((kind='custom' AND team_id IS NULL) OR (kind='team' AND team_id IS NOT NULL))",name='ck_perception_keyword_kind'))


class PerceptionSource(Base):
    __tablename__ = 'perception_sources'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    platform: Mapped[str] = mapped_column(String(16))
    method: Mapped[str] = mapped_column(String(12))
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2048),default='')
    timezone: Mapped[str] = mapped_column(String(64),default='Africa/Nairobi')
    recurring: Mapped[bool] = mapped_column(Boolean,default=False)
    interval_unit: Mapped[str] = mapped_column(String(12),default='hours')
    interval_value: Mapped[int] = mapped_column(Integer,default=24)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer,default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__ = (UniqueConstraint('teamspace_id','platform','name_key',name='uq_perception_source_name'),CheckConstraint("platform IN ('facebook','x') AND method IN ('page','keyword') AND revision>=1",name='ck_perception_source_kind'),CheckConstraint("(interval_unit='days' AND interval_value BETWEEN 1 AND 30) OR (interval_unit='hours' AND interval_value BETWEEN 1 AND 720) OR (interval_unit='minutes' AND interval_value BETWEEN 10 AND 720)",name='ck_perception_source_interval'))


class PerceptionSourceKeyword(Base):
    __tablename__ = 'perception_source_keywords'
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('perception_sources.id',ondelete='CASCADE'),primary_key=True)
    keyword_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('perception_keywords.id',ondelete='CASCADE'),primary_key=True)
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'))
    platform: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint('teamspace_id','platform','keyword_id',name='uq_perception_keyword_platform'),)


class PerceptionRun(Base):
    __tablename__ = 'perception_runs'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('perception_sources.id',ondelete='CASCADE'),index=True)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id',ondelete='SET NULL'))
    actor_name: Mapped[str] = mapped_column(String(320),default='Petrix AI')
    trigger: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(64),default='scrape')
    status: Mapped[str] = mapped_column(String(16),default='scheduled')
    range_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    worker_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    error: Mapped[str] = mapped_column(String(1000),default='')
    detail: Mapped[dict] = mapped_column(JSON,default=dict)
    __table_args__ = (CheckConstraint("status IN ('scheduled','in_progress','success','failed','cancelled') AND trigger IN ('manual','historical','recurring','event') AND attempts>=0",name='ck_perception_run'),Index('ix_perception_run_claim','status','scheduled_at'))


class PerceptionEntry(Base):
    __tablename__ = 'perception_entries'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    platform: Mapped[str] = mapped_column(String(16))
    external_key: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(2048),default='')
    kind: Mapped[str] = mapped_column(String(12),default='post')
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),index=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    sources: Mapped[dict] = mapped_column(JSON,default=dict)
    keywords: Mapped[dict] = mapped_column(JSON,default=dict)
    tag_ids: Mapped[list] = mapped_column(JSON,default=list)
    categories: Mapped[list] = mapped_column(JSON,default=list)
    sentiment: Mapped[str] = mapped_column(String(12),default='unknown')
    language: Mapped[str] = mapped_column(String(12),default='')
    translation: Mapped[str] = mapped_column(Text,default='')
    processing: Mapped[str] = mapped_column(String(16),default='pending',index=True)
    processing_config: Mapped[dict] = mapped_column(JSON,default=dict)
    processing_error: Mapped[str] = mapped_column(String(500),default='')
    processing_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    processing_lease: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('teamspace_id','platform','external_key',name='uq_perception_entry_external'),CheckConstraint("sentiment IN ('positive','negative','neutral','mixed','unknown') AND processing IN ('pending','in_progress','completed','failed','skipped')",name='ck_perception_entry_processing'),Index('ix_perception_entry_window','teamspace_id','posted_at'))


class AdvisorConversation(Base):
    __tablename__ = 'advisor_conversations'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    teamspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('teamspaces.id',ondelete='CASCADE'),index=True)
    title: Mapped[str] = mapped_column(String(160),default='New chat')
    revision: Mapped[int] = mapped_column(Integer,default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    __table_args__ = (UniqueConstraint('workspace_id','user_id','client_id',name='uq_advisor_conversation_client'),CheckConstraint('revision>=1',name='ck_advisor_conversation_revision'),Index('ix_advisor_history','workspace_id','user_id','updated_at'))


class AdvisorMessage(Base):
    __tablename__ = 'advisor_messages'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('advisor_conversations.id',ondelete='CASCADE'),index=True)
    client_request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text,default='')
    payload_hash: Mapped[str] = mapped_column(String(64),default='')
    status: Mapped[str] = mapped_column(String(16),default='completed',index=True)
    mode: Mapped[str] = mapped_column(String(24),default='')
    sources: Mapped[list] = mapped_column(JSON,default=list)
    navigation: Mapped[list] = mapped_column(JSON,default=list)
    notice: Mapped[str] = mapped_column(String(1000),default='')
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    worker_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    __table_args__ = (UniqueConstraint('conversation_id','client_request_id','role',name='uq_advisor_message_request'),CheckConstraint("role IN ('user','assistant') AND status IN ('pending','running','completed','failed') AND attempts>=0",name='ck_advisor_message'),Index('ix_advisor_messages_order','conversation_id','created_at','id'))


class AdvisorAttachment(Base):
    __tablename__ = 'advisor_attachments'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('advisor_messages.id',ondelete='CASCADE'),index=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    preview: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    extracted_text: Mapped[str] = mapped_column(Text,default='')
    note: Mapped[str] = mapped_column(String(500),default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow,index=True)
    __table_args__ = (CheckConstraint('size>0 AND size<=10485760',name='ck_advisor_attachment_size'),)


class WebchatChannel(Base):
    __tablename__='webchat_channels'
    __table_args__=(UniqueConstraint('agent_id','client_id',name='uq_webchat_channel_client'),CheckConstraint('revision>=1',name='ck_webchat_channel_revision'))
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    client_id:Mapped[uuid.UUID]=mapped_column(Uuid)
    agent_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),index=True)
    workspace_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('workspaces.id',ondelete='CASCADE'),index=True)
    actor_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'))
    name:Mapped[str]=mapped_column(String(100))
    enabled:Mapped[bool]=mapped_column(Boolean,default=False)
    config:Mapped[dict]=mapped_column(JSON,default=dict)
    runtime:Mapped[dict]=mapped_column(JSON,default=dict)
    revision:Mapped[int]=mapped_column(Integer,default=1)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class WebchatAsset(Base):
    __tablename__='webchat_assets'
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    agent_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('ai_agents.id',ondelete='CASCADE'),index=True)
    data:Mapped[bytes]=mapped_column(LargeBinary)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class WebchatPreview(Base):
    __tablename__='webchat_previews'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    channel_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('webchat_channels.id',ondelete='CASCADE'),index=True)
    config:Mapped[dict]=mapped_column(JSON)
    runtime:Mapped[dict]=mapped_column(JSON)
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)


class WebchatSession(Base):
    __tablename__='webchat_sessions'
    id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    channel_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('webchat_channels.id',ondelete='CASCADE'),index=True)
    token_hash:Mapped[str]=mapped_column(String(64),unique=True)
    preview_hash:Mapped[str]=mapped_column(String(64),default='')
    host_origin:Mapped[str]=mapped_column(String(300))
    person_id:Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_people.id',ondelete='SET NULL'))
    expires_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class WebchatConversation(Base):
    __tablename__='webchat_conversations'
    conversation_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),primary_key=True)
    session_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('webchat_sessions.id',ondelete='CASCADE'),index=True)
    runtime:Mapped[dict]=mapped_column(JSON)
    started:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class WebchatRequest(Base):
    __tablename__='webchat_requests'
    __table_args__=(CheckConstraint("status IN ('running','completed','failed')",name='ck_webchat_request_status'),)
    session_id:Mapped[uuid.UUID]=mapped_column(ForeignKey('webchat_sessions.id',ondelete='CASCADE'),primary_key=True)
    request_id:Mapped[uuid.UUID]=mapped_column(Uuid,primary_key=True)
    digest:Mapped[str]=mapped_column(String(64))
    conversation_id:Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'))
    status:Mapped[str]=mapped_column(String(16),default='running')
    error:Mapped[str]=mapped_column(String(300),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=utcnow)


class WhatsAppChannel(Base):
    __tablename__ = 'whatsapp_channels'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('workspaces.id', ondelete='CASCADE'), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('ai_agents.id', ondelete='CASCADE'), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime: Mapped[dict] = mapped_column(JSON, default=dict)
    app_id: Mapped[str] = mapped_column(String(32), default='', index=True)
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    credentials: Mapped[str] = mapped_column(Text, default='')
    setup_secret: Mapped[str] = mapped_column(Text)
    verify_token_hash: Mapped[str] = mapped_column(String(64))
    connection_revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_revision: Mapped[Optional[int]] = mapped_column(Integer)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    webhook_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    display_phone: Mapped[str] = mapped_column(String(60), default='')
    verified_name: Mapped[str] = mapped_column(String(200), default='')
    last_error: Mapped[str] = mapped_column(String(300), default='')
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('agent_id','client_id',name='uq_whatsapp_channel_client'),
        CheckConstraint('revision >= 1 AND connection_revision >= 1',name='ck_whatsapp_channel_revision'))


class WhatsAppContact(Base):
    __tablename__ = 'whatsapp_contacts'
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('whatsapp_channels.id',ondelete='CASCADE'), index=True)
    phone_number_id: Mapped[str] = mapped_column(String(32))
    identity_key: Mapped[str] = mapped_column(String(160))
    wa_id: Mapped[str] = mapped_column(String(30),default='')
    bsuid: Mapped[str] = mapped_column(String(160),default='')
    person_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('support_people.id',ondelete='SET NULL'))
    current_conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_conversations.id',ondelete='SET NULL'))
    last_customer_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__ = (UniqueConstraint('channel_id','phone_number_id','identity_key',name='uq_whatsapp_contact_identity'),)


class WhatsAppConversation(Base):
    __tablename__ = 'whatsapp_conversations'
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),primary_key=True)
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('whatsapp_channels.id',ondelete='SET NULL'),index=True)
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('whatsapp_contacts.id',ondelete='SET NULL'),index=True)
    phone_number_id: Mapped[str] = mapped_column(String(32))
    runtime: Mapped[dict] = mapped_column(JSON,default=dict)
    started: Mapped[bool] = mapped_column(Boolean,default=False)
    queued_sequence: Mapped[int] = mapped_column(BigInteger,default=0)
    feedback_state: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)


class WhatsAppInbound(Base):
    __tablename__ = 'whatsapp_inbound'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    position: Mapped[int] = mapped_column(BigInteger,SQLIdentity(),unique=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('whatsapp_channels.id',ondelete='CASCADE'),index=True)
    contact_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('whatsapp_contacts.id',ondelete='SET NULL'),index=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('agent_conversations.id',ondelete='SET NULL'))
    event_key: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON,default=dict)
    connection_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16),default='pending')
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(300),default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('channel_id','event_key',name='uq_whatsapp_inbound_event'),
        CheckConstraint("status IN ('pending','running','completed','failed','uncertain','dismissed','ignored')",name='ck_whatsapp_inbound_status'))


class WhatsAppOutbox(Base):
    __tablename__ = 'whatsapp_outbox'
    id: Mapped[uuid.UUID] = mapped_column(Uuid,primary_key=True,default=uuid.uuid4)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('whatsapp_channels.id',ondelete='CASCADE'),index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_conversations.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('agent_messages.id',ondelete='CASCADE'),index=True)
    message_sequence: Mapped[int] = mapped_column(BigInteger)
    part: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON,default=dict)
    buttons: Mapped[dict] = mapped_column(JSON,default=dict)
    connection_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16),default='queued')
    provider_id: Mapped[Optional[str]] = mapped_column(String(512),unique=True)
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(String(300),default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utcnow)
    __table_args__ = (UniqueConstraint('message_id','part',name='uq_whatsapp_delivery_part'),
        CheckConstraint("status IN ('queued','sending','sent','delivered','read','failed','uncertain','blocked','cancelled')",name='ck_whatsapp_outbox_status'))
