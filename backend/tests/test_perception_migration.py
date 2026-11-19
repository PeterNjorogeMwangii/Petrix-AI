"""0024 adds only Perception tables; existing analytics and support data survive."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0024_upgrade_repeat_downgrade_preserves_0023(database_engine):
 schema='perception_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as c:
  original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
  try:
   c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
   with c.begin():command.upgrade(config,'0023')
   ws,user,space,chat,message=[uuid.uuid4() for _ in range(5)]
   c.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Keep Perception',now())"),{'id':ws})
   c.execute(text("INSERT INTO users(id,email,created_at) VALUES(:id,'keep-perception@example.com',now())"),{'id':user})
   c.execute(text("INSERT INTO workspace_settings(workspace_id,revision,ip_enabled,exempt_admins,ip_ranges,idle_minutes,colors,branding_enabled,timezone) VALUES(:ws,9,false,true,'[]',20,'{}',false,'UTC')"),{'ws':ws})
   c.execute(text("INSERT INTO teamspaces(id,workspace_id,name,name_key,is_default,revision) VALUES(:id,:ws,'Default','default',true,1)"),{'id':space,'ws':ws})
   c.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,created_at) VALUES(:id,:ws,:user,'Keep agent','{}',now())"),{'id':chat,'ws':ws,'user':user})
   c.execute(text("INSERT INTO agent_messages(id,conversation_id,role,content,message_data,created_at) VALUES(:id,:chat,'user','KEEP MESSAGE','{}',now())"),{'id':message,'chat':chat})
   c.commit()
   for _ in range(2):
    with c.begin():command.upgrade(config,'0024')
   assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0024'
   assert c.execute(text('SELECT timezone,revision,idle_minutes FROM workspace_settings')).one()==('UTC',9,20)
   assert c.execute(text('SELECT content FROM agent_messages')).scalar()=='KEEP MESSAGE'
   tables=c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'perception_%'")).scalars().all();assert len(tables)==6;c.commit()
   with c.begin():command.downgrade(config,'0023')
   assert c.execute(text('SELECT content FROM agent_messages')).scalar()=='KEEP MESSAGE';c.commit()
   with c.begin():command.upgrade(config,'0024')
   assert c.execute(text('SELECT timezone FROM workspace_settings')).scalar()=='UTC';c.commit()
  finally:
   c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
