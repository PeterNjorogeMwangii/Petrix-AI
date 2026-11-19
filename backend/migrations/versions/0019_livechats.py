"""Livechat descriptions, display status and private notes. Frozen PostgreSQL DDL."""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('agent_conversations', sa.Column('live_description', sa.String(4000), server_default='', nullable=False))
    op.add_column('agent_conversations', sa.Column('live_display_status', sa.String(16), server_default='', nullable=False))
    op.create_table('livechat_notes',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('conversation_id', sa.Uuid(), sa.ForeignKey('agent_conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('author_name', sa.String(200), nullable=False),
        sa.Column('content', sa.String(4000), nullable=False),
        sa.Column('color', sa.String(7), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('revision >= 1', name='ck_livechat_note_revision'))
    op.create_index('ix_livechat_notes_conversation_id', 'livechat_notes', ['conversation_id'])

def downgrade():
    op.drop_table('livechat_notes')
    op.drop_column('agent_conversations', 'live_display_status')
    op.drop_column('agent_conversations', 'live_description')
