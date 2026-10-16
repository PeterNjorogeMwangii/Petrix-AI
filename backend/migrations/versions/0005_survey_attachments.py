"""Persist survey file answers alongside their conversation, atomically."""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_agent_attachment_size"))
    op.create_index("ix_agent_attachments_conversation_id", "agent_attachments", ["conversation_id"])


def downgrade():
    op.drop_index("ix_agent_attachments_conversation_id", table_name="agent_attachments")
    op.drop_table("agent_attachments")
