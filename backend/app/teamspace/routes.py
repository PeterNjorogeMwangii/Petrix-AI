from datetime import date
import io,json,uuid,zipfile,xml.etree.ElementTree as ET
from html.parser import HTMLParser
from datetime import timedelta
from typing import Optional,Literal
from fastapi import APIRouter,Depends,Request,HTTPException,Query,Response,UploadFile,File,Form
from sqlalchemy import select,func,delete
from sqlalchemy.orm import Session
from pydantic import ValidationError
from app.database import get_db
from app.auth_service import current_session,require_csrf
from app.core.security import utcnow
from app.models import *
from app.workspace_settings import permissions as p
from app.teamspace import schemas as s,configuration as c,records as r,jobs

router=APIRouter(prefix='/api/workspaces/{workspace_id}',tags=['Teamspace operations'])
KINDS=Literal['analysis','copilot','assignment','enforcement','mandatory','overdue','analytics','profile_iframe']
MODELS={'analysis':s.AnalysisConfig,'copilot':s.CopilotConfig,'assignment':s.AssignmentConfig,'enforcement':s.EnforcementConfig,'mandatory':s.MandatoryConfig,'overdue':s.OverdueConfig,'analytics':s.EmbedConfig,'profile_iframe':s.EmbedConfig}

def actor(request,db,workspace_id,mutation=False):
 session=current_session(request,db)
 if mutation:require_csrf(request,session)
 from app.agent_service import workspace_for
 workspace_for(db,workspace_id,session.user_id)
 return session

def require_config(db,workspace_id,space_id,kind,user_id,manage=False):
 c.space_for(db,workspace_id,space_id)
 if kind=='copilot' and p.allowed(db,workspace_id,'manage_ai_agents',user_id,teamspace_id=space_id):return
 p.require(db,workspace_id,c.PERMISSIONS[kind][1 if manage else 0],user_id,teamspace_id=space_id)

def model_body(model,body):
 try:return model.model_validate(body)
 except ValidationError as error:
  issue=error.errors(include_input=False,include_context=False,include_url=False)[0];raise HTTPException(422,'.'.join(map(str,issue['loc']))+': '+issue['msg']) from None

def row_payload(row):return {column.name:getattr(row,column.name) for column in row.__table__.columns if column.name not in {'workspace_id','name_key','teamspace_id'}}

@router.get('/teamspace-operations/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id)
 return {'teamspaces':[{'id':str(t.id),'name':t.name,'is_default':t.is_default} for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace_id)) if str(t.id) in p.accessible_spaces(db,workspace_id,session.user_id)],'default_id':str(p.default_space(db,workspace_id))}

@router.get('/teamspace-operations/{space_id}/settings/{kind}')
def settings(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:KINDS,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);require_config(db,workspace_id,space_id,kind,session.user_id)
 value=c.operations(db,workspace_id,space_id);data=value.enforcement if kind=='mandatory' else getattr(value,kind)
 if kind=='enforcement':data={key:data[key] for key in ['enabled','minimum','sort']}
 from app.tickets.settings import active,payload
 start=utcnow().replace(day=1,hour=0,minute=0,second=0,microsecond=0)
 counts={}
 if kind=='analysis':
  for target in ['chats','tickets','people']:
   counts[target]={mode:db.scalar(select(func.count()).select_from(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.teamspace_id==space_id,SupportJob.kind=='analysis',SupportJob.target_kind==target,SupportJob.status=='completed',SupportJob.finished_at>=start,SupportJob.payload['mode'].as_string()==mode)) or 0 for mode in ['auto','manual']}
 db.commit()
 return {'config':data,'revision':value.revision,'can_manage':p.allowed(db,workspace_id,c.PERMISSIONS[kind][1],session.user_id,teamspace_id=space_id) or kind=='copilot' and p.allowed(db,workspace_id,'manage_ai_agents',session.user_id,teamspace_id=space_id),'provider_ready':request.app.state.support_ai.available,'provider_model':request.app.state.settings.support_ai_model or None,'provider_models':sorted({v.strip() for v in request.app.state.settings.support_ai_models.split(',') if v.strip()}|({request.app.state.settings.support_ai_model} if request.app.state.settings.support_ai_model else set())),'counts':counts,'limits':{key:c.limit(db,workspace_id,key) for key in ['analysis_auto','analysis_manual','ban_rules','canned_replies','tags','tag_groups']},'plan_rank':c.plan_rank(db,workspace_id,request.app.state.settings),'options':{'tags':[payload(t) for t in active(db,workspace_id,'tags')],'groups':[payload(g) for g in active(db,workspace_id,'groups')],'fields':[row_payload(f) for f in r.field_definitions(db,workspace_id,'conversation')],'users':[{'id':str(u.id),'name':u.full_name or u.email} for u in db.scalars(select(User).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id))],'teams':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))]}}

