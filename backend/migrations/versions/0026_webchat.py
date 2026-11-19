"""Published Webchat channels, visitor capabilities, receipts and voice input settings. Frozen DDL."""
from alembic import op
revision="0026"
down_revision="0025"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("ALTER TABLE ai_agents ADD COLUMN speech_to_text_enabled BOOLEAN NOT NULL DEFAULT false, ADD COLUMN speech_languages JSON NOT NULL DEFAULT '[]', ADD COLUMN include_audio_transcript BOOLEAN NOT NULL DEFAULT true")
    op.execute('CREATE TABLE webchat_assets (\n\tid UUID NOT NULL, \n\tagent_id UUID NOT NULL, \n\tdata BYTEA NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_webchat_assets_agent_id ON webchat_assets (agent_id)')
    op.execute('CREATE TABLE webchat_channels (\n\tid UUID NOT NULL, \n\tclient_id UUID NOT NULL, \n\tagent_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tactor_id UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tconfig JSON NOT NULL, \n\truntime JSON NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_webchat_channel_client UNIQUE (agent_id, client_id), \n\tCONSTRAINT ck_webchat_channel_revision CHECK (revision>=1), \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE, \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_webchat_channels_agent_id ON webchat_channels (agent_id)')
    op.execute('CREATE INDEX ix_webchat_channels_workspace_id ON webchat_channels (workspace_id)')
    op.execute('CREATE TABLE webchat_previews (\n\ttoken_hash VARCHAR(64) NOT NULL, \n\tchannel_id UUID NOT NULL, \n\tconfig JSON NOT NULL, \n\truntime JSON NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (token_hash), \n\tFOREIGN KEY(channel_id) REFERENCES webchat_channels (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_webchat_previews_channel_id ON webchat_previews (channel_id)')
    op.execute('CREATE INDEX ix_webchat_previews_expires_at ON webchat_previews (expires_at)')
    op.execute('CREATE TABLE webchat_sessions (\n\tid UUID NOT NULL, \n\tchannel_id UUID NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\tpreview_hash VARCHAR(64) NOT NULL, \n\thost_origin VARCHAR(300) NOT NULL, \n\tperson_id UUID, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(channel_id) REFERENCES webchat_channels (id) ON DELETE CASCADE, \n\tUNIQUE (token_hash), \n\tFOREIGN KEY(person_id) REFERENCES support_people (id) ON DELETE SET NULL\n)')
    op.execute('CREATE INDEX ix_webchat_sessions_channel_id ON webchat_sessions (channel_id)')
    op.execute('CREATE INDEX ix_webchat_sessions_expires_at ON webchat_sessions (expires_at)')
    op.execute('CREATE TABLE webchat_conversations (\n\tconversation_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\truntime JSON NOT NULL, \n\tstarted BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (conversation_id), \n\tFOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(session_id) REFERENCES webchat_sessions (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_webchat_conversations_session_id ON webchat_conversations (session_id)')
    op.execute("CREATE TABLE webchat_requests (\n\tsession_id UUID NOT NULL, \n\trequest_id UUID NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tconversation_id UUID, \n\tstatus VARCHAR(16) NOT NULL, \n\terror VARCHAR(300) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (session_id, request_id), \n\tCONSTRAINT ck_webchat_request_status CHECK (status IN ('running','completed','failed')), \n\tFOREIGN KEY(session_id) REFERENCES webchat_sessions (id) ON DELETE CASCADE, \n\tFOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE\n)")

def downgrade():
    op.drop_table('webchat_requests')
    op.drop_table('webchat_conversations')
    op.drop_table('webchat_sessions')
    op.drop_table('webchat_previews')
    op.drop_table('webchat_assets')
    op.drop_table('webchat_channels')
    op.drop_column('ai_agents','include_audio_transcript')
    op.drop_column('ai_agents','speech_languages')
    op.drop_column('ai_agents','speech_to_text_enabled')
