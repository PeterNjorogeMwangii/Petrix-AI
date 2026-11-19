import uuid,json,html,hashlib
from datetime import datetime,timezone
from typing import Optional,Literal
from fastapi import APIRouter,Depends,Request,Response,HTTPException,UploadFile,File,Form,Query
from sqlalchemy import select,func,update
from sqlalchemy.orm import Session
from pydantic import ValidationError
from app.database import get_db
from app.auth_service import current_session,require_csrf
from app.models import User,UserProfile,UserAlias,ProfileAsset,AgentConversation,AgentMessage,SupportAgent,SupportTicket,TicketEmail,TicketEmailDomain,TicketNote
from app.profile import service as s,schemas as m
from app.teamspace import records as r
from app.core.security import utcnow
router=APIRouter(prefix='/api',tags=['My profile and aliases'])

def actor(request,db,mutation=False):
 session=current_session(request,db)
 if mutation:require_csrf(request,session)
 return session

def form_body(model,body):
 try:return model.model_validate(body)
 except ValidationError as error:raise HTTPException(422,error.errors(include_input=False,include_url=False)[0]['msg']) from None

@router.get('/me/profile')
def get_profile(request:Request,db:Session=Depends(get_db)):
 session=actor(request,db);result=s.payload(db,session.user_id);db.commit()
 from app.agent_schemas import LANGUAGES
 return {**result,'languages':[{'code':code,'name':name} for code,name in LANGUAGES]}

@router.post('/me/profile/account')
async def account(request:Request,first_name:str=Form(''),last_name:str=Form(''),language:str=Form('en'),expected_revision:int=Form(...),remove_avatar:bool=Form(False),avatar:Optional[UploadFile]=File(None),db:Session=Depends(get_db)):
 session=actor(request,db,True);body=form_body(m.AccountBody,{'first_name':first_name,'last_name':last_name,'language':language,'expected_revision':expected_revision});value=s.profile(db,session.user_id,True);s.revision(value,body.expected_revision)
 if len(' '.join(v for v in [body.first_name,body.last_name] if v))>200:raise HTTPException(422,'Keep the combined account name within 200 characters.')
 value.first_name=body.first_name;value.last_name=body.last_name
 if value.language!=body.language:
  for live in db.scalars(select(SupportAgent).where(SupportAgent.user_id==session.user_id).with_for_update()):live.languages=[body.language];live.revision+=1
 value.language=body.language
 if remove_avatar:value.avatar_asset_id=None
 if avatar:value.avatar_asset_id=s.add_asset(db,session.user_id,await avatar.read(10*1024*1024+1))
 user=db.get(User,session.user_id);user.full_name=' '.join(part for part in [body.first_name,body.last_name] if part) or None;value.revision+=1;db.commit();return s.payload(db,session.user_id)

@router.post('/me/profile/preferences')
def preferences(body:m.PreferenceBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,True);value=s.profile(db,session.user_id,True);s.revision(value,body.expected_revision)
 for key,data in body.model_dump(exclude={'expected_revision'},exclude_unset=True).items():
  if data is None:raise HTTPException(422,'Select a value for '+key+'.')
  setattr(value,key,data)
 if body.presence=='online':
  from app.inbox.service import roster,ensure_profile
  workspace_id=session.active_workspace_id
  if not workspace_id:
   from app.models import Membership
   workspace_id=db.scalar(select(Membership.workspace_id).where(Membership.user_id==session.user_id).order_by(Membership.created_at).limit(1))
  if workspace_id:
   candidate=next((v for v in roster(db,workspace_id) if v['id']==str(session.user_id)),None)
   if candidate and candidate['team_ids']:
    live=ensure_profile(db,workspace_id,session.user_id);live.available=True;live.last_seen_at=utcnow();live.online_since=utcnow();live.presence_session_id=session.id;live.revision+=1
 value.revision+=1;db.commit()
 if body.presence=='online' and workspace_id:
  from app.teamspace.assignment import drain
  drain(db,workspace_id)
 return s.payload(db,session.user_id)