@router.post('/teamspace-operations/{space_id}/settings/{kind}')
def save_settings(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:KINDS,body:dict,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);require_config(db,workspace_id,space_id,kind,session.user_id,True)
 model=model_body(MODELS[kind],body);row=c.save_config(db,workspace_id,space_id,kind,model,request.app.state.settings)
 return {'revision':row.revision,'saved':True}

ITEMS={'ban-rules':(BanRule,s.RuleBody,'bans','ban_rules'),'ban-reasons':(BanReason,s.ReasonBody,'bans','ban_reasons'),'canned-replies':(CannedReply,s.CannedBody,'canned_replies','canned_replies')}
ItemKind=Literal['ban-rules','ban-reasons','canned-replies']

@router.get('/teamspace-operations/{space_id}/items/{kind}')
def items(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:ItemKind,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);model,_,key,cap=ITEMS[kind];p.require(db,workspace_id,'view_'+key,session.user_id,teamspace_id=space_id);c.space_for(db,workspace_id,space_id)
 return {'items':[row_payload(row) for row in db.scalars(select(model).where(model.workspace_id==workspace_id,model.teamspace_id==space_id).order_by(model.id))],'can_manage':p.allowed(db,workspace_id,'manage_'+key,session.user_id,teamspace_id=space_id),'limit':c.limit(db,workspace_id,cap)}

def save_item(db,workspace_id,space_id,kind,body,identifier=None):
 model,schema,_,cap=ITEMS[kind];body=model_body(schema,body)
 db.scalar(select(Workspace).where(Workspace.id==workspace_id).with_for_update())
 row=db.scalar(select(model).where(model.workspace_id==workspace_id,model.teamspace_id==space_id,model.id==identifier).with_for_update()) if identifier else None
 if identifier and not row:raise HTTPException(404,'Setting not found.')
 if row:c.checked_revision(row,body.expected_revision)
 else:c.check_count(db,workspace_id,cap,db.scalar(select(func.count()).select_from(model).where(model.workspace_id==workspace_id)) or 0)
 data=body.model_dump(exclude={'expected_revision'})
 if kind=='ban-rules':
  if db.scalar(select(model.id).where(model.teamspace_id==space_id,model.offences==body.offences,model.id!=(identifier or uuid.uuid4()))):raise HTTPException(409,'A rule for this offence count already exists.')
 else:
  from app.workspace_settings.routes import name_key
  data['name']=data['name'].strip();data['name_key']=name_key(data['name'])
  if not data['name_key']:raise HTTPException(422,'Enter a name.')
  if db.scalar(select(model.id).where(model.teamspace_id==space_id,model.name_key==data['name_key'],model.id!=(identifier or uuid.uuid4()))):raise HTTPException(409,'This name is already used.')
 if row:
  for key,value in data.items():setattr(row,key,value)
  row.revision+=1
 else:row=model(workspace_id=workspace_id,teamspace_id=space_id,**data);db.add(row)
 db.commit();return row_payload(row)

@router.post('/teamspace-operations/{space_id}/items/{kind}',status_code=201)
def add_item(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:ItemKind,body:dict,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);p.require(db,workspace_id,'manage_'+ITEMS[kind][2],session.user_id,teamspace_id=space_id);c.space_for(db,workspace_id,space_id);return save_item(db,workspace_id,space_id,kind,body)

@router.post('/teamspace-operations/{space_id}/items/{kind}/{identifier}')
def edit_item(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:ItemKind,identifier:uuid.UUID,body:dict,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);p.require(db,workspace_id,'manage_'+ITEMS[kind][2],session.user_id,teamspace_id=space_id);return save_item(db,workspace_id,space_id,kind,body,identifier)

