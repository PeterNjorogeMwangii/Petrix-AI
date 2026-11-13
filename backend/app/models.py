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
        CheckConstraint("scope = 'conversation'",name='ck_conversation_field_scope'),
        CheckConstraint("type IN ('text','number','toggle','dropdown','date','single_select_grouped')",name='ck_conversation_field_type'),
        CheckConstraint('revision >= 1',name='ck_conversation_field_revision'))


class AgentConversation(Base):
    __tablename__ = "agent_conversations"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("ai_agents.id", ondelete="SET NULL"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    workflow_state: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMessage(Base):
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
