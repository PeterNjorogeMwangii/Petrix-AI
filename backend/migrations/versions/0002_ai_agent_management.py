"""AI agent management schema and one-time default-agent backfill. Frozen snapshot."""
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workspaces", sa.Column("plan_name", sa.String(60), nullable=False, server_default="Starter"))
    op.add_column("workspaces", sa.Column("agent_limit", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("workspaces", sa.Column("language_limit", sa.Integer(), nullable=False, server_default="3"))
    op.create_check_constraint("ck_workspace_agent_limits", "workspaces", "agent_limit >= 1 AND language_limit >= 1")
    op.execute('CREATE TABLE ai_agents (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tavatar_data BYTEA, \n\tllm_enabled BOOLEAN NOT NULL, \n\tpersona TEXT NOT NULL, \n\tlanguages JSON NOT NULL, \n\tdefault_language VARCHAR(16) NOT NULL, \n\tvoice_enabled BOOLEAN NOT NULL, \n\tvoice_name VARCHAR(100) NOT NULL, \n\tvoice_speed FLOAT NOT NULL, \n\trevision INTEGER NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_agent_revision CHECK (revision >= 1), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_ai_agents_workspace_id ON ai_agents (workspace_id)')
    op.execute('CREATE TABLE workspace_role_permissions (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tmanage_ai_agents BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_workspace_role_permission UNIQUE (workspace_id, role), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_workspace_role_permissions_workspace_id ON workspace_role_permissions (workspace_id)')
    op.execute('CREATE TABLE agent_changes (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tagent_id UUID, \n\tagent_name VARCHAR(100) NOT NULL, \n\taction VARCHAR(60) NOT NULL, \n\tsummary VARCHAR(500) NOT NULL, \n\tactor_id UUID, \n\trevision INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE SET NULL, \n\tFOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL\n)')
    op.execute('CREATE INDEX ix_agent_changes_agent_id ON agent_changes (agent_id)')
    op.execute('CREATE INDEX ix_agent_changes_workspace_id ON agent_changes (workspace_id)')
    op.execute('CREATE TABLE agent_connections (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\ttarget_id UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_agent_connection UNIQUE (source_id, target_id), \n\tCONSTRAINT ck_agent_connection_no_self CHECK (source_id <> target_id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(source_id) REFERENCES ai_agents (id) ON DELETE CASCADE, \n\tFOREIGN KEY(target_id) REFERENCES ai_agents (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_agent_connections_source_id ON agent_connections (source_id)')
    op.execute('CREATE INDEX ix_agent_connections_target_id ON agent_connections (target_id)')
    op.execute('CREATE INDEX ix_agent_connections_workspace_id ON agent_connections (workspace_id)')
    op.execute('CREATE TABLE agent_conversations (\n\tid UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tagent_id UUID, \n\tuser_id UUID NOT NULL, \n\tagent_name VARCHAR(100) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE SET NULL, \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_agent_conversations_agent_id ON agent_conversations (agent_id)')
    op.execute('CREATE INDEX ix_agent_conversations_user_id ON agent_conversations (user_id)')
    op.execute('CREATE INDEX ix_agent_conversations_workspace_id ON agent_conversations (workspace_id)')
    op.execute('CREATE TABLE agent_versions (\n\tid UUID NOT NULL, \n\tagent_id UUID NOT NULL, \n\tnumber INTEGER NOT NULL, \n\tsection VARCHAR(40) NOT NULL, \n\tsnapshot JSON NOT NULL, \n\tactor_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_agent_version_number UNIQUE (agent_id, number), \n\tFOREIGN KEY(agent_id) REFERENCES ai_agents (id) ON DELETE CASCADE, \n\tFOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL\n)')
    op.execute('CREATE INDEX ix_agent_versions_agent_id ON agent_versions (agent_id)')
    op.execute('CREATE TABLE agent_messages (\n\tid UUID NOT NULL, \n\tconversation_id UUID NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tcontent TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(conversation_id) REFERENCES agent_conversations (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_agent_messages_conversation_id ON agent_messages (conversation_id)')

    connection = op.get_bind()
    metadata = sa.MetaData()
    workspaces = sa.Table("workspaces", metadata, autoload_with=connection)
    members = sa.Table("workspace_memberships", metadata, autoload_with=connection)
    roles = sa.Table("workspace_role_permissions", metadata, autoload_with=connection)
    agents = sa.Table("ai_agents", metadata, autoload_with=connection)
    versions = sa.Table("agent_versions", metadata, autoload_with=connection)
    changes = sa.Table("agent_changes", metadata, autoload_with=connection)
    for workspace in connection.execute(sa.select(workspaces.c.id, workspaces.c.created_at)).mappings():
        actor_id = connection.execute(sa.select(members.c.user_id).where(members.c.workspace_id == workspace["id"], members.c.role == "owner").order_by(members.c.created_at, members.c.id).limit(1)).scalar()
        for role in ("owner", "admin", "member"):
            connection.execute(roles.insert().values(id=uuid.uuid4(), workspace_id=workspace["id"], role=role, manage_ai_agents=role != "member"))
        agent_id = uuid.uuid4()
        snapshot = {"name": "Default AI Agent", "llm_enabled": True, "persona": "You are a helpful assistant for this workspace. Be clear, professional and concise. Ask for clarification when needed. Do not invent facts.", "languages": ["en"], "default_language": "en", "voice_enabled": False, "voice_name": "", "voice_speed": 1.0, "avatar_base64": None}
        connection.execute(agents.insert().values(id=agent_id, workspace_id=workspace["id"], revision=1, created_at=sa.func.now(), updated_at=sa.func.now(), **{key: value for key, value in snapshot.items() if key != "avatar_base64"}))
        connection.execute(versions.insert().values(id=uuid.uuid4(), agent_id=agent_id, number=1, section="created", snapshot=snapshot, actor_id=actor_id, created_at=sa.func.now()))
        connection.execute(changes.insert().values(id=uuid.uuid4(), workspace_id=workspace["id"], agent_id=agent_id, agent_name="Default AI Agent", action="created", summary="Default AI Agent added during workspace upgrade.", actor_id=actor_id, revision=1, created_at=sa.func.now()))


def downgrade():
    op.drop_table('agent_messages')
    op.drop_table('agent_versions')
    op.drop_table('agent_conversations')
    op.drop_table('agent_connections')
    op.drop_table('agent_changes')
    op.drop_table('workspace_role_permissions')
    op.drop_table('ai_agents')
    op.drop_constraint("ck_workspace_agent_limits", "workspaces", type_="check")
    op.drop_column("workspaces", "language_limit")
    op.drop_column("workspaces", "agent_limit")
    op.drop_column("workspaces", "plan_name")