@router.post('/teamspace-operations/{space_id}/items-delete/{kind}')
def delete_items(workspace_id:uuid.UUID,space_id:uuid.UUID,kind:ItemKind,body:s.Selection,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);model,_,key,_=ITEMS[kind];p.require(db,workspace_id,'manage_'+key,session.user_id,teamspace_id=space_id)
 rows=list(db.scalars(select(model).where(model.workspace_id==workspace_id,model.teamspace_id==space_id,model.id.in_(body.ids)).with_for_update()))
 if len(rows)!=len(set(body.ids)):raise HTTPException(404,'A selected item was not found.')
 for row in rows:db.delete(row)
 db.commit();return {'deleted':len(rows)}

class TextOnly(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.skip=0
 def handle_starttag(self,tag,attrs):
  if tag in {'script','style','noscript'}:self.skip+=1
 def handle_endtag(self,tag):
  if tag in {'script','style','noscript'}:self.skip=max(0,self.skip-1)
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)

def knowledge_text(name,data):
 if len(data)>5*1024*1024:raise HTTPException(422,'Upload a text or DOCX file up to 5 MiB.')
 if name.lower().endswith('.pdf'):
  try:
   from app.agent_knowledge.extract import extract
   return extract(data,name)
  except ValueError:raise HTTPException(422,'Upload a readable, unencrypted PDF up to 5 MiB.') from None
 if name.lower().endswith('.docx'):
  try:
   with zipfile.ZipFile(io.BytesIO(data)) as archive:
    info=archive.getinfo('word/document.xml')
    if info.file_size>2*1024*1024:raise ValueError
    raw=archive.read(info)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError
    root=ET.fromstring(raw);value='\n'.join(''.join(p.itertext()) for p in root.iter() if p.tag.endswith('}p'))
  except (ValueError,KeyError,zipfile.BadZipFile,ET.ParseError):raise HTTPException(422,'Upload a valid DOCX document.') from None
 else:
  if not name.lower().endswith(('.txt','.md','.csv','.json','.html','.htm')):raise HTTPException(422,'Supported files: TXT, Markdown, CSV, JSON, HTML, and DOCX.')
  try:value=data.decode('utf-8-sig')
  except UnicodeError:raise HTTPException(422,'Use UTF-8 text files.') from None
 if name.lower().endswith(('.html','.htm')):
  parser=TextOnly();parser.feed(value);value=' '.join(parser.parts)
 if not value.strip() or '\x00' in value or len(value)>200000:raise HTTPException(422,'Add readable content up to 200,000 characters.')
 return value.strip()

@router.get('/teamspace-operations/{space_id}/knowledge')
def knowledge(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);require_config(db,workspace_id,space_id,'copilot',session.user_id)
 return {'items':[{'id':str(row.id),'name':row.name,'kind':row.kind,'url':row.url,'revision':row.revision,'updated_at':row.updated_at,'status':row.status,'error':row.error,'options':row.options,'next_refresh_at':row.next_refresh_at,'characters':len(row.content)} for row in db.scalars(select(CopilotKnowledge).where(CopilotKnowledge.workspace_id==workspace_id,CopilotKnowledge.teamspace_id==space_id))]}

