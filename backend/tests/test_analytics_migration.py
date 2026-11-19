"""0023 is additive and keeps existing Inbox, preferences and messages intact."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text


def test_0023_upgrade_repeat_downgrade_and_reupgrade_preserve_0022(database_engine):
 schema='analytics_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as c:
  original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
  try:
   c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
   with c.begin():command.upgrade(config,'0022')
   ws,user,space,chat,view,message=[uuid.uuid4() for _ in range(6)]
   c.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Keep Analytics',now())"),{'id':ws})
   c.execute(text("INSERT INTO users(id,email,created_at) VALUES(:id,'keep-analytics@example.com',now())"),{'id':user})
   c.execute(text("INSERT INTO workspace_settings(workspace_id,revision,ip_enabled,exempt_admins,ip_ranges,idle_minutes,colors,branding_enabled) VALUES(:ws,8,false,true,'[]',20,'{}',false)"),{'ws':ws})
   c.execute(text("INSERT INTO teamspaces(id,workspace_id,name,name_key,is_default,revision) VALUES(:id,:ws,'Default','default',true,1)"),{'id':space,'ws':ws})
   c.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,created_at) VALUES(:id,:ws,:user,'Keep agent',CAST(:state AS JSON),now())"),{'id':chat,'ws':ws,'user':user,'state':'{"csat_rating":5,"variables":{"keep":true}}'})
   c.execute(text("INSERT INTO agent_messages(id,conversation_id,role,content,message_data,created_at) VALUES(:id,:chat,'user','KEEP MESSAGE','{}',now())"),{'id':message,'chat':chat})
   c.execute(text("INSERT INTO inbox_views(id,workspace_id,teamspace_id,owner_id,scope,name,filters,revision,created_at,updated_at) VALUES(:id,:ws,:space,:user,'private','Keep view','{}',4,now(),now())"),{'id':view,'ws':ws,'space':space,'user':user})
   c.commit()
   for _ in range(2):
    with c.begin():command.upgrade(config,'0023')
   assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0023'
   assert c.execute(text('SELECT timezone,revision,idle_minutes,analytics_started_at IS NOT NULL FROM workspace_settings')).one()==('Africa/Nairobi',8,20,True)
   assert c.execute(text('SELECT id,content FROM agent_messages')).one()==(message,'KEEP MESSAGE')
   assert c.execute(text('SELECT name,revision FROM inbox_views')).one()==('Keep view',4)
   assert c.execute(text('SELECT count(*) FROM analytics_activity')).scalar()==0;c.commit()
   with c.begin():command.downgrade(config,'0022')
   assert c.execute(text('SELECT id,content FROM agent_messages')).one()==(message,'KEEP MESSAGE');c.commit()
   with c.begin():command.upgrade(config,'0023')
   assert c.execute(text('SELECT name,revision FROM inbox_views')).one()==('Keep view',4);c.commit()
  finally:
   c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
