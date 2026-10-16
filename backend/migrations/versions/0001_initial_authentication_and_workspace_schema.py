"""Initial authentication and workspace schema. Frozen snapshot; do not import live models."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE TABLE auth_rate_limits (\n\tkey VARCHAR(64) NOT NULL, \n\tcount INTEGER NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (key)\n)')
    op.execute('CREATE INDEX ix_auth_rate_limits_expires_at ON auth_rate_limits (expires_at)')
    op.execute('CREATE TABLE pending_identities (\n\tid UUID NOT NULL, \n\tticket_hash VARCHAR(64) NOT NULL, \n\tprovider VARCHAR(20) NOT NULL, \n\tissuer VARCHAR(255) NOT NULL, \n\tsubject VARCHAR(255) NOT NULL, \n\tfull_name VARCHAR(200), \n\temail_hint VARCHAR(320), \n\trequested_at TIMESTAMP WITH TIME ZONE, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (ticket_hash)\n)')
    op.execute('CREATE INDEX ix_pending_identities_expires_at ON pending_identities (expires_at)')
    op.execute('CREATE TABLE users (\n\tid UUID NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\tfull_name VARCHAR(200), \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (email)\n)')
    op.execute('CREATE TABLE workspaces (\n\tid UUID NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tindustry VARCHAR(80), \n\tcountry_code VARCHAR(2), \n\twebsite VARCHAR(2048), \n\tavatar_data BYTEA, \n\tsetup_completed_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)')
    op.execute('CREATE TABLE auth_identities (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tprovider VARCHAR(20) NOT NULL, \n\tissuer VARCHAR(255) NOT NULL, \n\tsubject VARCHAR(255) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_provider_identity UNIQUE (provider, issuer, subject), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_auth_identities_user_id ON auth_identities (user_id)')
    op.execute('CREATE TABLE auth_sessions (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tsession_hash VARCHAR(64) NOT NULL, \n\tcsrf_hash VARCHAR(64) NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tUNIQUE (session_hash)\n)')
    op.execute('CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at)')
    op.execute('CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id)')
    op.execute('CREATE TABLE magic_links (\n\tid UUID NOT NULL, \n\ttoken_hash VARCHAR(64) NOT NULL, \n\temail VARCHAR(320) NOT NULL, \n\tpending_identity_id UUID, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tused_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (token_hash), \n\tFOREIGN KEY(pending_identity_id) REFERENCES pending_identities (id) ON DELETE CASCADE\n)')
    op.execute('CREATE INDEX ix_magic_links_email ON magic_links (email)')
    op.execute('CREATE INDEX ix_magic_links_expires_at ON magic_links (expires_at)')
    op.execute("CREATE TABLE workspace_memberships (\n\tid UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\trole VARCHAR(16) NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_workspace_member UNIQUE (user_id, workspace_id), \n\tCONSTRAINT ck_membership_role CHECK (role IN ('owner', 'admin', 'member')), \n\tFOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, \n\tFOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE\n)")
    op.execute('CREATE INDEX ix_workspace_memberships_user_id ON workspace_memberships (user_id)')
    op.execute('CREATE INDEX ix_workspace_memberships_workspace_id ON workspace_memberships (workspace_id)')


def downgrade():
    op.drop_table('workspace_memberships')
    op.drop_table('magic_links')
    op.drop_table('auth_sessions')
    op.drop_table('auth_identities')
    op.drop_table('workspaces')
    op.drop_table('users')
    op.drop_table('pending_identities')
    op.drop_table('auth_rate_limits')
