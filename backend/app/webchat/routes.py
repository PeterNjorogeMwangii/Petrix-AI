import json,secrets,uuid
from datetime import timedelta
from pathlib import Path
from typing import List,Optional
from urllib.parse import quote
from fastapi import APIRouter,Depends,Request,HTTPException,File,UploadFile,Form,Response,Query
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from pydantic import ValidationError
from app.database import get_db
from app.routes.agents import access
from app.core.security import utcnow,token_hash,new_token
from app.models import Agent,AgentConversation,AgentMessage,AgentAttachment,WebchatChannel as Channel,WebchatAsset as Asset,WebchatPreview as Preview,WebchatSession as Visitor,WebchatConversation as Link,WebchatRequest as Receipt
from app.webchat import schemas as b,service as s
from app.workflows.schemas import TesterEvent

router=APIRouter(prefix='/api/workspaces/{workspace_id}/agents/{agent_id}/webchat',tags=['Publish Webchat'])
public=APIRouter(prefix='/api/webchat',tags=['Public Webchat'])
staff=APIRouter(prefix='/api/workspaces/{workspace_id}/support/chats',tags=['Publish Webchat'])
STATIC=Path(__file__).with_name('static')

@router.get('')
def index(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,agent_id=agent_id)
    return {'items':[s.payload(row,request.app.state.settings) for row in db.scalars(select(Channel).where(Channel.agent_id==agent_id,Channel.workspace_id==workspace_id).order_by(Channel.created_at))],
        'fields':[{'key':key,'name':v.name,'type':v.type,'options':v.options} for key,v in s.fields(db,workspace_id).items()],
        'languages':agent.languages,'speech_to_text':agent.speech_to_text_enabled,'text_to_speech':agent.voice_enabled}

