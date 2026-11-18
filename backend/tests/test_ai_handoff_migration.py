from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_ai_network_upgrade_preserves_existing_external_and_chat_state(database_engine):
    schema='petrix_ai_upgrade_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as connection:
        original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "{}"'.format(schema)));connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema)));connection.commit();config.attributes['connection']=connection
            with connection.begin():command.upgrade(config,'0012')
            workspace,user,conversation=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"),{'id':workspace})
            connection.execute(text("INSERT INTO users (id,email,created_at) VALUES (:id,'old@example.com',now())"),{'id':user})
            connection.execute(text("INSERT INTO agent_conversations (id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:id,:workspace,:user,'Old agent','{\"variables\":{\"ref\":\"EXISTING\"}}','{}',now())"),{'id':conversation,'workspace':workspace,'user':user})
            connection.execute(text("INSERT INTO external_conversations (conversation_id,workspace_id,platform,external_id,account_key,status,error,revision,updated_at) VALUES (:id,:workspace,'zendesk','original','account','transferred','',4,now())"),{'id':conversation,'workspace':workspace});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0018'
            assert connection.execute(text('SELECT workflow_state,live_status FROM agent_conversations')).one()==({'variables':{'ref':'EXISTING'}},'none')
            assert connection.execute(text('SELECT status,external_id,revision FROM external_conversations')).one()==('transferred','original',4)
            for table in ('ai_network_credentials','ai_transfer_connections','ai_inbound_sessions','ai_inbound_requests','ai_outgoing_sessions'):
                assert connection.execute(text('SELECT count(*) FROM '+table)).scalar()==0
            connection.commit()
            with connection.begin():command.downgrade(config,'0012')
            assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'EXISTING'}};connection.commit()
        finally:
            connection.rollback();connection.execute(text('SET search_path TO "{}"'.format(original)));connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema)));connection.commit()