@router.post('/teamspace-operations/{space_id}/knowledge',status_code=201)
async def add_knowledge(workspace_id:uuid.UUID,space_id:uuid.UUID,request:Request,name:str=Form(...),kind:Literal['snippet','url','file']=Form(...),content:str=Form(''),url:str=Form(''),scope:Literal['page','all']=Form('page'),auto_retrain:bool=Form(False),retrain_days:int=Form(7,ge=1,le=90),file:Optional[UploadFile]=File(None),db:Session=Depends(get_db)):
 from starlette.concurrency import run_in_threadpool
 def authorize():
  session=actor(request,db,workspace_id,True);require_config(db,workspace_id,space_id,'copilot',session.user_id,True);db.rollback()
 await run_in_threadpool(authorize)
 file_bytes=await file.read(5*1024*1024+1) if kind=='file' and file else None
 def persist():
  nonlocal content
  session=actor(request,db,workspace_id,True);require_config(db,workspace_id,space_id,'copilot',session.user_id,True)
  from app.workspace_settings.routes import name_key
  if not name.strip() or len(name)>200:raise HTTPException(422,'Enter a source name of 1–200 characters.')
  if db.scalar(select(CopilotKnowledge.id).where(CopilotKnowledge.teamspace_id==space_id,CopilotKnowledge.name_key==name_key(name))):raise HTTPException(409,'This source name is already used.')
  if (db.scalar(select(func.count()).select_from(CopilotKnowledge).where(CopilotKnowledge.teamspace_id==space_id)) or 0)>=100:raise HTTPException(422,'A teamspace supports up to 100 knowledge sources.')
  if kind=='file':
   if not file:raise HTTPException(422,'Select a file.')
   content=knowledge_text(file.filename or '',file_bytes)
  elif kind=='url' and scope=='page' and not auto_retrain:
   if not url or len(url)>2048:raise HTTPException(422,'Enter a public HTTPS URL up to 2,048 characters.')
   result=request.app.state.knowledge_fetcher.send('GET',url,{'Accept':'text/html, text/plain, application/json'},None,15)
   if result.outcome!='success' or not isinstance(result.body,(str,dict,list)):raise HTTPException(422,'The URL could not be read. Use a public HTTPS page without redirects or sign-in.')
   raw=result.body if isinstance(result.body,str) else json.dumps(result.body,ensure_ascii=False)
   parser=TextOnly();parser.feed(raw);content=' '.join(parser.parts) if '<' in raw else raw
  deferred=kind=='url' and (scope=='all' or auto_retrain)
  if deferred:
   try:__import__('app.agent_knowledge.schemas',fromlist=['url_value']).url_value(url)
   except ValueError:raise HTTPException(422,'Enter a public HTTPS URL without credentials.') from None
  if not deferred and (not content.strip() or len(content)>200000 or '\x00' in content):raise HTTPException(422,'Add readable content up to 200,000 characters.')
  row=CopilotKnowledge(workspace_id=workspace_id,teamspace_id=space_id,name=name.strip(),name_key=name_key(name),kind=kind,url=url if kind=='url' else '',content=content.strip(),options={'scope':scope,'auto_retrain':auto_retrain,'retrain_days':retrain_days,'actor_id':str(session.user_id)},status='pending' if deferred else 'success');db.add(row);db.commit()
  if deferred:
   from app.inbox.knowledge import queue
   queue(db,row,session.user_id)
  return {'id':str(row.id),'name':row.name,'status':row.status}
 return await run_in_threadpool(persist)

@router.post('/teamspace-operations/{space_id}/knowledge-delete')
def remove_knowledge(workspace_id:uuid.UUID,space_id:uuid.UUID,body:s.Selection,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);require_config(db,workspace_id,space_id,'copilot',session.user_id,True)
 rows=list(db.scalars(select(CopilotKnowledge).where(CopilotKnowledge.workspace_id==workspace_id,CopilotKnowledge.teamspace_id==space_id,CopilotKnowledge.id.in_(body.ids))))
 if len(rows)!=len(set(body.ids)):raise HTTPException(404,'A selected source was not found.')
 for row in rows:db.delete(row)
 db.commit();return {'deleted':len(rows)}

@router.post('/support/taxonomy-delete/{kind}')
def remove_taxonomy(workspace_id:uuid.UUID,kind:Literal['tags','groups'],body:s.Selection,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);p.require(db,workspace_id,'manage_tags',session.user_id,teamspace_id=p.default_space(db,workspace_id))
 from app.tickets import settings
 rows=[settings.get(db,workspace_id,kind,identifier) for identifier in sorted(set(body.ids))]
 for row in rows:
  if settings.references(db,workspace_id,kind,row.id):raise HTTPException(409,'Remove selected tags/groups from Create Ticket actions before deleting them.')
 for row in rows:settings.remove(db,workspace_id,session.user_id,kind,row.id,row.revision,False)
 db.commit();return {'deleted':len(rows)}

@router.get('/support/filter-options')
def filters(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id)
 return r.filter_options(db,workspace_id,kind,session.user_id)

