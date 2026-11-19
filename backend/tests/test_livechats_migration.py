from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0019_preserves_existing_livechat_workflow_priority_and_messages(database_engine):
    schema='livechats_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as connection:
        original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "'+schema+'"'));connection.commit()
        try:
            connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit();config.attributes['connection']=connection
            with connection.begin():command.upgrade(config,'0018')
            ws,user,chat,msg=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Preserved workspace',now())"),{'id':ws})
            connection.execute(text("INSERT INTO users(id,email,created_at) VALUES(:id,'preserved@example.invalid',now())"),{'id':user})
            connection.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at,live_status,live_context,live_revision) VALUES(:id,:ws,:user,'Preserved agent','{\"variables\":{\"ref\":\"OLD-1\"}}','{\"category\":{\"value\":\"support\"}}',now(),'active','{\"priority\": true}',7)"),{'id':chat,'ws':ws,'user':user})
            connection.execute(text("INSERT INTO agent_messages(id,conversation_id,role,content,message_data,created_at) VALUES(:id,:chat,'user','Preserved message','{}',now())"),{'id':msg,'chat':chat});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT live_description,live_display_status,live_revision,live_context,workflow_state FROM agent_conversations')).one()==('','',7,{'priority':True},{'variables':{'ref':'OLD-1'}})
            assert connection.execute(text('SELECT content FROM agent_messages')).scalar()=='Preserved message';connection.commit()
            with connection.begin():command.downgrade(config,'0018')
            assert connection.execute(text('SELECT live_revision,live_context FROM agent_conversations')).one()==(7,{'priority':True});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT count(*) FROM agent_messages')).scalar()==1;connection.commit()
        finally:
            connection.rollback();connection.execute(text('SET search_path TO "'+original+'"'));connection.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));connection.commit()
