from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text

def test_0016_upgrade_retains_people_identity_tags_and_existing_records(database_engine):
 config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));schema='petrix_teamspace_'+uuid.uuid4().hex
 with database_engine.connect() as connection:
  original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "'+schema+'"'));connection.commit()
  try:
   connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit();config.attributes['connection']=connection
   with connection.begin():command.upgrade(config,'0015')
   ws,user,chat=uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
   connection.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES (:id,'Existing',now())"),{'id':ws});connection.execute(text("INSERT INTO users(id,email,created_at) VALUES (:id,'existing@example.com',now())"),{'id':user})
   connection.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,custom_fields,created_at) VALUES (:chat,:ws,:user,'Existing agent','{\"variables\":{\"ref\":\"KEEP\"}}','{}',now())"),{'chat':chat,'ws':ws,'user':user});connection.commit()
   with connection.begin():command.upgrade(config,'head')
   with connection.begin():command.upgrade(config,'head')
   assert connection.execute(text('SELECT person_id IS NOT NULL FROM agent_conversations')).scalar();assert connection.execute(text('SELECT email FROM support_people')).scalar()=='existing@example.com';assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'KEEP'}};connection.commit()
   with connection.begin():command.downgrade(config,'0015')
   assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'KEEP'}};connection.commit()
   with connection.begin():command.upgrade(config,'head')
   assert connection.execute(text('SELECT count(*) FROM support_people')).scalar()==1;connection.commit()
  finally:
   connection.rollback();connection.execute(text('SET search_path TO "'+original+'"'));connection.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));connection.commit()
