"""Workflow documents and tester state; one-time backfill for existing agents.

This migration is a frozen snapshot and does not import application services.
"""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def initial_nodes(agent_id, language):
    def identifier(key):
        return str(uuid.uuid5(uuid.UUID(str(agent_id)), "petrix-workflow:" + key))

    nodes = []
    for trigger, name in (("chat_started", "Chat started"), ("chat_closed", "Chat closed"),
            ("chat_timeout", "Chat timeout"), ("ai_timeout", "AI timeout"), ("fallback", "Fallback")):
        node = {"id": identifier(trigger), "kind": "trigger", "type": trigger,
            "name": name, "description": "", "enabled": trigger != "fallback", "config": {}, "children": []}
        if trigger == "chat_started":
            node["children"] = [{"id": identifier("greeting"), "kind": "action", "type": "send_message",
                "name": "Send message", "description": "", "enabled": True,
                "config": {"translations": {language: {"messages": ["Hi! I’m {_chatbot.name}. How can I assist you today?"], "quick_replies": []}}}, "children": []}]
        nodes.append(node)
    return nodes


def upgrade():
    op.create_table("agent_workflows",
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("ai_agents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_workflow_revision"))
    op.add_column("agent_conversations", sa.Column("workflow_state", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("agent_messages", sa.Column("message_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    connection = op.get_bind()
    agents = sa.table("ai_agents", sa.column("id", sa.Uuid()), sa.column("default_language", sa.String()))
    workflows = sa.table("agent_workflows", sa.column("agent_id", sa.Uuid()), sa.column("revision", sa.Integer()),
        sa.column("nodes", sa.JSON()), sa.column("updated_at", sa.DateTime(timezone=True)))
    for agent in connection.execute(sa.select(agents.c.id, agents.c.default_language)).mappings().all():
        connection.execute(workflows.insert().values(agent_id=agent["id"], revision=1,
            nodes=initial_nodes(agent["id"], agent["default_language"]), updated_at=sa.func.now()))


def downgrade():
    op.drop_column("agent_messages", "message_data")
    op.drop_column("agent_conversations", "workflow_state")
    op.drop_table("agent_workflows")
