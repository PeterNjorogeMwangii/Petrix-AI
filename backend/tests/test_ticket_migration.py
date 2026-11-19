from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text


def test_ticket_upgrade_and_downgrade_preserve_previous_ai_handoff(database_engine):
    schema='petrix_ticket_upgrade_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as connection:
        original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "{}"'.format(schema)));connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema)));connection.commit();config.attributes['connection']=connection
            with connection.begin():command.upgrade(config,'0013')
            workspace,user,conversation=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"),{'id':workspace})
            connection.execute(text("INSERT INTO users (id,email,created_at) VALUES (:id,'old@example.com',now())"),{'id':user})
            connection.execute(text("INSERT INTO agent_conversations (id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:id,:workspace,:user,'Existing agent','{\"variables\":{\"ref\":\"KEEP\"}}','{}',now())"),{'id':conversation,'workspace':workspace,'user':user})
            connection.execute(text("INSERT INTO ai_outgoing_sessions (conversation_id,transfer_id,target_id,target_name,domain,status,token_ciphertext,start_payload,pending,last_result,error,updated_at) VALUES (:id,:transfer,:target,'Receiving agent','https://receiver.example','starting','','{}','{}','{}','',now())"),{'id':conversation,'transfer':uuid.uuid4(),'target':uuid.uuid4()});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()==ScriptDirectory.from_config(config).get_current_head()
            assert connection.execute(text('SELECT status FROM ai_outgoing_sessions')).scalar()=='starting'
            assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'KEEP'}}
            for table in ['ticket_tag_groups','ticket_tags','ticket_email_domains','ticket_email_templates','ticket_counters','support_tickets','ticket_notes','ticket_emails']:
                assert connection.execute(text('SELECT count(*) FROM '+table)).scalar()==0
            connection.commit()
            with connection.begin():command.downgrade(config,'0013')
            assert connection.execute(text('SELECT status FROM ai_outgoing_sessions')).scalar()=='starting';connection.commit()
        finally:
            connection.rollback();connection.execute(text('SET search_path TO "{}"'.format(original)));connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema)));connection.commit()
