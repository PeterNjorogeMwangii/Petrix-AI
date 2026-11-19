"""Measured Analytics activity, assignment/status history and workspace timezone. Frozen DDL."""
from alembic import op
import sqlalchemy as sa
revision='0023'
down_revision='0022'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('workspace_settings',sa.Column('timezone',sa.String(64),nullable=False,server_default='Africa/Nairobi'))
    op.add_column('workspace_settings',sa.Column('analytics_started_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.execute("CREATE TABLE analytics_activity (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\tstate VARCHAR(12) NOT NULL, \n\tcapacity INTEGER NOT NULL, \n\tcapacities JSON DEFAULT '{}' NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tlast_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlease_until TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_analytics_activity CHECK (state IN ('online','away','offline') AND capacity>=0 AND lease_until>=started_at), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(session_id) REFERENCES auth_sessions (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_analytics_activity_session ON analytics_activity (workspace_id, session_id, ended_at, started_at)')
    op.execute('CREATE INDEX ix_analytics_activity_user_id ON analytics_activity (user_id)')
    op.execute('CREATE INDEX ix_analytics_activity_window ON analytics_activity (workspace_id, started_at, lease_until)')
    op.execute('CREATE INDEX ix_analytics_activity_workspace_id ON analytics_activity (workspace_id)')
    op.execute("CREATE TABLE analytics_record_events (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tconversation_id UUID, \n\tticket_id UUID, \n\tevent VARCHAR(16) NOT NULL, \n\tassigned_user_id UUID, \n\tteam_ids JSON NOT NULL, \n\tstatus VARCHAR(16) NOT NULL, \n\tdetail JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_analytics_record_event CHECK (event IN ('assignment','status','feedback') AND ((conversation_id IS NOT NULL AND ticket_id IS NULL) OR (ticket_id IS NOT NULL AND conversation_id IS NULL))), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE, \n\tFOREIGN KEY(ticket_id) REFERENCES support_tickets (id) ON DELETE CASCADE, \n\tFOREIGN KEY(assigned_user_id) REFERENCES users (id) ON DELETE SET NULL\n)")
    op.execute('CREATE INDEX ix_analytics_record_events_conversation_id ON analytics_record_events (conversation_id)')
    op.execute('CREATE INDEX ix_analytics_record_events_created_at ON analytics_record_events (created_at)')
    op.execute('CREATE INDEX ix_analytics_record_events_ticket_id ON analytics_record_events (ticket_id)')
    op.execute('CREATE INDEX ix_analytics_record_events_workspace_id ON analytics_record_events (workspace_id)')

def downgrade():
    op.drop_table('analytics_record_events')
    op.drop_table('analytics_activity')
    op.drop_column('workspace_settings','analytics_started_at')
    op.drop_column('workspace_settings','timezone')
