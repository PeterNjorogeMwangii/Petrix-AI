from pathlib import Path
import uuid,json
from sqlalchemy import text
from alembic import command
from alembic.config import Config

def test_0018_indexes_legacy_snippets_once_and_retains_workflows_names_and_cross_agent_ids(database_engine):
 schema='knowledge_sources_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as connection:
  original=connection.execute(text('SELECT current_schema()')).scalar();connection.execute(text('CREATE SCHEMA "'+schema+'"'));connection.commit()
  try:
   connection.execute(text('SET search_path TO "'+schema+'"'));connection.commit();config.attributes['connection']=connection
   with connection.begin():command.upgrade(config,'0017')
   ws,a,b,identifier=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4();content='Kept policy '+('knowledge '*500);sources=[{'id':str(identifier),'name':'Existing policy','content':content,'enabled':True}]
   connection.execute(text("INSERT INTO workspaces(id,name,created_at) VALUES(:id,'Kept workspace',now())"),{'id':ws})
   for agent in [a,b]:
    connection.execute(text("INSERT INTO ai_agents(id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES(:id,:ws,'Kept agent',true,'Kept persona','[\"en\"]','en',false,'',1,5,now(),now())"),{'id':agent,'ws':ws})
    connection.execute(text("INSERT INTO agent_knowledge(agent_id,revision,sources,updated_at) VALUES(:id,7,CAST(:sources AS json),now())"),{'id':agent,'sources':json.dumps(sources)})
    connection.execute(text("INSERT INTO agent_workflows(agent_id,revision,next_node_number,nodes,updated_at) VALUES(:id,9,42,'[]',now())"),{'id':agent})
   connection.commit()
   with connection.begin():command.upgrade(config,'head')
   with connection.begin():command.upgrade(config,'head')
   assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0019'
   rows=connection.execute(text('SELECT id,legacy,content,chunks,status,enabled FROM agent_knowledge_sources')).all();assert len(rows)==2
   for row in rows:assert row.id==identifier and row.legacy and row.content==content and len(row.chunks)>=3 and row.status=='ready' and row.enabled
   assert connection.execute(text('SELECT revision FROM agent_knowledge')).scalars().all()==[7,7]
   assert connection.execute(text('SELECT revision,next_node_number FROM agent_workflows')).all()==[(9,42),(9,42)];connection.commit()
   with connection.begin():command.downgrade(config,'0017')
   assert connection.execute(text('SELECT sources FROM agent_knowledge')).scalar()==sources;connection.commit()
   with connection.begin():command.upgrade(config,'head')
   assert connection.execute(text('SELECT count(*) FROM agent_knowledge_sources')).scalar()==2;connection.commit()
  finally:
   connection.rollback();connection.execute(text('SET search_path TO "'+original+'"'));connection.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));connection.commit()
