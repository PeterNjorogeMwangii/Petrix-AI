"""Durable source jobs with leases and stale-result protection."""
import argparse,base64,json,subprocess,sys,uuid
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select,or_,and_
from app.models import Agent,AgentKnowledgeSource as Source
from app.agent_knowledge.extract import ImportFailure
from app.agent_knowledge import service as s
from app.core.security import utcnow

def parse_document(data,filename):
 try:
  result=subprocess.run([sys.executable,'-m','app.agent_knowledge.extract_worker'],input=json.dumps({'filename':filename,'data':base64.b64encode(data).decode()}).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,cwd=str(Path(__file__).resolve().parents[2]),check=False)
  parsed=json.loads(result.stdout)
  if result.returncode or parsed.get('error'):raise ImportFailure(parsed.get('error','document_invalid'))
  return parsed['text']
 except subprocess.TimeoutExpired:raise ImportFailure('parse_timeout') from None
 except ImportFailure:raise
 except Exception:raise ImportFailure('document_invalid') from None

def permitted(db,row):
 agent=db.get(Agent,row.agent_id)
 if not agent or not row.actor_id:return False
 from app.workspace_settings.permissions import allowed
 return allowed(db,agent.workspace_id,'manage_ai_agents',row.actor_id,teamspace_id=agent.teamspace_id)

def run_one(factory,reader,identifier=None,agent_id=None):
 token=uuid.uuid4();now=utcnow()
 with factory() as db:
  query=select(Source).where(or_(Source.status=='queued',and_(Source.status=='processing',Source.started_at<now-timedelta(minutes=2))))
  if identifier:query=query.where(Source.id==identifier,Source.agent_id==agent_id)
  row=db.scalar(query.order_by(Source.updated_at,Source.id).limit(1).with_for_update(skip_locked=True))
  if not row:return False
  if not permitted(db,row):row.status='failed';row.error_code='access_revoked';row.updated_at=now;row.revision+=1;db.commit();return True
  row.status='processing';row.started_at=now;row.lease_token=token;row.revision+=1
  aid,sid,kind,url,filename,data=row.agent_id,row.id,row.kind,row.source_url,row.filename,row.file_data
  db.commit()
 error='';resolved='';media=''
 try:
  if kind=='url':
   fetched=reader.fetch(url);data,filename,resolved=fetched.data,fetched.filename,fetched.final_url
  text=parse_document(data,filename);media='application/pdf' if filename.lower().endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' if filename.lower().endswith('.docx') else 'text/plain'
 except ImportFailure as e:error=e.code;text=''
 except Exception:error='import_interrupted';text=''
 with factory() as db:
  agent=db.scalar(select(Agent).where(Agent.id==aid).with_for_update())
  if not agent:return True
  row=db.scalar(select(Source).where(Source.agent_id==aid,Source.id==sid).with_for_update())
  if not row or row.lease_token!=token:return True
  if not permitted(db,row):error='access_revoked'
  if not error:
   try:s.quota(db,aid,len(text.encode()),row.size_bytes,identifier=sid)
   except Exception:error='storage_limit'
  if error:row.status='failed';row.error_code=error;row.content='';row.chunks=[];row.text_bytes=0
  else:s.set_text(row,text);row.resolved_url=resolved;row.media_type=media
  row.revision+=1;row.updated_at=utcnow();row.lease_token=None;row.started_at=None;s.bump_document(db,aid);db.commit()
 return True

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=20);args=parser.parse_args()
 if not 1<=args.limit<=100:parser.error('--limit must be 1–100')
 from app.core.config import Settings
 from app.database import create_database
 from app.agent_knowledge.web import WebsiteReader
 engine,factory=create_database(Settings().database_url.get_secret_value());count=0
 try:
  for _ in range(args.limit):
   if not run_one(factory,WebsiteReader()):break
   count+=1
 finally:engine.dispose()
 print('Processed knowledge jobs:',count)
if __name__=='__main__':main()
