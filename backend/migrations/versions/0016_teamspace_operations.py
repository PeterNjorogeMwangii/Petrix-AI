"""Operational Teamspace settings and support records; frozen PostgreSQL DDL."""
from alembic import op
revision="0016"
down_revision="0015"
branch_labels=None
depends_on=None
DDL = (
    """CREATE TABLE teamspace_operations (
	teamspace_id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	revision INTEGER NOT NULL, 
	analysis JSON NOT NULL, 
	copilot JSON NOT NULL, 
	assignment JSON NOT NULL, 
	enforcement JSON NOT NULL, 
	overdue JSON NOT NULL, 
	analytics JSON NOT NULL, 
	profile_iframe JSON NOT NULL, 
	PRIMARY KEY (teamspace_id), 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_teamspace_operations_workspace_id ON teamspace_operations (workspace_id);""",
    """CREATE TABLE support_people (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID, 
	team_id UUID, 
	user_id UUID, 
	identity_key VARCHAR(200) NOT NULL, 
	external_id VARCHAR(200), 
	name VARCHAR(200) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	phone VARCHAR(50) NOT NULL, 
	location VARCHAR(200) NOT NULL, 
	country VARCHAR(2) NOT NULL, 
	custom_fields JSON NOT NULL, 
	offences INTEGER NOT NULL, 
	ban_permanent BOOLEAN NOT NULL, 
	banned_until TIMESTAMP WITH TIME ZONE, 
	ban_reason VARCHAR(200) NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_support_person_identity UNIQUE (workspace_id, identity_key), 
	CONSTRAINT uq_support_person_external_id UNIQUE (workspace_id, external_id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE RESTRICT, 
	FOREIGN KEY(team_id) REFERENCES support_teams (id) ON DELETE SET NULL, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
);""",
    """CREATE INDEX ix_support_people_workspace_id ON support_people (workspace_id);""",
    """CREATE TABLE support_ban_rules (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID NOT NULL, 
	offences INTEGER NOT NULL, 
	duration INTEGER NOT NULL, 
	unit VARCHAR(12) NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ban_rule_offences UNIQUE (teamspace_id, offences), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_support_ban_rules_teamspace_id ON support_ban_rules (teamspace_id);""",
    """CREATE INDEX ix_support_ban_rules_workspace_id ON support_ban_rules (workspace_id);""",
    """CREATE TABLE support_ban_reasons (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	name_key VARCHAR(400) NOT NULL, 
	description VARCHAR(2000) NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_ban_reason_name UNIQUE (teamspace_id, name_key), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_support_ban_reasons_teamspace_id ON support_ban_reasons (teamspace_id);""",
    """CREATE INDEX ix_support_ban_reasons_workspace_id ON support_ban_reasons (workspace_id);""",
    """CREATE TABLE person_ban_events (
	id UUID NOT NULL, 
	person_id UUID NOT NULL, 
	actor_id UUID, 
	reason VARCHAR(200) NOT NULL, 
	permanent BOOLEAN NOT NULL, 
	until TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(person_id) REFERENCES support_people (id) ON DELETE CASCADE, 
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);""",
    """CREATE INDEX ix_person_ban_events_person_id ON person_ban_events (person_id);""",
    """CREATE TABLE support_canned_replies (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	name_key VARCHAR(200) NOT NULL, 
	language VARCHAR(16) NOT NULL, 
	content VARCHAR(4000) NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_canned_reply_name UNIQUE (teamspace_id, name_key), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_support_canned_replies_teamspace_id ON support_canned_replies (teamspace_id);""",
    """CREATE INDEX ix_support_canned_replies_workspace_id ON support_canned_replies (workspace_id);""",
    """CREATE TABLE support_record_labels (
	workspace_id UUID NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	record_id UUID NOT NULL, 
	tag_ids JSON NOT NULL, 
	group_ids JSON NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (workspace_id, kind, record_id), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE
);""",
    """CREATE TABLE support_tag_group_memberships (
	tag_id UUID NOT NULL, 
	group_id UUID NOT NULL, 
	PRIMARY KEY (tag_id, group_id), 
	FOREIGN KEY(tag_id) REFERENCES ticket_tags (id) ON DELETE CASCADE, 
	FOREIGN KEY(group_id) REFERENCES ticket_tag_groups (id) ON DELETE CASCADE
);""",
    """CREATE TABLE copilot_knowledge_sources (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	name_key VARCHAR(400) NOT NULL, 
	kind VARCHAR(12) NOT NULL, 
	url VARCHAR(2048) NOT NULL, 
	content TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_copilot_source_name UNIQUE (teamspace_id, name_key), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_copilot_knowledge_sources_teamspace_id ON copilot_knowledge_sources (teamspace_id);""",
    """CREATE INDEX ix_copilot_knowledge_sources_workspace_id ON copilot_knowledge_sources (workspace_id);""",
    """CREATE TABLE support_jobs (
	id UUID NOT NULL, 
	workspace_id UUID NOT NULL, 
	teamspace_id UUID NOT NULL, 
	actor_id UUID, 
	actor_team_ids JSON NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	target_kind VARCHAR(16) NOT NULL, 
	target_id UUID, 
	request_key VARCHAR(200) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	payload JSON NOT NULL, 
	output JSON NOT NULL, 
	file_data BYTEA, 
	error VARCHAR(300) NOT NULL, 
	lease_id UUID, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_support_job_request UNIQUE (workspace_id, request_key), 
	CONSTRAINT ck_support_job_status CHECK (status IN ('pending','running','completed','failed')), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL
);""",
    """CREATE INDEX ix_support_jobs_created_at ON support_jobs (created_at);""",
    """CREATE INDEX ix_support_jobs_status ON support_jobs (status);""",
    """CREATE INDEX ix_support_jobs_teamspace_id ON support_jobs (teamspace_id);""",
    """CREATE INDEX ix_support_jobs_workspace_id ON support_jobs (workspace_id);""",
    """ALTER TABLE agent_conversations ADD COLUMN person_id UUID REFERENCES support_people(id) ON DELETE SET NULL;""",
    """CREATE INDEX ix_agent_conversations_person_id ON agent_conversations(person_id);""",
    """ALTER TABLE support_tickets ADD COLUMN person_id UUID REFERENCES support_people(id) ON DELETE SET NULL;""",
    """CREATE INDEX ix_support_tickets_person_id ON support_tickets(person_id);""",
    """ALTER TABLE support_tickets ADD COLUMN overdue_at TIMESTAMP WITH TIME ZONE;""",
    """ALTER TABLE support_tickets ADD COLUMN audience_team_ids JSON NOT NULL DEFAULT '[]';""",
    """ALTER TABLE support_agents ADD COLUMN online_since TIMESTAMP WITH TIME ZONE;""",
    """ALTER TABLE conversation_fields DROP CONSTRAINT ck_conversation_field_scope;""",
    """ALTER TABLE conversation_fields ADD CONSTRAINT ck_conversation_field_scope CHECK (scope IN ('conversation','person'));""",
    """ALTER TABLE chat_assignments DROP CONSTRAINT ck_chat_assignment_mode;""",
    """ALTER TABLE chat_assignments ADD CONSTRAINT ck_chat_assignment_mode CHECK (mode IN ('manual','round_robin','least_busy','balanced','random','balanced_round_robin'));""",
    """ALTER TABLE support_tickets DROP CONSTRAINT ck_support_ticket_status;""",
    """ALTER TABLE support_tickets ADD CONSTRAINT ck_support_ticket_status CHECK (status IN ('open','in_progress','resolved','closed','overdue'));""",
    """INSERT INTO support_people(id,workspace_id,teamspace_id,user_id,identity_key,name,email,phone,location,country,custom_fields,offences,ban_permanent,ban_reason,revision,created_at,updated_at) SELECT gen_random_uuid(),c.workspace_id,min(a.teamspace_id::text)::uuid,c.user_id,'account:'||c.user_id::text,coalesce(u.full_name,''),u.email,'','','','{}',0,false,'',1,min(c.created_at),now() FROM agent_conversations c JOIN users u ON u.id=c.user_id LEFT JOIN ai_agents a ON a.id=c.agent_id WHERE NOT EXISTS(SELECT 1 FROM ai_inbound_sessions i WHERE i.conversation_id=c.id) GROUP BY c.workspace_id,c.user_id,u.full_name,u.email;""",
    """UPDATE agent_conversations c SET person_id=p.id FROM support_people p WHERE p.workspace_id=c.workspace_id AND p.identity_key='account:'||c.user_id::text AND NOT EXISTS(SELECT 1 FROM ai_inbound_sessions i WHERE i.conversation_id=c.id);""",
    """UPDATE support_tickets t SET person_id=c.person_id FROM agent_conversations c WHERE c.id=t.conversation_id;""",
    """INSERT INTO support_tag_group_memberships(tag_id,group_id) SELECT id,group_id FROM ticket_tags WHERE group_id IS NOT NULL;""",
)

