"""Private Advisor history, durable turns and owner-scoped attachments. Frozen DDL."""
from alembic import op
revision="0025"
down_revision="0024"
branch_labels=None
depends_on=None

def upgrade():
    op.execute('CREATE TABLE advisor_conversations (\n\tid UUID NOT NULL, \n\tclient_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tteamspace_id UUID NOT NULL, \n\ttitle VARCHAR(160) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_advisor_conversation_client UNIQUE (workspace_id, user_id, client_id), \n\tCONSTRAINT ck_advisor_conversation_revision CHECK (revision>=1), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_advisor_conversations_teamspace_id ON advisor_conversations (teamspace_id)')
    op.execute('CREATE INDEX ix_advisor_conversations_updated_at ON advisor_conversations (updated_at)')
    op.execute('CREATE INDEX ix_advisor_conversations_user_id ON advisor_conversations (user_id)')
    op.execute('CREATE INDEX ix_advisor_conversations_workspace_id ON advisor_conversations (workspace_id)')
    op.execute('CREATE INDEX ix_advisor_history ON advisor_conversations (workspace_id, user_id, updated_at)')
    op.execute("CREATE TABLE advisor_messages (\n\tid UUID NOT NULL, \n\tconversation_id UUID NOT NULL, \n\tclient_request_id UUID NOT NULL, \n\trole VARCHAR(12) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tpayload_hash VARCHAR(64) NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tmode VARCHAR(24) NOT NULL, \n\tsources JSON NOT NULL, \n\tnavigation JSON NOT NULL, \n\tnotice VARCHAR(1000) NOT NULL, \n\tattempts INTEGER NOT NULL, \n\tworker_token UUID, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_advisor_message_request UNIQUE (conversation_id, client_request_id, role), \n\tCONSTRAINT ck_advisor_message CHECK (role IN ('user','assistant') AND status IN ('pending','running','completed','failed') AND attempts>=0), \n\tFOREIGN KEY(conversation_id) REFERENCES advisor_conversations (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_advisor_messages_conversation_id ON advisor_messages (conversation_id)')
    op.execute('CREATE INDEX ix_advisor_messages_created_at ON advisor_messages (created_at)')
    op.execute('CREATE INDEX ix_advisor_messages_order ON advisor_messages (conversation_id, created_at, id)')
    op.execute('CREATE INDEX ix_advisor_messages_status ON advisor_messages (status)')
    op.execute('CREATE TABLE advisor_attachments (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tmessage_id UUID, \n\tfilename VARCHAR(200) NOT NULL, \n\tmime_type VARCHAR(100) NOT NULL, \n\tsize INTEGER NOT NULL, \n\tdata BYTEA NOT NULL, \n\tpreview BYTEA, \n\textracted_text TEXT NOT NULL, \n\tnote VARCHAR(500) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_advisor_attachment_size CHECK (size>0 AND size<=10485760), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(message_id) REFERENCES advisor_messages (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_advisor_attachments_created_at ON advisor_attachments (created_at)')
    op.execute('CREATE INDEX ix_advisor_attachments_message_id ON advisor_attachments (message_id)')
    op.execute('CREATE INDEX ix_advisor_attachments_user_id ON advisor_attachments (user_id)')
    op.execute('CREATE INDEX ix_advisor_attachments_workspace_id ON advisor_attachments (workspace_id)')

def downgrade():
    op.drop_table('advisor_attachments')
    op.drop_table('advisor_messages')
    op.drop_table('advisor_conversations')
