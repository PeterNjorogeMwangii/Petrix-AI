"""Predefined workflow media and 20 MiB outgoing conversation attachments."""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("workflow_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 20971520", name="ck_workflow_asset_size"))
    op.create_index("ix_workflow_assets_agent_id", "workflow_assets", ["agent_id"])
    op.create_index("ix_workflow_assets_workspace_id", "workflow_assets", ["workspace_id"])
    op.drop_constraint("ck_agent_attachment_size", "agent_attachments", type_="check")
    op.create_check_constraint("ck_agent_attachment_size", "agent_attachments", "size_bytes > 0 AND size_bytes <= 20971520")


def downgrade():
    # PostgreSQL rejects this constraint if a sent file is larger than 10 MiB;
    # do not silently remove existing chat files to make a downgrade succeed.
    op.drop_constraint("ck_agent_attachment_size", "agent_attachments", type_="check")
    op.create_check_constraint("ck_agent_attachment_size", "agent_attachments", "size_bytes > 0 AND size_bytes <= 10485760")
    op.drop_table("workflow_assets")
