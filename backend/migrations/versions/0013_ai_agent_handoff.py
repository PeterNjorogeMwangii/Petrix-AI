"""Authenticated AI agent connections and durable, idempotent proxy sessions."""
from alembic import op
import sqlalchemy as sa
revision='0013'
down_revision='0012'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('ai_network_credentials',
        sa.Column('agent_id',sa.Uuid(),sa.ForeignKey('ai_agents.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('actor_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('secret_hash',sa.String(64),nullable=False),sa.Column('secret_ciphertext',sa.Text(),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),
        sa.CheckConstraint('revision >= 1',name='ck_ai_network_credential_revision'))
    op.create_table('ai_transfer_connections',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),nullable=False),
        sa.Column('source_id',sa.Uuid(),sa.ForeignKey('ai_agents.id',ondelete='CASCADE'),nullable=False),
        sa.Column('target_id',sa.Uuid(),nullable=False),sa.Column('target_name',sa.String(100),nullable=False),sa.Column('domain',sa.String(250),nullable=False),
        sa.Column('secret_ciphertext',sa.Text(),nullable=False),sa.Column('verified_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('source_id','target_id','domain',name='uq_ai_transfer_target'))
    op.create_index('ix_ai_transfer_connections_workspace_id','ai_transfer_connections',['workspace_id'])
    op.create_index('ix_ai_transfer_connections_source_id','ai_transfer_connections',['source_id'])
    op.create_table('ai_inbound_sessions',
        sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('agent_id',sa.Uuid(),sa.ForeignKey('ai_agents.id',ondelete='CASCADE'),nullable=False),
        sa.Column('conversation_id',sa.Uuid(),sa.ForeignKey('agent_conversations.id',ondelete='CASCADE'),nullable=False,unique=True),
        sa.Column('transfer_id',sa.Uuid(),nullable=False),sa.Column('credential_revision',sa.Integer(),nullable=False),
        sa.Column('token_hash',sa.String(64),nullable=False),sa.Column('token_ciphertext',sa.Text(),nullable=False),sa.Column('trail',sa.JSON(),nullable=False),
        sa.Column('start_digest',sa.String(64),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('initial_error',sa.String(300),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('agent_id','transfer_id',name='uq_ai_inbound_transfer'),sa.CheckConstraint("status IN ('running','ready')",name='ck_ai_inbound_status'))
    op.create_index('ix_ai_inbound_sessions_agent_id','ai_inbound_sessions',['agent_id'])
    op.create_table('ai_inbound_requests',
        sa.Column('session_id',sa.Uuid(),sa.ForeignKey('ai_inbound_sessions.id',ondelete='CASCADE'),primary_key=True),sa.Column('request_id',sa.Uuid(),primary_key=True),
        sa.Column('digest',sa.String(64),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('response',sa.JSON(),nullable=False),
        sa.CheckConstraint("status IN ('running','completed')",name='ck_ai_inbound_request_status'))
    op.create_table('ai_outgoing_sessions',
        sa.Column('conversation_id',sa.Uuid(),sa.ForeignKey('agent_conversations.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('connection_id',sa.Uuid(),sa.ForeignKey('ai_transfer_connections.id',ondelete='SET NULL')),
        sa.Column('transfer_id',sa.Uuid(),nullable=False,unique=True),sa.Column('target_id',sa.Uuid(),nullable=False),
        sa.Column('target_name',sa.String(100),nullable=False),sa.Column('domain',sa.String(250),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('remote_session_id',sa.Uuid()),sa.Column('token_ciphertext',sa.Text(),nullable=False),sa.Column('start_payload',sa.JSON(),nullable=False),
        sa.Column('pending',sa.JSON(),nullable=False),sa.Column('last_result',sa.JSON(),nullable=False),sa.Column('error',sa.String(300),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),sa.CheckConstraint("status IN ('starting','active','failed')",name='ck_ai_outgoing_status'))


def downgrade():
    for name in ['ai_outgoing_sessions','ai_inbound_requests','ai_inbound_sessions','ai_transfer_connections','ai_network_credentials']:op.drop_table(name)