@router.get('/support/{kind}/{identifier}/details')
def record_details(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);row=r.record(db,workspace_id,kind,identifier,session.user_id);space_id=r.record_space(db,kind,row);ops=c.operations(db,workspace_id,space_id)
 person=row if kind=='people' else db.get(SupportPerson,row.person_id) if row.person_id else None
 from app.tickets.settings import active,payload
 latest=db.scalar(select(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.teamspace_id==space_id,SupportJob.kind=='analysis',SupportJob.target_kind==kind,SupportJob.target_id==identifier,SupportJob.status=='completed').order_by(SupportJob.finished_at.desc()).limit(1)) if p.allowed(db,workspace_id,'view_ai_analysis',session.user_id,teamspace_id=space_id) else None
 can_manage=True
 try:r.record(db,workspace_id,kind,identifier,session.user_id,'manage')
 except HTTPException:can_manage=False
 fields=r.field_definitions(db,workspace_id,'person' if kind=='people' else 'conversation')
 values=dict(row.custom_fields or {})
 from app.custom_fields.service import option_snapshot
 for field in fields:
  if field.key not in values and field.default_value is not None:values[field.key]=option_snapshot(field,field.default_value,'default')
 people_fields=kind!='people' or p.allowed(db,workspace_id,'view_people_custom_fields',session.user_id,teamspace_id=space_id)
 sort=ops.enforcement.get('sort','a_z')
 tag_options=active(db,workspace_id,'tags');tag_options.sort(key=(lambda t:t.created_at) if sort=='newest' else (lambda t:t.name.casefold()),reverse=sort in {'z_a','newest'})
 db.commit()
 return {'id':str(identifier),'teamspace_id':str(space_id),'kind':kind,'revision':row.live_revision if kind=='chats' else row.revision,'labels':r.label_payload(db,workspace_id,kind,row) if kind!='people' or p.allowed(db,workspace_id,'view_people_tags',session.user_id,teamspace_id=space_id) else None,'person':r.person_payload(db,person,session.user_id) if person else None,'fields':[row_payload(f) for f in fields] if people_fields else [],'values':values if people_fields else {},'tags':[payload(t) for t in tag_options],'groups':[payload(t) for t in active(db,workspace_id,'groups')],'reasons':[row_payload(t) for t in db.scalars(select(BanReason).where(BanReason.workspace_id==workspace_id,BanReason.teamspace_id==space_id))] if p.allowed(db,workspace_id,'manage_bans',session.user_id,teamspace_id=space_id) else [],'canned_replies':[row_payload(t) for t in db.scalars(select(CannedReply).where(CannedReply.teamspace_id==space_id))] if kind=='chats' and p.allowed(db,workspace_id,'view_canned_replies',session.user_id,teamspace_id=space_id) else [],'analysis':jobs.job_payload(latest) if latest else None,'can_view_analysis':p.allowed(db,workspace_id,'view_ai_analysis',session.user_id,teamspace_id=space_id),'can_tags':can_manage and (kind!='people' or p.allowed(db,workspace_id,'manage_people_tags',session.user_id,teamspace_id=space_id)),'can_analyze':p.allowed(db,workspace_id,'trigger_ai_analysis',session.user_id,teamspace_id=space_id) and ops.analysis.get(kind,{}).get('enabled',False) and ops.analysis.get(kind,{}).get('manual',False),'can_copilot':kind=='chats' and p.allowed(db,workspace_id,'view_copilot',session.user_id,teamspace_id=space_id),'can_manage':can_manage,'can_fields':can_manage and (kind!='people' or p.allowed(db,workspace_id,'manage_people_custom_fields',session.user_id,teamspace_id=space_id)),'can_ban':p.allowed(db,workspace_id,'manage_bans',session.user_id,teamspace_id=space_id),'profile_iframe':ops.profile_iframe if p.allowed(db,workspace_id,'view_profile_iframe',session.user_id,teamspace_id=space_id) else {'enabled':False}}

@router.post('/support/{kind}/{identifier}/labels')
def update_labels(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,body:s.LabelsBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id,'manage',True)
 if kind=='people':p.require(db,workspace_id,'manage_people_tags',session.user_id,teamspace_id=row.teamspace_id)
 return r.set_labels(db,workspace_id,kind,row,body)

@router.post('/support/bulk-labels')
def bulk_labels(workspace_id:uuid.UUID,body:s.BulkLabels,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);rows=[r.record(db,workspace_id,body.kind,i,session.user_id,'manage',True) for i in sorted(set(body.ids))]
 for row in rows:
  if body.kind=='people':p.require(db,workspace_id,'manage_people_tags',session.user_id,teamspace_id=row.teamspace_id)
 for row in rows:r.set_labels(db,workspace_id,body.kind,row,body,False)
 db.commit();return {'updated':len(rows)}

