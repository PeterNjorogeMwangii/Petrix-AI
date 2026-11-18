"""Revision-safe source records, bounded retrieval and legacy snippet compatibility."""
import json,math,re,uuid
from collections import Counter
from datetime import timedelta
from pathlib import PurePosixPath
from sqlalchemy import select,func,delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import defer
from fastapi import HTTPException
from app.models import Agent,AgentKnowledge,AgentKnowledgeSource as Source,AgentKnowledgePolicy as Policy,AgentChange,RateLimit
from app.core.security import utcnow,token_hash
from app.agent_knowledge import schemas as m
from app.agent_knowledge.extract import normalize,chunks,ImportFailure

ERRORS={
 'no_text':'No readable text was found. Scanned PDFs need OCR before upload.',
 'text_too_large':'Extracted text exceeds 256 KB. Split this document into smaller files.',
 'file_too_large':'Choose a file no larger than 10 MB.',
 'empty_file':'The selected file is empty.',
 'unsupported_document':'Use TXT, Markdown, CSV, JSON, HTML, PDF or DOCX.',
 'document_invalid':'The document could not be read. Export a fresh, supported copy.',
 'invalid_pdf':'This is not a readable PDF file.',
 'invalid_docx':'This is not a readable DOCX file.',
 'encrypted_pdf':'Remove the PDF password before uploading.',
 'too_many_pages':'PDF files support up to 200 pages. Split this file.',
 'document_too_complex':'This document exceeds the parser limits. Export a simpler text copy.',
 'text_encoding':'Save text files as UTF-8 or UTF-16 with a byte-order mark.',
 'website_challenge':'The website returned a browser challenge. Ask its owner to allow the Petrix server IP, or upload its content.',
 'website_blocked':'The website blocked this request. Review its access rules or upload the content.',
 'blocked_endpoint':'Use a public HTTPS website. Private, local and metadata addresses are blocked.',
 'fetch_timeout':'The website took too long. Try again or upload the page text.',
 'parse_timeout':'The document took too long to read. Export a simpler text copy.',
 'website_unavailable':'The website could not be read. Check the URL and try again.',
 'website_invalid':'The website returned an invalid response.',
 'unsupported_website':'The URL must return HTML, plain text or a PDF.',
 'unsupported_encoding':'The website returned an unsupported content encoding.',
 'redirect_limit':'The website redirects too many times. Enter its final HTTPS URL.',
 'robots_disallowed':'The website disallows this reader in robots.txt. Ask its owner for access or upload the content.',
 'robots_unavailable':'robots.txt could not be checked. Retry later or upload the content.',
 'response_too_large':'The downloaded response exceeds 10 MB.',
 'storage_limit':'This agent has reached its knowledge text limit. Remove or shorten other sources.',
 'access_revoked':'The user who queued this import no longer has permission to manage this agent.',
 'import_interrupted':'The import was interrupted. Retry it.',
}

def revision(row,expected):
 if row.revision!=expected:raise HTTPException(409,'This source changed. Reload before saving.')

def clean_name(value):
 value=value.strip()
 if not value or len(value)>100 or any(ord(c)<32 for c in value):raise HTTPException(422,'Enter a name of 1–100 characters without control characters.')
 return value

