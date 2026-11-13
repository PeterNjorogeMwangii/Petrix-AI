"""Workspace custom fields and independent, durable conversation assignments."""
from alembic import op
import sqlalchemy as sa

revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('conversation_fields',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),nullable=False),
        sa.Column('key',sa.String(64),nullable=False),sa.Column('scope',sa.String(24),nullable=False),
        sa.Column('type',sa.String(32),nullable=False),sa.Column('name',sa.String(100),nullable=False),
        sa.Column('description',sa.Text(),nullable=False),sa.Column('options',sa.JSON(),nullable=False),
        sa.Column('default_value',sa.JSON(),nullable=True),sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('archived',sa.Boolean(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('workspace_id','key',name='uq_conversation_field_key'),
        sa.CheckConstraint("scope = 'conversation'",name='ck_conversation_field_scope'),
        sa.CheckConstraint("type IN ('text','number','toggle','dropdown','date','single_select_grouped')",name='ck_conversation_field_type'),
        sa.CheckConstraint('revision >= 1',name='ck_conversation_field_revision'))
    op.create_index('ix_conversation_fields_workspace_id','conversation_fields',['workspace_id'])
    op.add_column('agent_conversations',sa.Column('custom_fields',sa.JSON(),nullable=False,server_default='{}'))


def downgrade():
    op.drop_column('agent_conversations','custom_fields')
    op.drop_table('conversation_fields')
