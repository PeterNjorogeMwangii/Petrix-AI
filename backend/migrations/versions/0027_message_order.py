"""Durable per-conversation message order, independent of clock precision. Frozen DDL."""
from alembic import op

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None

SEQUENCE_FUNCTION = '''CREATE FUNCTION petrix_assign_message_sequence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE agent_conversations
       SET message_sequence = message_sequence + 1
     WHERE id = NEW.conversation_id
     RETURNING message_sequence INTO NEW.sequence;
    RETURN NEW;
END;
$$'''

SEQUENCE_TRIGGER = '''CREATE TRIGGER agent_message_sequence
BEFORE INSERT ON agent_messages
FOR EACH ROW EXECUTE FUNCTION petrix_assign_message_sequence()'''


def upgrade():
    op.execute('ALTER TABLE agent_conversations ADD COLUMN message_sequence BIGINT NOT NULL DEFAULT 0')
    op.execute('ALTER TABLE agent_messages ADD COLUMN sequence BIGINT')
    # Old timestamps can be identical on Windows. Preserve chronological groups;
    # within a tied group place customer input before the responses it triggered.
    # The original order among multiple legacy replies was never recorded.
    op.execute('''WITH ordered AS (
        SELECT id, row_number() OVER (
            PARTITION BY conversation_id
            ORDER BY created_at, CASE role WHEN 'user' THEN 0 ELSE 1 END, id
        ) AS position
        FROM agent_messages
    ) UPDATE agent_messages AS m SET sequence = ordered.position
      FROM ordered WHERE m.id = ordered.id''')
    op.execute('''UPDATE agent_conversations AS c SET message_sequence = counts.last_sequence
        FROM (SELECT conversation_id, max(sequence) AS last_sequence
              FROM agent_messages GROUP BY conversation_id) AS counts
        WHERE c.id = counts.conversation_id''')
    op.execute('ALTER TABLE agent_messages ALTER COLUMN sequence SET NOT NULL, ALTER COLUMN sequence SET DEFAULT 0')
    op.execute('ALTER TABLE agent_messages ADD CONSTRAINT ck_agent_message_sequence CHECK (sequence > 0)')
    op.execute('ALTER TABLE agent_conversations ADD CONSTRAINT ck_conversation_message_sequence CHECK (message_sequence >= 0)')
    op.execute('CREATE UNIQUE INDEX ix_agent_message_sequence ON agent_messages (conversation_id, sequence)')
    op.execute(SEQUENCE_FUNCTION)
    op.execute(SEQUENCE_TRIGGER)


def downgrade():
    op.execute('DROP TRIGGER agent_message_sequence ON agent_messages')
    op.execute('DROP FUNCTION petrix_assign_message_sequence()')
    op.execute('DROP INDEX ix_agent_message_sequence')
    op.execute('ALTER TABLE agent_messages DROP CONSTRAINT ck_agent_message_sequence, DROP COLUMN sequence')
    op.execute('ALTER TABLE agent_conversations DROP CONSTRAINT ck_conversation_message_sequence, DROP COLUMN message_sequence')
