"""Unified Inbox views, personal pins, attachments and Copilot source refresh. Frozen PostgreSQL DDL."""
from alembic import op
import sqlalchemy as sa
revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("CREATE TABLE inbox_views (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tteamspace_id UUID NOT NULL, \n\towner_id UUID NOT NULL, \n\tscope VARCHAR(12) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tfilters JSON NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_inbox_view CHECK (scope IN ('private','teamspace') AND revision>=1), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(teamspace_id) REFERENCES teamspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_inbox_views_teamspace_id ON inbox_views (teamspace_id)')
    op.execute('CREATE INDEX ix_inbox_views_workspace_id ON inbox_views (workspace_id)')
    op.execute("CREATE TABLE inbox_preferences (\n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tpinned JSON NOT NULL, \n\tsort VARCHAR(12) NOT NULL, \n\tdirection VARCHAR(4) NOT NULL, \n\trevision INTEGER NOT NULL, \n\tPRIMARY KEY (workspace_id, user_id), \n\tCONSTRAINT ck_inbox_preference CHECK (sort IN ('','created','latest','wait','handoff') AND direction IN ('asc','desc') AND revision>=1), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)")
    op.execute("CREATE TABLE inbox_attachments (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tkind VARCHAR(8) NOT NULL, \n\trecord_id UUID NOT NULL, \n\tactor_id UUID, \n\tfile_name VARCHAR(255) NOT NULL, \n\tcontent_type VARCHAR(120) NOT NULL, \n\tsize_bytes INTEGER NOT NULL, \n\tdata BYTEA NOT NULL, \n\tused BOOLEAN NOT NULL, \n\tprivate BOOLEAN NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_inbox_attachment CHECK (kind IN ('chats','tickets') AND size_bytes>0 AND size_bytes<=10485760), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL\n)")
    op.execute('CREATE INDEX ix_inbox_attachments_record_id ON inbox_attachments (record_id)')
    op.execute('CREATE INDEX ix_inbox_attachments_workspace_id ON inbox_attachments (workspace_id)')
    op.add_column('copilot_knowledge_sources',sa.Column('status',sa.String(12),nullable=False,server_default='success'))
    op.add_column('copilot_knowledge_sources',sa.Column('error',sa.String(300),nullable=False,server_default=''))
    op.add_column('copilot_knowledge_sources',sa.Column('options',sa.JSON(),nullable=False,server_default='{}'))
    op.add_column('copilot_knowledge_sources',sa.Column('next_refresh_at',sa.DateTime(timezone=True),nullable=True))

def downgrade():
    op.drop_column('copilot_knowledge_sources','next_refresh_at')
    op.drop_column('copilot_knowledge_sources','options')
    op.drop_column('copilot_knowledge_sources','error')
    op.drop_column('copilot_knowledge_sources','status')
    op.drop_table('inbox_attachments')
    op.drop_table('inbox_preferences')
    op.drop_table('inbox_views')
