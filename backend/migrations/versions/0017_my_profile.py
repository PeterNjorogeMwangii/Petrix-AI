"""Personal account preferences, aliases and frozen reply personas. Frozen DDL."""
from alembic import op
revision='0017'
down_revision='0016'
branch_labels=None
depends_on=None
DDL=(
    "ALTER TABLE agent_messages ADD COLUMN author_id UUID REFERENCES users(id) ON DELETE SET NULL;",
    """CREATE TABLE profile_assets (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	data BYTEA NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);""",
    """CREATE INDEX ix_profile_assets_user_id ON profile_assets (user_id);""",
    """CREATE TABLE user_profiles (
	user_id UUID NOT NULL, 
	first_name VARCHAR(100) NOT NULL, 
	last_name VARCHAR(100) NOT NULL, 
	avatar_asset_id UUID, 
	language VARCHAR(16) NOT NULL, 
	presence VARCHAR(10) NOT NULL, 
	theme VARCHAR(10) NOT NULL, 
	notifications JSON NOT NULL, 
	multichat BOOLEAN NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (user_id), 
	CONSTRAINT ck_user_profile_preferences CHECK (presence IN ('online','away','offline') AND theme IN ('system','light','dark') AND revision>=1), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(avatar_asset_id) REFERENCES profile_assets (id) ON DELETE SET NULL
);""",
    """CREATE TABLE user_aliases (
	id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	name_key VARCHAR(200) NOT NULL, 
	avatar_asset_id UUID, 
	is_default BOOLEAN NOT NULL, 
	signature_name VARCHAR(200) NOT NULL, 
	signature_content VARCHAR(4000) NOT NULL, 
	archived BOOLEAN NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(avatar_asset_id) REFERENCES profile_assets (id) ON DELETE SET NULL
);""",
    """CREATE INDEX ix_user_aliases_user_id ON user_aliases (user_id);""",
    """CREATE UNIQUE INDEX uq_user_alias_name ON user_aliases (user_id, name_key) WHERE NOT archived;""",
    """CREATE UNIQUE INDEX uq_user_default_alias ON user_aliases (user_id) WHERE is_default AND NOT archived;""",
    """CREATE TABLE conversation_personas (
	workspace_id UUID NOT NULL, 
	kind VARCHAR(8) NOT NULL, 
	record_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	alias_id UUID, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (workspace_id, kind, record_id, user_id), 
	CONSTRAINT ck_conversation_persona CHECK (kind IN ('chats','tickets') AND revision>=1), 
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(alias_id) REFERENCES user_aliases (id) ON DELETE SET NULL
);""",
    """ALTER TABLE ticket_emails ADD COLUMN kind VARCHAR(8) NOT NULL DEFAULT 'initial';""",
    """ALTER TABLE ticket_emails ADD COLUMN actor_id UUID REFERENCES users(id) ON DELETE SET NULL;""",
    """ALTER TABLE ticket_emails ADD COLUMN client_request_id UUID;""",
    """ALTER TABLE ticket_emails ADD COLUMN persona JSON NOT NULL DEFAULT '{}';""",
    """ALTER TABLE ticket_emails DROP CONSTRAINT uq_ticket_initial_recipient;""",
    """CREATE UNIQUE INDEX uq_ticket_initial_recipient ON ticket_emails(ticket_id,recipient_type,recipient) WHERE kind='initial';""",
    """ALTER TABLE ticket_emails ADD CONSTRAINT uq_ticket_reply_request UNIQUE(ticket_id,client_request_id);""",
    """ALTER TABLE ticket_emails ADD CONSTRAINT ck_ticket_email_kind CHECK (kind IN ('initial','reply'));""",
    """INSERT INTO user_profiles(user_id,first_name,last_name,language,presence,theme,notifications,multichat,revision)
SELECT id,left(split_part(coalesce(full_name,''),' ',1),100),left(ltrim(substr(coalesce(full_name,''),length(split_part(coalesce(full_name,''),' ',1))+1)),100),'en','online','system','{"browser": false,"platform": true,"assigned_you":"byte","assigned_team":"none","transferred_you":"byte","message_open":"none","message_other":"byte"}',false,1 FROM users;""",
    """INSERT INTO conversation_personas(workspace_id,kind,record_id,user_id,alias_id,revision)
SELECT c.workspace_id,'chats',c.id,c.live_assigned_user_id,NULL,1 FROM agent_conversations c
WHERE c.live_assigned_user_id IS NOT NULL AND EXISTS(SELECT 1 FROM agent_messages m WHERE m.conversation_id=c.id AND m.message_data->>'source'='live_agent');""",
)

def upgrade():
    for statement in DDL:op.execute(statement)

def downgrade():
    op.drop_column("agent_messages","author_id")
    op.execute("DELETE FROM ticket_emails WHERE kind='reply'")
    op.execute('DROP INDEX uq_ticket_initial_recipient')
    op.execute('ALTER TABLE ticket_emails ADD CONSTRAINT uq_ticket_initial_recipient UNIQUE(ticket_id,recipient_type,recipient)')
    op.drop_constraint('uq_ticket_reply_request','ticket_emails',type_='unique')
    op.drop_constraint('ck_ticket_email_kind','ticket_emails',type_='check')
    for column in ['kind','actor_id','client_request_id','persona']:op.drop_column('ticket_emails',column)
    for table in ['conversation_personas','user_aliases','user_profiles','profile_assets']:op.drop_table(table)