def upgrade():
    for statement in DDL:op.execute(statement)

def downgrade():
    op.execute("UPDATE support_tickets SET status='open' WHERE status='overdue'")
    op.execute("UPDATE chat_assignments SET mode='least_busy' WHERE mode IN ('balanced','random','balanced_round_robin')")
    op.execute("DELETE FROM conversation_fields WHERE scope='person'")
    op.execute("ALTER TABLE conversation_fields DROP CONSTRAINT ck_conversation_field_scope")
    op.execute("ALTER TABLE conversation_fields ADD CONSTRAINT ck_conversation_field_scope CHECK (scope='conversation')")
    op.execute("ALTER TABLE chat_assignments DROP CONSTRAINT ck_chat_assignment_mode")
    op.execute("ALTER TABLE chat_assignments ADD CONSTRAINT ck_chat_assignment_mode CHECK (mode IN ('manual','round_robin','least_busy'))")
    op.execute("ALTER TABLE support_tickets DROP CONSTRAINT ck_support_ticket_status")
    op.execute("ALTER TABLE support_tickets ADD CONSTRAINT ck_support_ticket_status CHECK (status IN ('open','in_progress','resolved','closed'))")
    op.drop_column("support_agents","online_since")
    op.drop_column("support_tickets","audience_team_ids")
    op.drop_column("support_tickets","overdue_at")
    op.drop_column("support_tickets","person_id")
    op.drop_column("agent_conversations","person_id")
    op.drop_table("support_jobs")
    op.drop_table("copilot_knowledge_sources")
    op.drop_table("support_tag_group_memberships")
    op.drop_table("support_record_labels")
    op.drop_table("support_canned_replies")
    op.drop_table("person_ban_events")
    op.drop_table("support_ban_reasons")
    op.drop_table("support_ban_rules")
    op.drop_table("support_people")
    op.drop_table("teamspace_operations")
