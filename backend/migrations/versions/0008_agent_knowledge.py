"""Agent-scoped text knowledge sources for workflow LLM processing."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("agent_knowledge",
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_agent_knowledge_revision"))


def downgrade():
    op.drop_table("agent_knowledge")