@router.post('/support/{kind}/{identifier}/fields')
def update_fields(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,body:s.FieldsBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id,'manage',True)
 if kind=='people':p.require(db,workspace_id,'manage_people_custom_fields',session.user_id,teamspace_id=row.teamspace_id)
 return r.save_fields(db,workspace_id,kind,row,body)

@router.post('/support/{kind}/{identifier}/analyze',status_code=202)
def analyze(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,body:s.RequestJob,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id)
 job=jobs.analysis_request(db,workspace_id,kind,row,session.user_id,body.request_id,request.app.state.settings)
 result=jobs.job_payload(job)
 if not p.allowed(db,workspace_id,'view_ai_analysis',session.user_id,teamspace_id=job.teamspace_id):result['output']={}
 return result

@router.post('/support/chats/{identifier}/copilot',status_code=202)
def copilot(workspace_id:uuid.UUID,identifier:uuid.UUID,body:s.RequestJob,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,'chats',identifier,session.user_id);space=r.record_space(db,'chats',row);p.require(db,workspace_id,'view_copilot',session.user_id,teamspace_id=space);r.check_ban(db,row)
 prior=db.scalar(select(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.request_key=='copilot:'+str(body.request_id)))
 if not prior:jobs.reserve(db,workspace_id,request.app.state.settings,'copilot')
 job=jobs.request(db,workspace_id,space,session.user_id,'copilot','chats',identifier,{},'copilot:'+str(body.request_id));return jobs.job_payload(job)

@router.post('/support/{kind}/{identifier}/ban')
def ban(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,body:s.BanBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id);space=r.record_space(db,kind,row)
 p.require(db,workspace_id,'manage_bans',session.user_id,teamspace_id=space)
 person=row if kind=='people' else r.person_for(db,row) if kind=='chats' else db.get(SupportPerson,row.person_id)
 if not person:raise HTTPException(422,'This record has no linked person.')
 person=db.scalar(select(SupportPerson).where(SupportPerson.id==person.id).with_for_update().execution_options(populate_existing=True))
 r.apply_ban(db,workspace_id,person,space,session.user_id,body);return {'saved':True}

@router.post('/support/{kind}/{identifier}/unban')
def unban(workspace_id:uuid.UUID,kind:Literal['chats','tickets','people'],identifier:uuid.UUID,body:s.Revision,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id);space=r.record_space(db,kind,row);p.require(db,workspace_id,'manage_bans',session.user_id,teamspace_id=space)
 person=row if kind=='people' else r.person_for(db,row) if kind=='chats' else db.get(SupportPerson,row.person_id)
 if not person:raise HTTPException(422,'This record has no linked person.')
 person=db.scalar(select(SupportPerson).where(SupportPerson.id==person.id).with_for_update().execution_options(populate_existing=True));c.checked_revision(person,body.expected_revision)
 person.ban_permanent=False;person.banned_until=None;person.ban_reason='';person.revision+=1;person.updated_at=utcnow();db.add(PersonBanEvent(person_id=person.id,actor_id=session.user_id,reason='Unbanned'));db.commit();return {'saved':True}

@router.get('/support/people')
def people(workspace_id:uuid.UUID,request:Request,q:str=Query('',max_length=256),tag:Optional[uuid.UUID]=None,group:Optional[uuid.UUID]=None,
 custom_key:str=Query('',max_length=100),custom_value:str=Query('',max_length=4000),
 channel:Literal['','ticket','webchat','whatsapp','ai_agent_handoff','messaging','bitrix24','africas_talking']='',country:str=Query('',max_length=2),
 period:Literal['','today','week','month','custom']='',created_from:Optional[date]=None,created_to:Optional[date]=None,
 timezone:str=Query('Africa/Nairobi',max_length=100),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id)
 from app.people import service
 filters={k:v for k,v in locals().items() if k in {'q','tag','group','custom_key','custom_value','channel','country','period','created_from','created_to','timezone'}}
 return service.listing(db,workspace_id,session.user_id,filters,offset,limit)

@router.post('/support/people',status_code=201)
def create_person(workspace_id:uuid.UUID,body:s.PersonBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True)
 from app.people.service import register
 return register(db,workspace_id,session.user_id,body)