@router.post('',status_code=201)
def create(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:b.Create,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=db.scalar(select(Channel).where(Channel.agent_id==agent_id,Channel.client_id==body.client_id))
    if not row:
        if (db.scalar(select(func.count()).select_from(Channel).where(Channel.agent_id==agent_id)) or 0)>=50:raise HTTPException(422,'Use up to 50 Webchat channels per agent.')
        config=b.Configuration(language=agent.default_language).model_dump(mode='json')
        row=Channel(client_id=body.client_id,agent_id=agent_id,workspace_id=workspace_id,actor_id=actor.user_id,name=body.name,config=config,runtime=s.freeze(db,agent,config));db.add(row);db.commit()
    return s.payload(row,request.app.state.settings)

@router.post('/assets',status_code=201)
def image(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,file:UploadFile=File(...),db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    from app.agent_service import sanitize_avatar
    data=sanitize_avatar(file.file.read(10*1024*1024+1))
    row=Asset(agent_id=agent_id,data=data);db.add(row);db.commit();return {'id':str(row.id),'url':str(request.url).rstrip('/')+'/'+str(row.id)}

@router.get('/assets/{identifier}')
def private_image(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,agent_id=agent_id);row=db.get(Asset,identifier)
    if not row or row.agent_id!=agent_id:raise HTTPException(404,'Image not found.')
    return Response(row.data,media_type='image/png')

@router.post('/{identifier}')
def save(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Save,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id);row=s.own(db,workspace_id,agent_id,identifier,True)
    if body.expected_revision!=row.revision:raise HTTPException(409,'This Webchat changed. Reload before saving.')
    row.config=s.checked(db,agent,body.config);row.runtime=s.freeze(db,agent,row.config);row.name=body.name;row.enabled=body.enabled;row.revision+=1;row.updated_at=utcnow()
    from app.agent_service import record_change
    record_change(db,agent,actor.user_id,'webchat_saved','Webchat channel '+row.name+' saved'+(' and enabled.' if row.enabled else '.'))
    db.commit();return s.payload(row,request.app.state.settings)

@router.post('/{identifier}/preview',status_code=201)
def preview(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Preview,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id);row=s.own(db,workspace_id,agent_id,identifier)
    s.rate(db,'preview:'+str(actor.user_id),20);config=s.checked(db,agent,body.config);token=new_token()
    db.add(Preview(token_hash=token_hash(token),channel_id=row.id,config=config,runtime=s.freeze(db,agent,config),expires_at=utcnow()+timedelta(minutes=30)));db.commit()
    return {'url':request.app.state.settings.api_base_url.rstrip('/')+'/api/webchat/'+str(row.id)+'/demo?preview='+quote(token),'expires_in':1800}

@router.post('/{identifier}/delete')
def remove(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Delete,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,agent_id=agent_id);row=s.own(db,workspace_id,agent_id,identifier,True)
    if body.expected_revision!=row.revision or not body.confirmed:raise HTTPException(409,'Reload and confirm deletion of this Webchat channel.')
    db.delete(row);db.commit();return {'deleted':True}

def bearer(request):
    value=request.headers.get('authorization','')
    return value[7:] if value.startswith('Bearer ') else ''
def authenticated(db,identifier,request,lock=False):return s.session(db,identifier,bearer(request),lock)

@public.get('/embed.js')
def embed():return Response((STATIC/'embed.js').read_text(encoding='utf-8'),media_type='application/javascript',headers={'Cache-Control':'public, max-age=300','Cross-Origin-Resource-Policy':'cross-origin'})
@public.get('/widget.js')
def javascript():return Response((STATIC/'widget.js').read_text(encoding='utf-8'),media_type='application/javascript')
@public.get('/widget.css')
def css():return Response((STATIC/'widget.css').read_text(encoding='utf-8'),media_type='text/css')

@public.get('/{identifier}/frame')
def frame(identifier:uuid.UUID,request:Request,host_origin:str,preview:str='',page:str=Query('',max_length=2048),db:Session=Depends(get_db)):
    row,config,runtime=s.effective(db,identifier,token_hash(preview) if preview else '')
    host=s.origin(host_origin) if preview else s.allowed_host(config,host_origin)
    nonce=secrets.token_urlsafe(20);data=json.dumps({'id':str(identifier),'host_origin':host,'page':page,'preview':preview},ensure_ascii=False).replace('<','\\u003c')
    html=(STATIC/'widget.html').read_text(encoding='utf-8').replace('__NONCE__',nonce).replace('__BOOTSTRAP__',data)
    ancestors="'self' "+request.app.state.settings.frontend_origin if preview else "'self' "+' '.join(config['domains']) if config['whitelist'] else '*'
    return Response(html,media_type='text/html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self' 'nonce-"+nonce+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; media-src 'self' blob: https:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors "+ancestors,'Permissions-Policy':'microphone=(self)'})

@public.get('/{identifier}/demo')
def demo(identifier:uuid.UUID,request:Request,preview:str='',db:Session=Depends(get_db)):
    s.effective(db,identifier,token_hash(preview) if preview else '')
    html=(STATIC/'demo.html').read_text(encoding='utf-8').replace('__CHANNEL__',str(identifier)).replace('__PREVIEW__',quote(preview))
    return Response(html,media_type='text/html',headers={'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'"})

@public.get('/{identifier}/config')
def config(identifier:uuid.UUID,request:Request,host_origin:str,preview:str='',db:Session=Depends(get_db)):
    row,c,runtime=s.effective(db,identifier,token_hash(preview) if preview else '')
    if not preview:s.allowed_host(c,host_origin)
    return s.public_config(db,row,c,runtime,preview)

@public.get('/{identifier}/assets/{asset}')
def asset(identifier:uuid.UUID,asset:uuid.UUID,preview:str='',db:Session=Depends(get_db)):
    row,c,_=s.effective(db,identifier,token_hash(preview) if preview else '')
    if str(asset) not in [c.get('icon'),c['appearance'].get('agent_icon'),c['appearance'].get('launcher_icon')]:raise HTTPException(404,'Image not found.')
    image=db.get(Asset,asset)
    if not image or image.agent_id!=row.agent_id:raise HTTPException(404,'Image not found.')
    return Response(image.data,media_type='image/png')

@public.post('/{identifier}/sessions',status_code=201)
def start_session(identifier:uuid.UUID,body:b.StartSession,request:Request,preview:str='',db:Session=Depends(get_db)):
    return s.new_session(db,identifier,body,preview,request)

@public.get('/{identifier}/requests/{request_id}')
def request_state(identifier:uuid.UUID,request_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    v,row,c,runtime=authenticated(db,identifier,request);record=db.get(Receipt,(v.id,request_id))
    if not record:raise HTTPException(404,'Request not found.')
    return {'status':record.status,'conversation_id':str(record.conversation_id) if record.conversation_id else None}

@public.get('/{identifier}/conversations')
def history(identifier:uuid.UUID,request:Request,offset:int=Query(0,ge=0),db:Session=Depends(get_db)):
    v,row,c,runtime=authenticated(db,identifier,request)
    query=select(Link,AgentConversation).join(AgentConversation,AgentConversation.id==Link.conversation_id).where(Link.session_id==v.id).order_by(Link.created_at.desc()).offset(offset).limit(31)
    rows=db.execute(query).all()
    return {'items':[{'id':str(chat.id),'created_at':link.created_at,'closed':bool(chat.workflow_state.get('closed')),'agent_name':chat.agent_name} for link,chat in rows[:30]],'has_more':len(rows)>30}

@public.post('/{identifier}/conversations',status_code=201)
def start_chat(identifier:uuid.UUID,body:b.StartChat,request:Request,db:Session=Depends(get_db)):
    v,row,c,runtime=authenticated(db,identifier,request)
    s.rate(db,'start:'+str(v.id),10)
    if c['hide_offline'] and not s.online(db,row):raise HTTPException(409,'Live support is currently offline.')
    existing=db.get(Receipt,(v.id,body.request_id))
    if existing:
        receipt,duplicate=s.receipt(db,v,body.request_id,{'kind':'start',**body.model_dump(mode='json')})
        link,chat=s.link(db,v,receipt.conversation_id);return s.snapshot(db,v,row,link,request)
    person=s.person(db,v,row,body.values,c,runtime)
    receipt,_=s.receipt(db,v,body.request_id,{'kind':'start',**body.model_dump(mode='json')})
    chat=AgentConversation(workspace_id=row.workspace_id,agent_id=row.agent_id,user_id=row.actor_id,person_id=person.id,agent_name=runtime['agent']['name'],workflow_state={'channel':'tester' if v.preview_hash else 'webchat','language':c['language'],'webchat_channel_id':str(row.id),'webchat_name':row.name,'webchat_icon':c.get('icon')})
    db.add(chat);db.flush();link=Link(conversation_id=chat.id,session_id=v.id,runtime=runtime);db.add(link);receipt.conversation_id=chat.id;db.commit()
    s.execute(db,v,row,link,TesterEvent(event='chat_started',language=c['language'],client_event_id=body.request_id),request)
    return s.snapshot(db,v,row,link,request)

@public.get('/{identifier}/conversations/{conversation_id}')
def detail(identifier:uuid.UUID,conversation_id:uuid.UUID,request:Request,before:Optional[uuid.UUID]=None,db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    return s.snapshot(db,v,row,link,request,before)

@public.post('/{identifier}/conversations/{conversation_id}/events')
def event(identifier:uuid.UUID,conversation_id:uuid.UUID,body:b.Event,request:Request,db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    from app.teamspace.records import check_ban
    check_ban(db,chat)
    if body.voice and (not c['voice'] or chat.live_status in {'pending','active'}):raise HTTPException(409,'Voice is unavailable in this conversation.')
    s.rate(db,'event:'+str(v.id),60)
    receipt,duplicate=s.receipt(db,v,body.request_id,{'conversation':str(chat.id),**body.model_dump(mode='json')},chat.id)
    if not duplicate:
        data=body.model_dump(exclude={'request_id','voice'});s.execute(db,v,row,link,TesterEvent(**data,conversation_id=chat.id,client_event_id=body.request_id),request)
    return s.snapshot(db,v,row,link,request)

@public.post('/{identifier}/conversations/{conversation_id}/poll')
def poll(identifier:uuid.UUID,conversation_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    from app.webchat.worker import expire
    expire(db,v,row,link,request,c)
    return s.snapshot(db,v,row,link,request)

@public.post('/{identifier}/conversations/{conversation_id}/attachments')
async def attachment(identifier:uuid.UUID,conversation_id:uuid.UUID,request:Request,request_id:uuid.UUID=Form(...),survey_answer_token:Optional[uuid.UUID]=Form(None),files:List[UploadFile]=File(...),transcript:str=Form('',max_length=4000),db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    from app.workflows.attachments import read_uploads
    from app.teamspace.records import check_ban
    check_ban(db,chat)
    uploads=await read_uploads(files)
    audio=all(f['content_type'].startswith('audio/') for f in uploads)
    if not c['attachments'] and not(audio and c['audio']):raise HTTPException(403,'Attachments are disabled for this Webchat.')
    s.rate(db,'upload:'+str(v.id),20)
    fingerprint={'conversation':str(chat.id),'survey':str(survey_answer_token) if survey_answer_token else None,'transcript':transcript,'files':[{'name':f['file_name'],'type':f['content_type'],'digest':s.hashlib.sha256(f['data']).hexdigest()} for f in uploads]}
    receipt,duplicate=s.receipt(db,v,request_id,fingerprint,chat.id)
    if not duplicate:
        from starlette.concurrency import run_in_threadpool
        body=TesterEvent(event='attachment_received',conversation_id=chat.id,client_event_id=request_id,survey_answer_token=survey_answer_token,attachment={'name':uploads[0]['file_name'],'media_type':'audio' if audio else 'image' if uploads[0]['content_type'].startswith('image/') else 'file'})
        await run_in_threadpool(s.execute,db,v,row,link,body,request,uploads)
        if audio and transcript and link.runtime['agent'].get('include_audio_transcript',True):
            message=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==chat.id,AgentMessage.role=='user',AgentMessage.message_data['source_request_id'].as_string()==str(request_id)))
            if message:message.content='Voice message: '+transcript;db.commit()
    return s.snapshot(db,v,row,link,request)

@public.get('/{identifier}/conversations/{conversation_id}/files/{file_id}')
def download(identifier:uuid.UUID,conversation_id:uuid.UUID,file_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    file=db.scalar(select(AgentAttachment).where(AgentAttachment.conversation_id==chat.id,AgentAttachment.id==file_id))
    if not file:raise HTTPException(404,'File not found.')
    # A visitor may only download copies actually delivered in their public log.
    public_log=s.snapshot(db,v,row,link,request)
    visible=any(str(file.id)==f.get('id') for m in public_log['messages'] for f in m.get('attachments',[]))
    if not visible:
        rows=db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==chat.id,AgentMessage.role.in_(['user','assistant'])))
        for message in rows:
            assets=list(message.message_data.get('attachments') or [])
            assets.extend(card.get('image') for card in (message.message_data.get('carousel') or {}).get('cards',[]) if card.get('image'))
            if any(isinstance(item,dict) and str(item.get('id'))==str(file.id) for item in assets):visible=True;break
    if not visible:raise HTTPException(404,'File was not delivered to this conversation.')
    from app.workflows.assets import file_response
    return file_response(file,request,inline=True)

@public.post('/{identifier}/conversations/{conversation_id}/messages/{message_id}/rating')
def ranking(identifier:uuid.UUID,conversation_id:uuid.UUID,message_id:uuid.UUID,body:b.Rating,request:Request,db:Session=Depends(get_db)):
    v,row,c,_=authenticated(db,identifier,request);link,chat=s.link(db,v,conversation_id)
    if not c['ranking']:raise HTTPException(403,'Message ranking is disabled.')
    message=db.scalar(select(AgentMessage).where(AgentMessage.id==message_id,AgentMessage.conversation_id==chat.id,AgentMessage.role=='assistant').with_for_update())
    if not message or message.message_data.get('source')!='live_agent':raise HTTPException(404,'Live agent message not found.')
    message.message_data={**message.message_data,'visitor_rating':body.value};db.commit();return {'saved':True}


@staff.get('/{conversation_id}/channel-icon')
def inbox_channel_icon(workspace_id:uuid.UUID,conversation_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    from app.teamspace.routes import actor
    from app.teamspace.records import record
    session=actor(request,db,workspace_id);conversation=record(db,workspace_id,'chats',conversation_id,session.user_id)
    key=conversation.workflow_state.get('webchat_icon') or conversation.workflow_state.get('whatsapp_icon')
    image=db.get(Asset,uuid.UUID(key)) if key else None
    if not image or image.agent_id!=conversation.agent_id:raise HTTPException(404,'Channel icon not found.')
    return Response(image.data,media_type='image/png')