@router.post('/me/profile/notifications')
def notifications(body:m.NotificationBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,True);value=s.profile(db,session.user_id,True);s.revision(value,body.expected_revision);value.notifications=body.notifications.model_dump();value.revision+=1;db.commit();return s.payload(db,session.user_id)

@router.get('/me/aliases')
def aliases(request:Request,db:Session=Depends(get_db)):
 session=actor(request,db);return {'aliases':[s.alias_payload(a) for a in s.aliases(db,session.user_id)],'account':s.user_summary(db,db.get(User,session.user_id))}

@router.post('/me/aliases',status_code=201)
@router.post('/me/aliases/{identifier}')
async def save_alias(request:Request,identifier:Optional[uuid.UUID]=None,name:str=Form(...),is_default:bool=Form(False),signature_name:str=Form(''),signature_content:str=Form(''),expected_revision:Optional[int]=Form(None),remove_avatar:bool=Form(False),avatar:Optional[UploadFile]=File(None),db:Session=Depends(get_db)):
 session=actor(request,db,True);s.profile(db,session.user_id,True);body=form_body(m.AliasBody,{'name':name,'is_default':is_default,'signature_name':signature_name,'signature_content':signature_content,'expected_revision':expected_revision})
 row=s.alias_for(db,session.user_id,identifier,True) if identifier else None
 if row:s.revision(row,body.expected_revision)
 elif len(s.aliases(db,session.user_id))>=50:raise HTTPException(409,'An account supports up to 50 active aliases.')
 key=body.name.casefold()
 if db.scalar(select(UserAlias.id).where(UserAlias.user_id==session.user_id,UserAlias.archived.is_(False),UserAlias.name_key==key,UserAlias.id!=(identifier or uuid.uuid4()))):raise HTTPException(409,'An alias with this name already exists.')
 if body.is_default:db.execute(update(UserAlias).where(UserAlias.user_id==session.user_id,UserAlias.is_default.is_(True),UserAlias.id!=(identifier or uuid.uuid4())).values(is_default=False,revision=UserAlias.revision+1))
 if not row:row=UserAlias(user_id=session.user_id,name=body.name,name_key=key);db.add(row)
 else:row.revision+=1
 row.name=body.name;row.name_key=key;row.is_default=body.is_default;row.signature_name=body.signature_name;row.signature_content=body.signature_content
 if remove_avatar:row.avatar_asset_id=None
 if avatar:row.avatar_asset_id=s.add_asset(db,session.user_id,await avatar.read(10*1024*1024+1))
 db.commit();return s.alias_payload(row)

@router.post('/me/aliases/{identifier}/delete')
def remove_alias(identifier:uuid.UUID,body:m.Revision,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,True);s.profile(db,session.user_id,True);row=s.alias_for(db,session.user_id,identifier,True);s.revision(row,body.expected_revision);row.archived=True;row.is_default=False;row.revision+=1;db.commit();return {'deleted':True}

@router.get('/profile-assets/{identifier}')
def avatar(identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db);asset=db.get(ProfileAsset,identifier)
 if not asset:raise HTTPException(404,'Avatar not found.')
 allowed=asset.user_id==session.user_id
 if not allowed:
  url=s.asset_url(identifier)
  for message,conversation in db.execute(select(AgentMessage,AgentConversation).join(AgentConversation,AgentConversation.id==AgentMessage.conversation_id).where(AgentMessage.message_data['avatar_url'].as_string()==url)):
   try:r.record(db,conversation.workspace_id,'chats',conversation.id,session.user_id);allowed=True;break
   except HTTPException:continue
  if not allowed:
   for email,ticket in db.execute(select(TicketEmail,SupportTicket).join(SupportTicket,SupportTicket.id==TicketEmail.ticket_id).where(TicketEmail.persona['avatar_url'].as_string()==url)):
    try:r.record(db,ticket.workspace_id,'tickets',ticket.id,session.user_id);allowed=True;break
    except HTTPException:continue
 if not allowed:raise HTTPException(404,'Avatar not found.')
 return Response(asset.data,media_type='image/png',headers={'Cache-Control':'private, max-age=300','X-Content-Type-Options':'nosniff'})

