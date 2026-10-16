-- Petrix AI reference schema, Milestone 3. Use Alembic to apply schema changes and seed defaults.

CREATE TABLE auth_rate_limits (
	key VARCHAR(64) NOT NULL, 
	count INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (key)
);

CREATE INDEX ix_auth_rate_limits_expires_at ON auth_rate_limits (expires_at);

CREATE TABLE pending_identities (
	id UUID NOT NULL, 
	ticket_hash VARCHAR(64) NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	issuer VARCHAR(255) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	full_name VARCHAR(200), 
	email_hint VARCHAR(320), 
	requested_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (ticket_hash)
);

CREATE INDEX ix_pending_identities_expires_at ON pending_identities (expires_at);

CREATE TABLE users (
	id UUID NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	full_name VARCHAR(200), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (email)
);

CREATE TABLE workspaces (
	id UUID NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	industry VARCHAR(80), 
	country_code VARCHAR(2), 
	website VARCHAR(2048), 
	avatar_data BYTEA, 
	setup_completed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	plan_name VARCHAR(60) DEFAULT 'Starter' NOT NULL, 
	agent_limit INTEGER DEFAULT '3' NOT NULL, 
	language_limit INTEGER DEFAULT '3' NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_workspace_agent_limits CHECK (agent_limit >= 1 AND language_limit >= 1)
);

CREATE TABLE ai_agents (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	avatar_data BYTEA, 
	llm_enabled BOOLEAN NOT NULL, 
	persona TEXT NOT NULL, 
	languages JSON NOT NULL, 
	default_language VARCHAR(16) NOT NULL, 
	voice_enabled BOOLEAN NOT NULL, 
	voice_name VARCHAR(100) NOT NULL, 
	voice_speed FLOAT NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT ck_agent_revision CHECK (revision >= 1), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);

CREATE INDEX ix_ai_agents_workspace_id ON ai_agents (workspace_id);

CREATE TABLE auth_identities (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	issuer VARCHAR(255) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_provider_identity UNIQUE (provider, issuer, subject), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_identities_user_id ON auth_identities (user_id);

CREATE TABLE auth_sessions (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	session_hash VARCHAR(64) NOT NULL, 
	csrf_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (session_hash)
);

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE TABLE magic_links (
	id UUID NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	pending_identity_id UUID, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash), 
	FOREIGN KEY(pending_identity_id) REFERENCES pending_identities (id) ON DELETE CASCADE
);

CREATE INDEX ix_magic_links_email ON magic_links (email);

CREATE INDEX ix_magic_links_expires_at ON magic_links (expires_at);

CREATE TABLE workspace_memberships (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_workspace_member UNIQUE (user_id, workspace_id), 
	CONSTRAINT ck_membership_role CHECK (role IN ('owner', 'admin', 'member')), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);

CREATE INDEX ix_workspace_memberships_user_id ON workspace_memberships (user_id);

CREATE INDEX ix_workspace_memberships_workspace_id ON workspace_memberships (workspace_id);

CREATE TABLE workspace_role_permissions (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	manage_ai_agents BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_workspace_role_permission UNIQUE (workspace_id, role), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);

CREATE INDEX ix_workspace_role_permissions_workspace_id ON workspace_role_permissions (workspace_id);

CREATE TABLE agent_changes (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	agent_id UUID, 
	agent_name VARCHAR(100) NOT NULL, 
	action VARCHAR(60) NOT NULL, 
	summary VARCHAR(500) NOT NULL, 
	actor_id UUID, 
	revision INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_agent_changes_agent_id ON agent_changes (agent_id);

CREATE INDEX ix_agent_changes_workspace_id ON agent_changes (workspace_id);

CREATE TABLE agent_connections (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	source_id UUID NOT NULL, 
	target_id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_agent_connection UNIQUE (source_id, target_id), 
	CONSTRAINT ck_agent_connection_no_self CHECK (source_id <> target_id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_id) REFERENCES ai_agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(target_id) REFERENCES ai_agents (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_connections_source_id ON agent_connections (source_id);

CREATE INDEX ix_agent_connections_target_id ON agent_connections (target_id);

CREATE INDEX ix_agent_connections_workspace_id ON agent_connections (workspace_id);

CREATE TABLE agent_conversations (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	agent_id UUID, 
	user_id UUID NOT NULL, 
	agent_name VARCHAR(100) NOT NULL, 
	workflow_state JSON DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE SET NULL, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_conversations_agent_id ON agent_conversations (agent_id);

CREATE INDEX ix_agent_conversations_user_id ON agent_conversations (user_id);

CREATE INDEX ix_agent_conversations_workspace_id ON agent_conversations (workspace_id);

CREATE TABLE agent_versions (
	id UUID NOT NULL, 
	agent_id UUID NOT NULL, 
	number INTEGER NOT NULL, 
	section VARCHAR(40) NOT NULL, 
	snapshot JSON NOT NULL, 
	actor_id UUID, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_agent_version_number UNIQUE (agent_id, number), 
	FOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_agent_versions_agent_id ON agent_versions (agent_id);

CREATE TABLE agent_workflows (
	agent_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	nodes JSON NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (agent_id), 
	CONSTRAINT ck_workflow_revision CHECK (revision >= 1), 
	FOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE
);

CREATE TABLE agent_messages (
	id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	message_data JSON DEFAULT '{}' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE
);

CREATE INDEX ix_agent_messages_conversation_id ON agent_messages (conversation_id);
