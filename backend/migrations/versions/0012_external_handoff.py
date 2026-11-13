"""Verified external integrations, unique provider chat links and durable transfer state."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('external_integrations',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('workspace_id', sa.Uuid(), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False), sa.Column('credentials_ciphertext', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False), sa.Column('verified_at', sa.DateTime(timezone=True)),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.UniqueConstraint('workspace_id', 'platform', name='uq_external_workspace_platform'),
        sa.CheckConstraint("platform IN ('zendesk','bitrix24') AND revision >= 1", name='ck_external_integration'))
    op.create_table('external_conversations',
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('agent_conversations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('workspace_id', sa.Uuid(), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False), sa.Column('external_id', sa.String(100), nullable=False),
        sa.Column('account_key', sa.String(64), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('error', sa.String(64), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('attempt_id', sa.Uuid()), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('workspace_id','platform','account_key','external_id',name='uq_external_provider_conversation'),
        sa.CheckConstraint("platform IN ('zendesk','bitrix24') AND status IN ('linked','sending','transferred','failed','uncertain') AND revision >= 1", name='ck_external_conversation'),
        sa.Index('ix_external_workspace_status','workspace_id','status'))


def downgrade():
    op.drop_table('external_conversations')
    op.drop_table('external_integrations')
