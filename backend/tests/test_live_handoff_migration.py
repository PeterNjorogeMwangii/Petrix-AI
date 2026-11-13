"""Frozen prior schema upgrades preserve saved chats and configure manual assignment."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_live_handoff_upgrade_is_additive_and_repeatable(database_engine):
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));schema='petrix_live_upgrade_'+uuid.uuid4().hex
    with database_engine.connect() as connection:
        original=connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema)));connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema)));connection.commit();config.attributes['connection']=connection
            with connection.begin():command.upgrade(config,'0010')
            workspace,user,conversation,team=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"),{'id':workspace})
            connection.execute(text("INSERT INTO users (id,email,created_at) VALUES (:id,'old@example.com',now())"),{'id':user})
            connection.execute(text("INSERT INTO agent_conversations (id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:id,:workspace,:user,'Old agent','{\"variables\":{\"ref\":\"OLD-1\"}}','{\"category\":{\"value\":\"support\"}}',now())"),{'id':conversation,'workspace':workspace,'user':user})
            connection.execute(text("INSERT INTO support_teams (id,workspace_id,name,description,alternative_names,revision,archived,created_at,updated_at) VALUES (:id,:workspace,'Existing team','','[\"Old alias\"]',3,false,now(),now())"),{'id':team,'workspace':workspace});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT live_status,live_context,live_revision,workflow_state,custom_fields FROM agent_conversations')).one()==('none',{},1,{'variables':{'ref':'OLD-1'}},{'category':{'value':'support'}})
            assert connection.execute(text('SELECT member_ids,revision,alternative_names FROM support_teams')).one()==([],3,['Old alias'])
            assert connection.execute(text('SELECT mode,max_active_chats,revision FROM chat_assignments')).one()==('manual',5,1)
            connection.commit()
            with connection.begin():command.downgrade(config,'0010')
            assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'OLD-1'}};connection.commit()
        finally:
            connection.rollback();connection.execute(text('SET search_path TO "{}"'.format(original)));connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema)));connection.commit()
