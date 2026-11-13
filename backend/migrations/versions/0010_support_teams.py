"""Workspace support teams and configured alternative names."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('support_teams',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('workspace_id', sa.Uuid(), sa.ForeignKey('workspaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('alternative_names', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('revision >= 1', name='ck_support_team_revision'))
    op.create_index('ix_support_teams_workspace_id', 'support_teams', ['workspace_id'])


def downgrade():
    op.drop_table('support_teams')
