"""Workspace-owned encrypted PostgreSQL connectors; preserve existing workflows."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("database_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workspace_id", sa.Uuid(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database", sa.String(128), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("ssl_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_error", sa.String(50)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "name", name="uq_database_connection_name"),
        sa.CheckConstraint("revision >= 1", name="ck_database_connection_revision"),
        sa.CheckConstraint("port >= 1 AND port <= 65535", name="ck_database_connection_port"))
    op.create_index("ix_database_connections_workspace_id", "database_connections", ["workspace_id"])


def downgrade():
    op.drop_index("ix_database_connections_workspace_id", table_name="database_connections")
    op.drop_table("database_connections")
