"""Ticket workspace, manual creation, notes and authenticated mail events. Frozen DDL."""
from alembic import op
import sqlalchemy as sa
revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('support_tickets',sa.Column('teamspace_id',sa.Uuid(),nullable=True))
    op.create_foreign_key('fk_ticket_teamspace','support_tickets','teamspaces',['teamspace_id'],['id'],ondelete='RESTRICT')
    op.create_index('ix_support_tickets_teamspace_id','support_tickets',['teamspace_id'])
    op.add_column('support_tickets',sa.Column('assigned_team_ids',sa.JSON(),nullable=False,server_default='[]'))
    op.add_column('support_tickets',sa.Column('sender_domain_id',sa.Uuid(),nullable=True))
    op.create_foreign_key('fk_ticket_sender_domain','support_tickets','ticket_email_domains',['sender_domain_id'],['id'],ondelete='SET NULL')
    op.add_column('support_tickets',sa.Column('origin',sa.String(12),nullable=False,server_default='workflow'))
    op.add_column('support_tickets',sa.Column('client_request_id',sa.Uuid(),nullable=True))
    op.add_column('support_tickets',sa.Column('create_payload_hash',sa.String(64),nullable=False,server_default=''))
    op.create_unique_constraint('uq_ticket_manual_request','support_tickets',['workspace_id','client_request_id'])
    op.execute("UPDATE support_tickets t SET teamspace_id=COALESCE((SELECT a.teamspace_id FROM agent_conversations c JOIN ai_agents a ON a.id=c.agent_id WHERE c.id=t.conversation_id),(SELECT s.id FROM teamspaces s WHERE s.workspace_id=t.workspace_id AND s.is_default LIMIT 1))")
    op.execute("UPDATE support_tickets SET assigned_team_ids=json_build_array(CAST(assignee_id AS text)) WHERE assignee_type='team' AND assignee_id IS NOT NULL")
    op.execute("UPDATE support_tickets t SET sender_domain_id=(SELECT e.domain_id FROM ticket_emails e WHERE e.ticket_id=t.id AND e.recipient_type='customer' ORDER BY e.created_at,e.id LIMIT 1)")
    op.add_column('ticket_notes',sa.Column('color',sa.String(7),nullable=False,server_default='#fff0b3'))
    op.add_column('ticket_notes',sa.Column('revision',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('ticket_notes',sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.execute('UPDATE ticket_notes SET updated_at=created_at')
    op.add_column('ticket_emails',sa.Column('cc_recipients',sa.JSON(),nullable=False,server_default='[]'))
    op.add_column('ticket_emails',sa.Column('delivery_status',sa.String(16),nullable=False,server_default=''))
    op.add_column('ticket_emails',sa.Column('delivery_at',sa.DateTime(timezone=True)))
    op.create_table('ticket_inbound_emails',sa.Column('id',sa.Uuid(),primary_key=True),sa.Column('ticket_id',sa.Uuid(),sa.ForeignKey('support_tickets.id',ondelete='CASCADE'),nullable=False),sa.Column('message_id',sa.String(320),nullable=False),sa.Column('sender',sa.String(320),nullable=False),sa.Column('recipient',sa.String(320),nullable=False),sa.Column('subject',sa.String(250),nullable=False),sa.Column('content',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_ticket_inbound_emails_ticket_id','ticket_inbound_emails',['ticket_id'])
    op.create_table('ticket_mail_events',sa.Column('message_key',sa.String(64)),sa.UniqueConstraint('workspace_id','message_key',name='uq_ticket_inbound_message'),sa.Column('workspace_id',sa.Uuid(),sa.ForeignKey('workspaces.id',ondelete='CASCADE'),primary_key=True),sa.Column('event_id',sa.String(200),primary_key=True),sa.Column('payload_hash',sa.String(64),nullable=False),sa.Column('ticket_id',sa.Uuid(),sa.ForeignKey('support_tickets.id',ondelete='SET NULL')),sa.Column('email_id',sa.Uuid(),sa.ForeignKey('ticket_emails.id',ondelete='SET NULL')),sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table('ticket_mail_events');op.drop_table('ticket_inbound_emails')
    for name in ['delivery_at','delivery_status','cc_recipients']:op.drop_column('ticket_emails',name)
    for name in ['updated_at','revision','color']:op.drop_column('ticket_notes',name)
    op.drop_constraint('uq_ticket_manual_request','support_tickets',type_='unique')
    op.drop_constraint('fk_ticket_sender_domain','support_tickets',type_='foreignkey');op.drop_constraint('fk_ticket_teamspace','support_tickets',type_='foreignkey')
    op.drop_index('ix_support_tickets_teamspace_id',table_name='support_tickets')
    for name in ['create_payload_hash','client_request_id','origin','sender_domain_id','assigned_team_ids','teamspace_id']:op.drop_column('support_tickets',name)
