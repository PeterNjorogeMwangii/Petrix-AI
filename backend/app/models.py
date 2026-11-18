import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, JSON, LargeBinary, String, Text, UniqueConstraint, Uuid
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
    person_id: Mapped[Optional[uuid.UUID]]=mapped_column(ForeignKey('support_people.id',ondelete='SET NULL'),index=True)
    __tablename__ = "agent_conversations"
    live_status: Mapped[str] = mapped_column(String(16), default='none', server_default='none')
    live_assigned_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    live_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default='{}')
    live_revision: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    live_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("live_status IN ('none','pending','active','closed') AND live_revision >= 1",name='ck_conversation_live_status'),
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
    __table_args__ = (UniqueConstraint('workspace_id','number',name='uq_support_ticket_number'), UniqueConstraint('conversation_id','action_node_id','execution_id',name='uq_ticket_action_execution'), CheckConstraint("status IN ('open','in_progress','resolved','closed','overdue')",name='ck_support_ticket_status'), CheckConstraint("assignee_type IN ('none','user','team')",name='ck_support_ticket_assignee'), CheckConstraint('revision >= 1',name='ck_support_ticket_revision'))


class TicketNote(Base):
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
    __tablename__='support_people'
    __table_args__=(UniqueConstraint('workspace_id','identity_key',name='uq_support_person_identity'),UniqueConstraint('workspace_id','external_id',name='uq_support_person_external_id'),)
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
