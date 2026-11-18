from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text

def test_profile_0017_backfills_existing_names_and_preserves_prior_records(database_engine):
 schema='petrix_profile_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as connection:
  original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "'+schema+'"'));connection.commit()
  try:
   connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit();config.attributes['connection']=connection
   with connection.begin():command.upgrade(config,'0016')
   ws,user,chat=uuid.uuid4(),uuid.uuid4(),uuid.uuid4();connection.execute(text("INSERT INTO users(id,email,full_name,created_at) VALUES(:id,'existing@example.com','Chris Example',now())"),{'id':user});connection.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Existing',now())"),{'id':ws});connection.execute(text("INSERT INTO agent_conversations(id,workspace_id,user_id,agent_name,workflow_state,created_at) VALUES(:chat,:ws,:user,'Existing','{\"variables\":{\"keep\":\"YES\"}}',now())"),{'chat':chat,'ws':ws,'user':user});connection.commit()
   connection.execute(text('UPDATE agent_conversations SET live_assigned_user_id=:user WHERE id=:chat'),{'user':user,'chat':chat})
   connection.execute(text("INSERT INTO agent_messages(id,conversation_id,role,content,message_data,created_at) VALUES(:id,:chat,'assistant','Existing reply',:data,now())"),{'id':uuid.uuid4(),'chat':chat,'data':'{"source": "live_agent", "author_name": "Chris Example"}'});connection.commit()
   with connection.begin():command.upgrade(config,'head')
   with connection.begin():command.upgrade(config,'head')
   row=connection.execute(text('SELECT first_name,last_name,presence,theme FROM user_profiles')).one();assert tuple(row)==('Chris','Example','online','system');assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'keep':'YES'}}
   persona=connection.execute(text('SELECT record_id,user_id,alias_id,revision FROM conversation_personas')).one();assert tuple(persona)==(chat,user,None,1);assert connection.execute(text('SELECT content FROM agent_messages')).scalar()=='Existing reply';connection.commit()
   with connection.begin():command.downgrade(config,'0016')
   assert connection.execute(text('SELECT full_name FROM users')).scalar()=='Chris Example';connection.commit()
   with connection.begin():command.upgrade(config,'head')
   assert connection.execute(text('SELECT count(*) FROM user_profiles')).scalar()==1;connection.commit()
  finally:
   connection.rollback();connection.execute(text('SET search_path TO "'+original+'"'));connection.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));connection.commit()