def throttle(db,user_id):
 now=utcnow();key=token_hash('knowledge-import:'+str(user_id)+':'+str(int(now.timestamp())//60))
 count=db.execute(insert(RateLimit).values(key=key,count=1,expires_at=now+timedelta(minutes=2)).on_conflict_do_update(index_elements=[RateLimit.key],set_={'count':RateLimit.count+1}).returning(RateLimit.count)).scalar_one()
 if count>20:raise HTTPException(429,'Import or refresh up to 20 knowledge sources per minute.')

def payload(row,content=False):
 value={k:getattr(row,k) for k in ['id','name','kind','enabled','status','source_url','resolved_url','filename','media_type','size_bytes','text_bytes','revision','created_at','updated_at']}
 value['error_code']=row.error_code;value['error']=ERRORS.get(row.error_code,'The source could not be processed.') if row.error_code else ''
 if content:value['content']=row.content;value['chunk_count']=len(row.chunks)
 return value

def source_for(db,agent_id,identifier,lock=False):
 q=select(Source).options(defer(Source.file_data)).where(Source.agent_id==agent_id,Source.id==identifier)
 if lock:q=q.with_for_update().execution_options(populate_existing=True)
 row=db.scalar(q)
 if not row:raise HTTPException(404,'Knowledge source not found.')
 return row

def audit(db,agent,actor_id,action,summary):
 db.add(AgentChange(workspace_id=agent.workspace_id,agent_id=agent.id,agent_name=agent.name,actor_id=actor_id,action=action,summary=summary))

def bump_document(db,agent_id,legacy_id=None,removed=False,source=None):
 db.execute(insert(AgentKnowledge).values(agent_id=agent_id,revision=1,sources=[],updated_at=utcnow()).on_conflict_do_nothing())
 document=db.scalar(select(AgentKnowledge).where(AgentKnowledge.agent_id==agent_id).with_for_update().execution_options(populate_existing=True))
 if legacy_id:
  old=document.sources;updated=[]
  for value in old:
   if value['id']!=str(legacy_id):updated.append(value)
   elif not removed and source:updated.append({'id':str(source.id),'name':source.name,'content':source.content,'enabled':source.enabled})
  document.sources=updated
 document.revision+=1;document.updated_at=utcnow()

def quota(db,agent_id,text_bytes=0,file_bytes=0,identifier=None,new=False):
 rows=db.execute(select(Source.id,Source.text_bytes,Source.size_bytes).where(Source.agent_id==agent_id)).all()
 if (new and len(rows)>=m.MAX_SOURCES) or (not new and len(rows)>m.MAX_SOURCES):raise HTTPException(409,'An agent supports up to 50 knowledge sources.')
 others=[v for v in rows if v.id!=identifier]
 if sum(v.text_bytes for v in others)+text_bytes>m.MAX_AGENT_TEXT:raise HTTPException(409,'Keep extracted knowledge text within 2 MB per agent.')
 if sum(v.size_bytes for v in others)+file_bytes>m.MAX_AGENT_FILES:raise HTTPException(409,'Keep original knowledge files within 50 MB per agent.')

def set_text(row,text):
 row.content=normalize(text);row.chunks=chunks(row.content);row.text_bytes=len(row.content.encode('utf-8'));row.status='ready';row.error_code='';row.lease_token=None;row.started_at=None;row.updated_at=utcnow()

def sync_legacy(db,agent,sources):
 old=list(db.scalars(select(Source).where(Source.agent_id==agent.id,Source.legacy.is_(True)).with_for_update()))
 ids={uuid.UUID(str(v['id'])) for v in sources}
 for row in old:
  if row.id not in ids:db.delete(row)
 for item in sources:
  identifier=uuid.UUID(str(item['id']));row=db.get(Source,(agent.id,identifier))
  if row and not row.legacy:raise HTTPException(409,'This source ID belongs to the updated Knowledge editor.')
  if not row:row=Source(agent_id=agent.id,id=identifier,kind='snippet',legacy=True,name=item['name']);db.add(row)
  else:row.revision+=1
  row.name=item['name'];row.enabled=item['enabled'];set_text(row,item['content'])
 db.flush();quota(db,agent.id)

def create_snippet(db,agent,actor_id,body):
 name=clean_name(body.name);existing=db.get(Source,(agent.id,body.id))
 if existing:
  if existing.kind=='snippet' and existing.name==name and existing.content==body.content.strip() and existing.enabled==body.enabled:return existing
  raise HTTPException(409,'This source request ID was already used.')
 text=normalize(body.content);quota(db,agent.id,len(text.encode()),new=True)
 row=Source(agent_id=agent.id,id=body.id,kind='snippet',name=name,enabled=body.enabled,actor_id=actor_id);set_text(row,text);db.add(row);bump_document(db,agent.id);audit(db,agent,actor_id,'knowledge_source_added','Knowledge snippet added.');db.commit();return row

def create_import(db,agent,actor_id,identifier,name,kind,enabled=True,url='',filename='',data=None):
 name=clean_name(name);row=db.get(Source,(agent.id,identifier))
 if row:
  if row.kind==kind and row.name==name and row.source_url==url and row.enabled==enabled and (kind!='file' or row.file_data==data):
   db.commit();return row
  raise HTTPException(409,'This source request ID was already used.')
 quota(db,agent.id,file_bytes=len(data or b''),new=True)
 row=Source(agent_id=agent.id,id=identifier,kind=kind,name=name,enabled=enabled,actor_id=actor_id,source_url=url,filename=filename,file_data=data,size_bytes=len(data or b''))
 db.add(row);bump_document(db,agent.id);audit(db,agent,actor_id,'knowledge_source_added','Knowledge '+kind+' queued.');db.commit();return row

def queue(row):
 row.status='queued';row.content='';row.chunks=[];row.text_bytes=0;row.error_code='';row.started_at=None;row.lease_token=None;row.updated_at=utcnow();row.revision+=1

def edit(db,agent,actor_id,row,body):
 revision(row,body.expected_revision)
 if row.status=='processing':raise HTTPException(409,'This source is processing. Reload after it finishes, or delete it to cancel.')
 if body.content is not None and row.kind!='snippet':raise HTTPException(422,'Only snippets have editable text. Re-upload a file or refresh its URL.')
 if body.url is not None and row.kind!='url':raise HTTPException(422,'Only URL sources have a website URL.')
 row.name=clean_name(body.name);row.enabled=body.enabled
 if body.content is not None:
  text=normalize(body.content);quota(db,agent.id,len(text.encode()),identifier=row.id);set_text(row,text)
 if body.url is not None and row.source_url!=body.url:row.source_url=body.url;queue(row)
 row.revision+=1;row.updated_at=utcnow();bump_document(db,agent.id,row.id if row.legacy else None,source=row)
 audit(db,agent,actor_id,'knowledge_source_updated','Knowledge source updated.');db.commit();return row

def remove(db,agent,actor_id,body):
 if len(set(body.ids))!=len(body.ids) or set(body.revisions)!=set(body.ids):raise HTTPException(422,'Send one revision for each selected source.')
 rows=list(db.scalars(select(Source).where(Source.agent_id==agent.id,Source.id.in_(body.ids)).order_by(Source.id).with_for_update()))
 if len(rows)!=len(body.ids):raise HTTPException(404,'A selected source was not found.')
 for row in rows:revision(row,body.revisions[row.id])
 for row in rows:
  if row.legacy:bump_document(db,agent.id,row.id,removed=True)
  db.delete(row)
 bump_document(db,agent.id);audit(db,agent,actor_id,'knowledge_sources_deleted',str(len(rows))+' knowledge source(s) deleted.');db.commit()

def retrieve(db,agent_id,query,maximum=24000):
 rows=db.execute(select(Source.id,Source.name,Source.source_url,Source.chunks).where(Source.agent_id==agent_id,Source.enabled.is_(True),Source.status=='ready').order_by(Source.created_at,Source.id)).all()
 terms=set(re.findall(r'\w+',query.casefold()))-{'a','an','the','is','are','to','of','and','in','i','you','what','how','do','for','it','please'}
 candidates=[];frequency=Counter();prepared=[]
 for row in rows:
  for number,text in enumerate(row.chunks):
   counts=Counter(re.findall(r'\w+',text.casefold()));frequency.update(set(counts)&terms);prepared.append((row,number,text,counts))
 total=max(len(prepared),1)
 for row,number,text,counts in prepared:
  score=sum((1+math.log(counts[t]))*math.log(1+total/(1+frequency[t])) for t in terms if counts[t])
  candidates.append((score,row,number,text))
 if any(v[0]>0 for v in candidates):candidates.sort(key=lambda v:(-v[0],str(v[1].id),v[2]))
 else:candidates=[v for v in candidates if v[2]==0]
 selected=[];size=0
 for _,row,number,text in candidates:
  item={'id':str(row.id),'name':row.name,'content':text,'chunk':number+1,'url':row.source_url};cost=len(json.dumps(item,ensure_ascii=False).encode())
  if size+cost>maximum:continue
  selected.append(item);size+=cost
  if len(selected)>=12:break
 return selected

def policy_payload(db,agent_id):
 row=db.get(Policy,agent_id)
 return {'revision':row.revision if row else 1,'master_prompt':row.master_prompt if row else '', 'use_for_replies':row.use_for_replies if row else True,'fallback_message':row.fallback_message if row else "Sorry, I can't respond to that request."}