@router.get('/workspaces/{workspace_id}/support/{kind}/{identifier}/persona')
def persona(workspace_id:uuid.UUID,kind:Literal['chats','tickets'],identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db);r.record(db,workspace_id,kind,identifier,session.user_id);value=s.selection(db,workspace_id,kind,identifier,session.user_id);current=s.snapshot(db,workspace_id,kind,identifier,session.user_id);db.commit()
 return {'current':current,'revision':value.revision if value else 0,'aliases':[s.alias_payload(a) for a in s.aliases(db,session.user_id)],'account_name':db.get(User,session.user_id).full_name or 'Support agent'}

@router.post('/workspaces/{workspace_id}/support/{kind}/{identifier}/persona')
def set_persona(workspace_id:uuid.UUID,kind:Literal['chats','tickets'],identifier:uuid.UUID,body:m.PersonaBody,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,True);r.record(db,workspace_id,kind,identifier,session.user_id,'manage',True);s.profile(db,session.user_id,True);current=s.selection(db,workspace_id,kind,identifier,session.user_id,True)
 if (current.revision if current else 0)!=body.expected_revision:raise HTTPException(409,'The selected alias changed. Reload before switching.')
 if body.alias_id:s.alias_for(db,session.user_id,body.alias_id)
 s.remember(db,workspace_id,kind,identifier,session.user_id,body.alias_id);db.commit();return persona(workspace_id,kind,identifier,request,db)

@router.get('/workspaces/{workspace_id}/profile-updates')
def updates(workspace_id:uuid.UUID,request:Request,since:Optional[datetime]=None,db:Session=Depends(get_db)):
 session=actor(request,db)
 from app.agent_service import workspace_for
 workspace_for(db,workspace_id,session.user_id)
 from app.inbox.service import can_view
 now=utcnow()
 if since and (since.tzinfo is None or since>now):raise HTTPException(422,'Use a past timestamp with timezone.')
 query=select(AgentConversation).where(AgentConversation.workspace_id==workspace_id,AgentConversation.live_status.in_(['pending','active']))
 if since:query=query.where(AgentConversation.live_updated_at>=since)
 rows=[]
 for row in db.scalars(query.order_by(AgentConversation.live_updated_at.desc()).limit(500)):
  if not can_view(db,row,session.user_id):continue
  latest=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==row.id,AgentMessage.role=='user').order_by(AgentMessage.created_at.desc(),AgentMessage.id.desc()).limit(1))
  rows.append({'id':str(row.id),'status':row.live_status,'assigned_user_id':str(row.live_assigned_user_id) if row.live_assigned_user_id else None,'updated_at':row.live_updated_at,'customer_message_id':str(latest.id) if latest else None,'customer_message_at':latest.created_at if latest else None})
 return {'chats':rows,'cursor':now}

