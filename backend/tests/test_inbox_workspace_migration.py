"""0022 preserves existing CRM/knowledge content and supports repeat upgrades."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0022_additive_upgrade_and_downgrade_preserve_existing_sources(database_engine):
    schema='inbox_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as c:
        original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
        try:
            c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
            with c.begin():command.upgrade(config,'0021')
            ws,user,person,chat,space,source=[uuid.uuid4() for _ in range(6)]
            c.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Existing Inbox',now())"),{'id':ws})
            c.execute(text("INSERT INTO users(id,email,created_at) VALUES(:id,'existing-inbox@example.com',now())"),{'id':user})
            c.execute(text("INSERT INTO teamspaces(id,workspace_id,name,name_key,is_default,revision) VALUES(:id,:ws,'Default','default',true,1)"),{'id':space,'ws':ws})
            c.execute(text("INSERT INTO copilot_knowledge_sources(id,workspace_id,teamspace_id,name,name_key,kind,url,content,revision,updated_at) VALUES(:id,:ws,:space,'Existing FAQ','existing faq','snippet','','PRESERVE KNOWLEDGE',7,now())"),{'id':source,'ws':ws,'space':space})
            c.execute(text("INSERT INTO support_people(id,workspace_id,user_id,identity_key,name,email,phone,location,country,custom_fields,offences,ban_permanent,ban_reason,revision,created_at,updated_at) VALUES(:id,:ws,:user,'inbox-person','Existing Name','person@example.com','+254700000001','Nairobi','KE','{}',0,false,'',8,now(),now())"),{'id':person,'ws':ws,'user':user})
            c.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,person_id,agent_name,workflow_state,created_at) VALUES(:id,:ws,:user,:person,'Existing Agent','{\"variables\":{\"keep\":\"YES\"}}',now())"),{'id':chat,'ws':ws,'user':user,'person':person});c.commit()
            with c.begin():command.upgrade(config,'0022')
            with c.begin():command.upgrade(config,'0022')
            assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0022'
            assert c.execute(text('SELECT id,content,revision,status,options,next_refresh_at FROM copilot_knowledge_sources')).one()==(source,'PRESERVE KNOWLEDGE',7,'success',{},None)
            assert c.execute(text('SELECT person_id,workflow_state FROM agent_conversations')).one()==(person,{'variables':{'keep':'YES'}})
            assert c.execute(text('SELECT name,revision FROM support_people')).one()==('Existing Name',8)
            assert c.execute(text('SELECT count(*) FROM inbox_views')).scalar()==0;c.commit()
            with c.begin():command.downgrade(config,'0021')
            assert c.execute(text('SELECT id,content,revision FROM copilot_knowledge_sources')).one()==(source,'PRESERVE KNOWLEDGE',7);c.commit()
            with c.begin():command.upgrade(config,'0022')
            assert c.execute(text('SELECT person_id FROM agent_conversations')).scalar()==person;c.commit()
        finally:
            c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
