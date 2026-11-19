from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0021_additive_upgrade_preserves_people_and_conversation_links(database_engine):
    schema='people_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as c:
        original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
        try:
            c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
            with c.begin():command.upgrade(config,'0020')
            ws,user,person,chat=[uuid.uuid4() for _ in range(4)]
            c.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Existing CRM',now())"),{'id':ws})
            c.execute(text("INSERT INTO users(id,email,created_at) VALUES(:id,'existing@example.com',now())"),{'id':user})
            c.execute(text("INSERT INTO support_people(id,workspace_id,user_id,identity_key,name,email,phone,location,country,custom_fields,offences,ban_permanent,ban_reason,revision,created_at,updated_at) VALUES(:id,:ws,:user,'existing-profile','Existing Name','person@example.com','+254700000001','Nairobi','KE','{\"segment\":{\"value\":\"KEEP\"}}',1,false,'Existing reason',8,now(),now())"),{'id':person,'ws':ws,'user':user})
            c.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,person_id,agent_name,workflow_state,created_at) VALUES(:id,:ws,:user,:person,'Existing Agent','{\"variables\":{\"keep\":\"YES\"}}',now())"),{'id':chat,'ws':ws,'user':user,'person':person});c.commit()
            with c.begin():command.upgrade(config,'head')
            with c.begin():command.upgrade(config,'head')
            assert c.execute(text('SELECT id,name,email,phone,country,custom_fields,revision,client_request_id,create_payload_hash FROM support_people')).one()==(person,'Existing Name','person@example.com','+254700000001','KE',{'segment':{'value':'KEEP'}},8,None,'')
            assert c.execute(text('SELECT person_id,workflow_state FROM agent_conversations')).one()==(person,{'variables':{'keep':'YES'}})
            assert c.execute(text('SELECT count(*) FROM person_notes')).scalar()==0;c.commit()
            with c.begin():command.downgrade(config,'0020')
            assert c.execute(text('SELECT id,name,revision FROM support_people')).one()==(person,'Existing Name',8);c.commit()
            with c.begin():command.upgrade(config,'head')
            assert c.execute(text('SELECT person_id FROM agent_conversations')).scalar()==person;c.commit()
        finally:
            c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