@router.post('/support/people/{identifier}')
def update_person(workspace_id:uuid.UUID,identifier:uuid.UUID,body:s.PersonBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True)
 from app.people.service import update
 return update(db,workspace_id,identifier,session.user_id,body)

@router.post('/support/exports',status_code=202)
def export(workspace_id:uuid.UUID,body:s.ExportBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id,True);c.require_plan(db,workspace_id,request.app.state.settings,1);space=None
 allowed=set(jobs.EXPORT_FIELDS[body.kind])|{'custom:'+f.key for f in r.field_definitions(db,workspace_id,'person' if body.kind=='people' else 'conversation')}
 if not set(body.fields)<=allowed:raise HTTPException(422,'Select available export fields.')
 for identifier in body.ids:
  row=r.record(db,workspace_id,body.kind,identifier,session.user_id);jobs.permitted_export(db,workspace_id,body.kind,row,session.user_id);jobs.check_export_fields(db,workspace_id,body.kind,row,session.user_id,body.fields);space=space or r.record_space(db,body.kind,row)
 payload=body.model_dump(mode='json',exclude={'request_id','kind'});payload['name']=body.kind+'-'+str(body.request_id)[:8]+'.csv'
 return jobs.job_payload(jobs.request(db,workspace_id,space,session.user_id,'export',body.kind,None,payload,'export:'+str(body.request_id)))

@router.get('/support/exports')
def exports(workspace_id:uuid.UUID,request:Request,offset:int=Query(0,ge=0),page_size:int=Query(40,ge=10,le=100),db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);c.require_plan(db,workspace_id,request.app.state.settings,1)
 rows=[j for j in db.scalars(select(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.kind=='export').order_by(SupportJob.created_at.desc())) if jobs.can_download(db,j,session.user_id)]
 items=[{**jobs.job_payload(j),'initiated_by':(db.get(User,j.actor_id).full_name or db.get(User,j.actor_id).email) if j.actor_id and db.get(User,j.actor_id) else 'Former user'} for j in rows[offset:offset+page_size]]
 return {'items':items,'total':len(rows),'page_size':page_size}

@router.get('/support/jobs/{identifier}')
def job(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);row=db.get(SupportJob,identifier)
 if not row or row.workspace_id!=workspace_id:raise HTTPException(404,'Job not found.')
 if row.kind=='export':
  if not jobs.can_download(db,row,session.user_id):raise HTTPException(404,'Export not found.')
 elif row.kind=='knowledge':require_config(db,workspace_id,row.teamspace_id,'copilot',session.user_id)
 else:
  target=r.record(db,workspace_id,row.target_kind,row.target_id,session.user_id)
  if row.kind=='analysis':p.require(db,workspace_id,'view_ai_analysis',session.user_id,teamspace_id=row.teamspace_id)
  else:__import__('app.inbox.copilot',fromlist=['require']).require(db,target,row.target_kind,session.user_id)
  if row.kind=='copilot' and row.actor_id!=session.user_id:raise HTTPException(404,'Draft not found.')
 return jobs.job_payload(row)

@router.get('/support/exports/{identifier}/download')
def download(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);row=db.get(SupportJob,identifier);c.require_plan(db,workspace_id,request.app.state.settings,1)
 if not row or row.workspace_id!=workspace_id or row.kind!='export' or not jobs.can_download(db,row,session.user_id):raise HTTPException(404,'Export not found.')
 if row.status!='completed' or row.file_data is None:raise HTTPException(409,'This export is not ready for download.')
 for target_id in row.payload['ids']:
  target=r.record(db,workspace_id,row.target_kind,uuid.UUID(target_id),session.user_id)
  jobs.check_export_fields(db,workspace_id,row.target_kind,target,session.user_id,row.payload['fields'])
 return Response(row.file_data,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="'+row.payload['name']+'"','Cache-Control':'no-store'})

@router.get('/support/embeds/{kind}')
def embeds(workspace_id:uuid.UUID,kind:Literal['analytics','profile_iframe'],request:Request,space_id:Optional[uuid.UUID]=None,db:Session=Depends(get_db)):
 session=actor(request,db,workspace_id);space_id=space_id or p.default_space(db,workspace_id);require_config(db,workspace_id,space_id,kind,session.user_id)
 if kind=='analytics':c.require_plan(db,workspace_id,request.app.state.settings,4)
 return getattr(c.operations(db,workspace_id,space_id),kind)
