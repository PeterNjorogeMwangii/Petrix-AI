"""Support profiles, team members, assignment policy and durable live-chat ownership."""
from alembic import op
import sqlalchemy as sa

revision='0011'
down_revision='0010'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('auth_sessions',sa.Column('active_workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='SET NULL'),nullable=True))
    op.add_column('support_teams',sa.Column('member_ids',sa.JSON(),nullable=False,server_default='[]'))
    op.create_table('support_agents',
        sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('languages',sa.JSON(),nullable=False),sa.Column('available',sa.Boolean(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),sa.Column('last_seen_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('last_assigned_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('presence_session_id',sa.Uuid(),sa.ForeignKey('auth_sessions.id',ondelete='SET NULL'),nullable=True),
        sa.CheckConstraint('revision >= 1',name='ck_support_agent_revision'))
    op.create_table('chat_assignments',
        sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('mode',sa.String(24),nullable=False),sa.Column('max_active_chats',sa.Integer(),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.CheckConstraint("mode IN ('manual','round_robin','least_busy')",name='ck_chat_assignment_mode'),
        sa.CheckConstraint('max_active_chats BETWEEN 1 AND 100 AND revision >= 1',name='ck_chat_assignment_limits'))
    op.execute("INSERT INTO chat_assignments (workspace_id,mode,max_active_chats,revision) SELECT id,'manual',5,1 FROM workspaces")
    op.add_column('agent_conversations',sa.Column('live_status',sa.String(16),nullable=False,server_default='none'))
    op.add_column('agent_conversations',sa.Column('live_assigned_user_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True))
    op.add_column('agent_conversations',sa.Column('live_context',sa.JSON(),nullable=False,server_default='{}'))
    op.add_column('agent_conversations',sa.Column('live_revision',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('agent_conversations',sa.Column('live_updated_at',sa.DateTime(timezone=True),nullable=True))
    op.create_check_constraint('ck_conversation_live_status','agent_conversations',"live_status IN ('none','pending','active','closed') AND live_revision >= 1")
    op.create_index('ix_conversation_live_queue','agent_conversations',['workspace_id','live_status','live_updated_at'])


def downgrade():
    op.drop_index('ix_conversation_live_queue',table_name='agent_conversations')
    op.drop_constraint('ck_conversation_live_status','agent_conversations',type_='check')
    for name in ['live_updated_at','live_revision','live_context','live_assigned_user_id','live_status']:op.drop_column('agent_conversations',name)
    op.drop_table('chat_assignments');op.drop_table('support_agents')
    op.drop_column('support_teams','member_ids');op.drop_column('auth_sessions','active_workspace_id')
