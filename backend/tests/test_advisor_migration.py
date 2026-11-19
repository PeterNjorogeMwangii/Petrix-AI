"""0025 adds only Advisor tables and preserves Perception and workspace data."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models import Workspace,User,Teamspace,PerceptionEntry,WorkspaceSettings


def test_0025_upgrade_repeat_downgrade_and_reupgrade_keep_0024(database_engine):
 schema='advisor_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
 with database_engine.connect() as c:
  original=c.execute(text('SELECT current_schema()')).scalar();c.execute(text('CREATE SCHEMA "'+schema+'"'));c.commit()
  try:
   c.execute(text('SET search_path TO "'+schema+'"'));c.commit();config.attributes['connection']=c
   with c.begin():command.upgrade(config,'0024')
   ws,user,space,entry=[uuid.uuid4() for _ in range(4)]
   with Session(c) as db:
    db.add_all([Workspace(id=ws,name='Keep Advisor'),User(id=user,email='keep-advisor@example.com')]);db.flush();db.add(Teamspace(id=space,workspace_id=ws,name='Default',name_key='default',is_default=True));db.flush()
    db.add(WorkspaceSettings(workspace_id=ws,revision=9,timezone='UTC'))
    from app.core.security import utcnow
    db.add(PerceptionEntry(id=entry,workspace_id=ws,teamspace_id=space,platform='x',external_key='keep-public-entry',content='KEEP PUBLIC CONTENT',url='https://x.com/example/status/1',posted_at=utcnow()));db.commit()
   for _ in range(2):
    with c.begin():command.upgrade(config,'0025')
   assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0025'
   assert c.execute(text('SELECT id,content FROM perception_entries')).one()==(entry,'KEEP PUBLIC CONTENT')
   assert c.execute(text('SELECT timezone,revision FROM workspace_settings')).one()==('UTC',9)
   assert len(c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename LIKE 'advisor_%'")).all())==3;c.commit()
   with c.begin():command.downgrade(config,'0024')
   assert c.execute(text('SELECT content FROM perception_entries')).scalar()=='KEEP PUBLIC CONTENT';c.commit()
   with c.begin():command.upgrade(config,'0025')
   assert c.execute(text('SELECT timezone FROM workspace_settings')).scalar()=='UTC';c.commit()
  finally:
   c.rollback();c.execute(text('SET search_path TO "'+original+'"'));c.execute(text('DROP SCHEMA "'+schema+'" CASCADE'));c.commit()