@router.post('/workspaces/{workspace_id}/tickets/{identifier}/reply')
def ticket_reply(workspace_id:uuid.UUID,identifier:uuid.UUID,body:m.TicketReply,request:Request,db:Session=Depends(get_db)):
 session=actor(request,db,True);ticket=r.record(db,workspace_id,'tickets',identifier,session.user_id,'manage',True)
 request_data={'content':body.content,'domain_id':str(body.domain_id),'alias':str(body.alias_id) if body.alias_id else None if 'alias_id' in body.model_fields_set else 'automatic'}
 if body.attachment_ids:request_data['attachment_ids']=list(map(str,body.attachment_ids))
 if 'cc' in body.model_fields_set:request_data['cc']=body.cc
 if 'takeover' in body.model_fields_set:request_data['takeover']=body.takeover
 request_hash=hashlib.sha256(json.dumps(request_data,sort_keys=True).encode()).hexdigest()
 existing=db.scalar(select(TicketEmail).where(TicketEmail.ticket_id==identifier,TicketEmail.client_request_id==body.client_request_id))
 if existing:
  if existing.actor_id!=session.user_id or existing.persona.get('request_hash')!=request_hash:raise HTTPException(409,'This reply request ID was already used.')
  from app.routes.tickets import detail_payload
  return detail_payload(db,ticket,request.app.state.settings)
 if ticket.status in {'closed','resolved'}:raise HTTPException(409,'Reopen this ticket before sending a reply.')
 s.revision(ticket,body.expected_revision)
 from app.inbox.workspace import ensure_budget,attachments,attachment_payload
 ensure_budget(db,workspace_id,request.app.state.settings,'manual_emails')
 person=db.get(__import__('app.models',fromlist=['SupportPerson']).SupportPerson,ticket.person_id) if ticket.person_id else None
 if r.is_banned(person):raise HTTPException(403,'This person cannot receive replies while a ban is active.')
 from app.tickets import workspace as tw
 _,sender_email,domain=tw.sender(db,workspace_id,body.domain_id if 'domain_id' in body.model_fields_set else ticket.sender_domain_id,request.app.state.settings)
 from app.tickets.service import email,ScriptError
 prior_reply=db.scalar(select(TicketEmail).where(TicketEmail.ticket_id==identifier,TicketEmail.recipient_type=='customer',TicketEmail.kind=='reply').order_by(TicketEmail.created_at.desc()).limit(1))
 cc_values=body.cc if 'cc' in body.model_fields_set else prior_reply.cc_recipients if prior_reply else []
 try:cc=list(dict.fromkeys(email(v.strip()) for v in cc_values if v.strip()))
 except ScriptError as error:raise HTTPException(422,str(error)) from None
 cc=[v for v in cc if v.lower()!=ticket.email.lower()]
 if body.takeover:
  from app.workspace_settings.permissions import require
  require(db,workspace_id,'transfer_tickets',session.user_id,teamspace_id=r.record_space(db,'tickets',ticket))
  ticket.assignee_type='user';ticket.assignee_id=session.user_id;ticket.assignee_name=db.get(User,session.user_id).full_name or db.get(User,session.user_id).email

 identity=s.snapshot(db,workspace_id,'tickets',identifier,session.user_id,body.alias_id,'alias_id' in body.model_fields_set)
 assets=attachments(db,ticket,'tickets',session.user_id,body.attachment_ids)
 identity['attachments']=[attachment_payload(a) for a in assets]
 identity['request_hash']=request_hash
 identity['default_sender']=domain is None
 from app.models import TicketInboundEmail
 previous=db.scalar(select(TicketInboundEmail).where(TicketInboundEmail.ticket_id==identifier).order_by(TicketInboundEmail.created_at.desc()).limit(1))
 if previous:identity['in_reply_to']=previous.message_id
 signature='\n'.join(v for v in [identity['signature_name'],identity['signature_content']] if v);plain=body.content+('\n\n--\n'+signature if signature else '')
 content='<p>'+html.escape(body.content).replace('\n','<br>')+'</p>'+('<div class="email-signature">'+html.escape(signature).replace('\n','<br>')+'</div>' if signature else '')
 mail_id=uuid.uuid4()
 row=TicketEmail(id=mail_id,ticket_id=identifier,domain_id=domain.id if domain else None,cc_recipients=cc,kind='reply',actor_id=session.user_id,client_request_id=body.client_request_id,persona=identity,recipient_type='customer',recipient=ticket.email,sender=sender_email,sender_name=identity['name'],subject='['+ticket.reference+'] '+ticket.subject,body_html=content,body_text=plain,message_id='<reply-'+mail_id.hex+'@'+sender_email.rsplit('@',1)[1]+'>')
 db.add(row);s.remember(db,workspace_id,'tickets',identifier,session.user_id,uuid.UUID(identity['alias_id']) if identity['alias_id'] else None);ticket.updated_at=utcnow();ticket.revision+=1
 db.add(TicketNote(ticket_id=identifier,author_id=session.user_id,author_name=db.get(User,session.user_id).full_name or 'Support agent',kind='event',content='Customer email reply prepared as '+identity['name']+'.'));db.commit()
 from app.tickets.notifications import dispatch
 dispatch(db,request.app.state.ticket_mailer,ticket_id=identifier,email_id=row.id)
 from app.routes.tickets import detail_payload
 return detail_payload(db,ticket,request.app.state.settings)
