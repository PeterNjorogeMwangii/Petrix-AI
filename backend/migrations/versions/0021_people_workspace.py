"""Private People notes and retry-safe manual registration. Frozen PostgreSQL DDL."""
from alembic import op
import sqlalchemy as sa
revision='0021'
down_revision='0020'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('support_people',sa.Column('client_request_id',sa.Uuid(),nullable=True))
    op.add_column('support_people',sa.Column('create_payload_hash',sa.String(64),nullable=False,server_default=''))
    op.create_unique_constraint('uq_person_manual_request','support_people',['workspace_id','client_request_id'])
    op.create_table('person_notes',
        sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('person_id',sa.Uuid(),sa.ForeignKey('support_people.id',ondelete='CASCADE'),nullable=False),
        sa.Column('author_id',sa.Uuid(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('author_name',sa.String(200),nullable=False),
        sa.Column('content',sa.Text(),nullable=False),
        sa.Column('color',sa.String(7),nullable=False),
        sa.Column('revision',sa.Integer(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.CheckConstraint('revision >= 1',name='ck_person_note_revision'))
    op.create_index('ix_person_notes_person_id','person_notes',['person_id'])

def downgrade():
    op.drop_table('person_notes')
    op.drop_constraint('uq_person_manual_request','support_people',type_='unique')
    op.drop_column('support_people','create_payload_hash')
    op.drop_column('support_people','client_request_id')
