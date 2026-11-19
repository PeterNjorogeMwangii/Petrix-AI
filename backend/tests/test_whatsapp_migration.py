"""0028 is additive, repeat-safe, reversible and retains durable message order."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models import Workspace,User


def test_0028_upgrade_repeat_downgrade_reupgrade_preserves_0027(database_engine):
 schema='whatsapp_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as c:
  original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
  try:
   c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
   with c.begin():command.upgrade(config,'0027')
   ws,user,agent,chat=[uuid.uuid4() for _ in range(4)]
   with Session(c) as db:
    db.add_all([Workspace(id=ws,name='Keep workspace'),User(id=user,email='preserve-wa@example.com')]);db.commit()
   c.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:id,:ws,:user,'Keep agent','{}','{}',now())"),{'id':chat,'ws':ws,'user':user})
   c.execute(text("INSERT INTO agent_messages(id,conversation_id,role,content,message_data,created_at) VALUES (:id,:chat,'user','KEEP CUSTOMER QUESTION',CAST('{}' AS JSON),now())"),{'id':uuid.uuid4(),'chat':chat});c.commit()
   for _ in range(2):
    with c.begin():command.upgrade(config,'0028')
   assert c.execute(text("SELECT count(*) FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'whatsapp_%'")).scalar()==5
   assert c.execute(text('SELECT content,sequence FROM agent_messages')).one()==('KEEP CUSTOMER QUESTION',1)
   assert c.execute(text('SELECT message_sequence FROM agent_conversations')).scalar()==1;c.commit()
   with c.begin():command.downgrade(config,'0027')
   assert c.execute(text('SELECT content,sequence FROM agent_messages')).one()==('KEEP CUSTOMER QUESTION',1);c.commit()
   with c.begin():command.upgrade(config,'head')
   assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0028';c.commit()
  finally:
   c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
