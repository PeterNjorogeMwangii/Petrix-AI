"""0027 repairs tied legacy ordering without changing message content, IDs or times."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models import Workspace, User


def test_0027_backfill_repeat_downgrade_and_reupgrade_preserve_messages(database_engine):
    schema = 'message_order_'+uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as c:
        original = c.execute(text('SELECT current_schema()')).scalar()
        c.execute(text('CREATE SCHEMA "'+schema+'"')); c.commit()
        try:
            c.execute(text('SET search_path TO "'+schema+'"')); c.commit()
            config.attributes['connection'] = c
            with c.begin(): command.upgrade(config, '0026')
            workspace, user, conversation = [uuid.uuid4() for _ in range(3)]
            with Session(c) as db:
                db.add_all([Workspace(id=workspace, name='Keep message history'), User(id=user, email='keep-order@example.com')]); db.commit()
            c.execute(text("INSERT INTO agent_conversations (id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:id,:ws,:user,'Keep bot','{}','{}',now())"), {'id':conversation,'ws':workspace,'user':user})
            insert = text("INSERT INTO agent_messages (id,conversation_id,role,content,message_data,created_at) VALUES (:id,:chat,:role,:content,'{}','2026-11-19T08:00:00Z')")
            identifiers = [uuid.UUID(int=2**128-1), uuid.UUID(int=1)]
            for identifier, role, content in zip(identifiers, ['user','assistant'], ['hi','Bot answer']):
                c.execute(insert, {'id':identifier,'chat':conversation,'role':role,'content':content})
            before = c.execute(text('SELECT id,content,created_at FROM agent_messages ORDER BY id')).all(); c.commit()
            for _ in range(2):
                with c.begin(): command.upgrade(config, '0027')
            assert c.execute(text('SELECT content,sequence FROM agent_messages ORDER BY sequence')).all() == [('hi',1),('Bot answer',2)]
            assert c.execute(text('SELECT message_sequence FROM agent_conversations WHERE id=:id'), {'id':conversation}).scalar() == 2
            assert c.execute(text('SELECT id,content,created_at FROM agent_messages ORDER BY id')).all() == before
            c.execute(insert, {'id':uuid.uuid4(),'chat':conversation,'role':'user','content':'Next turn'})
            assert c.execute(text('SELECT content FROM agent_messages ORDER BY sequence')).scalars().all() == ['hi','Bot answer','Next turn']
            assert c.execute(text('SELECT message_sequence FROM agent_conversations WHERE id=:id'), {'id':conversation}).scalar() == 3
            saved = c.execute(text('SELECT id,content,created_at FROM agent_messages ORDER BY id')).all(); c.commit()
            with c.begin(): command.downgrade(config, '0026')
            assert c.execute(text('SELECT id,content,created_at FROM agent_messages ORDER BY id')).all() == saved; c.commit()
            with c.begin(): command.upgrade(config, '0027')
            assert c.execute(text('SELECT id,content,created_at FROM agent_messages ORDER BY id')).all() == saved
            assert c.execute(text('SELECT count(DISTINCT sequence) FROM agent_messages')).scalar() == 3; c.commit()
        finally:
            c.rollback(); c.execute(text('SET search_path TO "'+original+'"')); c.execute(text('DROP SCHEMA "'+schema+'" CASCADE')); c.commit()
