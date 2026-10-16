"""Assign permanent per-agent numbers to existing triggers and actions.

Frozen migration: retain root-list numbers, then number descendants in tree
order. Configuration, UUIDs, revisions, and conversation state are preserved.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def descendants(nodes):
    for node in nodes:
        for child in node["children"]:
            yield child
            yield from descendants([child])


def upgrade():
    op.add_column("agent_workflows", sa.Column("next_node_number", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_workflow_next_node_number", "agent_workflows", "next_node_number >= 1")
    connection = op.get_bind()
    workflows = sa.table("agent_workflows", sa.column("agent_id", sa.Uuid()),
        sa.column("nodes", sa.JSON()), sa.column("next_node_number", sa.Integer()))
    for document in connection.execute(sa.select(workflows.c.agent_id, workflows.c.nodes)).mappings().all():
        nodes = document["nodes"]
        ordered = list(nodes) + list(descendants(nodes))
        for number, node in enumerate(ordered, start=1):
            node["number"] = number
        connection.execute(workflows.update().where(workflows.c.agent_id == document["agent_id"])
            .values(nodes=nodes, next_node_number=len(ordered) + 1))
    op.alter_column("agent_workflows", "next_node_number", server_default=None)


def downgrade():
    connection = op.get_bind()
    workflows = sa.table("agent_workflows", sa.column("agent_id", sa.Uuid()), sa.column("nodes", sa.JSON()))
    for document in connection.execute(sa.select(workflows.c.agent_id, workflows.c.nodes)).mappings().all():
        nodes = document["nodes"]
        for node in list(nodes) + list(descendants(nodes)):
            node.pop("number", None)
        connection.execute(workflows.update().where(workflows.c.agent_id == document["agent_id"]).values(nodes=nodes))
    op.drop_constraint("ck_workflow_next_node_number", "agent_workflows", type_="check")
    op.drop_column("agent_workflows", "next_node_number")
