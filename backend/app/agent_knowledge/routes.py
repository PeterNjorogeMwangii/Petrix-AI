import uuid
from pathlib import PurePosixPath
from urllib.parse import quote
from typing import Optional,Literal
from fastapi import APIRouter,Depends,Request,BackgroundTasks,UploadFile,File,Form,HTTPException,Query,Response
from sqlalchemy import select,func,or_
from sqlalchemy.orm import Session,defer
from app.database import get_db
from app.routes.agents import access
from app.models import AgentKnowledgeSource as Source,AgentKnowledgePolicy as Policy
from app.agent_knowledge import service as s,schemas as m,jobs
from app.core.security import utcnow

router=APIRouter(prefix='/api/workspaces/{workspace_id}/agents/{agent_id}/knowledge',tags=['Agent Knowledge base'])

def background(tasks,request,row):
 if row.status=='queued':tasks.add_task(jobs.run_one,request.app.state.session_factory,request.app.state.agent_knowledge_reader,row.id,row.agent_id)

def result(db,row):return s.payload(row,True)

@router.get('/sources')
def listing(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,q:str=Query('',max_length=100),kind:Optional[Literal['snippet','url','file']]=None,status:Optional[Literal['ready','queued','processing','failed','disabled']]=None,page:int=Query(1,ge=1),per_page:int=Query(40,ge=1,le=100),db:Session=Depends(get_db)):
 access(request,db,workspace_id,agent_id=agent_id)
 query=select(Source).options(defer(Source.content),defer(Source.chunks),defer(Source.file_data)).where(Source.agent_id==agent_id)
 if kind:query=query.where(Source.kind==kind)
 if status:query=query.where(Source.enabled.is_(False)) if status=='disabled' else query.where(Source.enabled.is_(True),Source.status==status)
 if q.strip():
  pattern='%'+q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
  query=query.where(or_(*[column.ilike(pattern,escape='\\') for column in [Source.name,Source.filename,Source.source_url,Source.content]]))
 total=db.scalar(select(func.count()).select_from(query.subquery()));rows=db.scalars(query.order_by(Source.updated_at.desc(),Source.id).offset((page-1)*per_page).limit(per_page)).all()
 stats=db.execute(select(Source.status,Source.enabled,func.count()).where(Source.agent_id==agent_id).group_by(Source.status,Source.enabled)).all()
 return {'items':[s.payload(v) for v in rows],'total':total,'page':page,'per_page':per_page,'stats':[{'status':a if enabled else 'disabled','count':count} for a,enabled,count in stats],'limits':{'sources':m.MAX_SOURCES,'file_bytes':m.MAX_FILE_BYTES,'text_bytes':m.MAX_AGENT_TEXT,'original_bytes':m.MAX_AGENT_FILES}}

@router.get('/sources/{identifier}')
def detail(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 access(request,db,workspace_id,agent_id=agent_id);return result(db,s.source_for(db,agent_id,identifier))

@router.post('/snippets',status_code=201)
def snippet(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:m.SnippetBody,request:Request,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);s.throttle(db,session.user_id);return result(db,s.create_snippet(db,agent,session.user_id,body))

@router.post('/urls',status_code=201)
def url(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:m.URLBody,request:Request,tasks:BackgroundTasks,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);s.throttle(db,session.user_id);row=s.create_import(db,agent,session.user_id,body.id,body.name,'url',body.enabled,url=body.url);value=result(db,row);background(tasks,request,row);return value

@router.post('/files',status_code=201)
async def upload(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,tasks:BackgroundTasks,id:uuid.UUID=Form(...),file:UploadFile=File(...),name:str=Form(''),db:Session=Depends(get_db)):
 access(request,db,workspace_id,True,agent_id,lock=False)
 filename=PurePosixPath((file.filename or '').replace('\\','/')).name
 if not filename or len(filename)>250 or any(ord(c)<32 for c in filename) or PurePosixPath(filename.lower()).suffix not in m.EXTENSIONS:raise HTTPException(422,'Choose a TXT, Markdown, CSV, JSON, HTML, PDF or DOCX file.')
 data=await file.read(m.MAX_FILE_BYTES+1)
 if not data or len(data)>m.MAX_FILE_BYTES:raise HTTPException(413,'Choose a non-empty file up to 10 MB.')
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);s.throttle(db,session.user_id);row=s.create_import(db,agent,session.user_id,id,name or filename[:100],'file',filename=filename,data=data);value=result(db,row);background(tasks,request,row);return value

@router.post('/sources/{identifier}')
def edit(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:m.SourceEdit,request:Request,tasks:BackgroundTasks,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);row=s.source_for(db,agent_id,identifier,True);row=s.edit(db,agent,session.user_id,row,body);value=result(db,row);background(tasks,request,row);return value

@router.post('/sources/{identifier}/refresh')
def refresh(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:m.SourceRevision,request:Request,tasks:BackgroundTasks,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);row=s.source_for(db,agent_id,identifier,True);s.revision(row,body.expected_revision)
 if row.kind=='snippet':raise HTTPException(422,'Save snippet edits to update their knowledge.')
 if row.status=='processing' and row.started_at and (utcnow()-row.started_at).total_seconds()<120:raise HTTPException(409,'This source is still processing.')
 s.throttle(db,session.user_id);s.queue(row);row.actor_id=session.user_id;s.bump_document(db,agent_id);s.audit(db,agent,session.user_id,'knowledge_source_refreshed','Knowledge source queued for refresh.');db.commit();value=result(db,row);background(tasks,request,row);return value

@router.post('/sources-delete')
def remove(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:m.DeleteSources,request:Request,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);s.remove(db,agent,session.user_id,body);return {'deleted':len(body.ids)}

@router.get('/sources/{identifier}/download')
def download(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 access(request,db,workspace_id,agent_id=agent_id);row=s.source_for(db,agent_id,identifier)
 if row.kind!='file' or not row.file_data:raise HTTPException(404,'This source has no original file.')
 return Response(row.file_data,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.filename,safe=''),'X-Content-Type-Options':'nosniff','Cache-Control':'private, no-store'})

@router.get('/guidance')
def guidance(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 access(request,db,workspace_id,agent_id=agent_id)
 from app.agent_knowledge.guidance import COMPLAINT_PROMPT,MASTER_PROMPT
 moderator=request.app.state.agent_moderator;settings=request.app.state.settings
 return {'policy':s.policy_payload(db,agent_id),'complaint_prompt':COMPLAINT_PROMPT,'master_example':MASTER_PROMPT,'moderation':{'configured':moderator.configured,'required':moderator.required,'secret_filter':True},'egress_ip':settings.knowledge_egress_ip}

@router.post('/guidance')
def save_guidance(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:m.PolicyBody,request:Request,db:Session=Depends(get_db)):
 session,_,_,agent=access(request,db,workspace_id,True,agent_id);row=db.get(Policy,agent_id)
 if body.expected_revision!=(row.revision if row else 1):raise HTTPException(409,'Guidance changed. Reload before saving.')
 if not row:row=Policy(agent_id=agent_id,revision=1);db.add(row)
 row.master_prompt=body.master_prompt;row.use_for_replies=body.use_for_replies;row.fallback_message=body.fallback_message;row.revision+=1;row.updated_at=utcnow();s.audit(db,agent,session.user_id,'knowledge_guidance_saved','Knowledge reply guidance saved.');db.commit();return s.policy_payload(db,agent_id)
