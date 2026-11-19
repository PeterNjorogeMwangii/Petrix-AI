import base64,hashlib,hmac,json,re,uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only
from app.core.security import token_hash,utcnow
from app.models import (WebchatChannel as Channel,WebchatAsset as Asset,WebchatPreview as Preview,WebchatSession as Visitor,WebchatConversation as Link,WebchatRequest as Receipt,Agent,AgentConversation,AgentMessage,AgentAttachment,SupportPerson,ConversationField,RateLimit,Membership)
from app.webchat.schemas import Configuration

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def rate(db,key,maximum):
    now=utcnow();key=token_hash('webchat:'+str(int(now.timestamp())//60)+':'+key)
    statement=insert(RateLimit).values(key=key,count=1,expires_at=now+timedelta(minutes=2))
    count=db.execute(statement.on_conflict_do_update(index_elements=[RateLimit.key],set_={'count':RateLimit.count+1}).returning(RateLimit.count)).scalar_one();db.commit()
    if count>maximum:raise HTTPException(429,'Too many Webchat requests. Please wait a minute.')
def own(db,workspace,agent,identifier,lock=False):
    q=select(Channel).where(Channel.workspace_id==workspace,Channel.agent_id==agent,Channel.id==identifier)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(q)
    if not row:raise HTTPException(404,'Webchat channel not found.')
    return row
def fields(db,workspace):return {f.key:f for f in db.scalars(select(ConversationField).where(ConversationField.workspace_id==workspace,ConversationField.scope=='person',ConversationField.archived.is_(False)))}
def checked(db,agent,config):
    if config.language not in agent.languages:raise HTTPException(422,'Choose a configured agent language.')
    if config.playback and not agent.voice_enabled:raise HTTPException(422,'Message playback requires agent Text-to-speech.')
    if config.voice and not(agent.voice_enabled and agent.speech_to_text_enabled):raise HTTPException(422,'Voice conversations require agent Speech-to-text and Text-to-speech.')
    if config.audio and not agent.speech_to_text_enabled:raise HTTPException(422,'Audio messaging requires agent Speech-to-text to be enabled.')
    keys=set(fields(db,agent.workspace_id))|{'name','email','phone'}
    if any(v.key not in keys for v in config.prechat.fields):raise HTTPException(422,'Choose an active People field for the pre-chat form.')
    identifiers=[v for v in [config.icon,config.appearance.agent_icon,config.appearance.launcher_icon] if v]
    if identifiers and set(db.scalars(select(Asset.id).where(Asset.agent_id==agent.id,Asset.id.in_(identifiers))))!=set(identifiers):raise HTTPException(422,'Choose images uploaded for this AI agent.')
    return config.model_dump(mode='json')
def freeze(db,agent,config):
    from app.agent_service import configuration
    from app.workflows.service import get_document
    doc=get_document(db,agent.id)
    f=fields(db,agent.workspace_id)
    selected={v['key']:{'type':f[v['key']].type,'options':deepcopy(f[v['key']].options)} for v in config['prechat']['fields'] if v['key'] in f}
    return {'agent':{**configuration(agent),'id':str(agent.id),'workspace_id':str(agent.workspace_id),'teamspace_id':str(agent.teamspace_id) if agent.teamspace_id else None},'nodes':deepcopy(doc.nodes),'revision':doc.revision,'fields':selected}
def payload(row,settings):
    return {'id':str(row.id),'name':row.name,'enabled':row.enabled,'revision':row.revision,'updated_at':row.updated_at,'config':row.config,'workflow_revision':row.runtime.get('revision'),
        'embed_url':settings.api_base_url.rstrip('/')+'/api/webchat/embed.js','frame_url':settings.api_base_url.rstrip('/')+'/api/webchat/'+str(row.id)+'/frame'}
def online(db,row):
    from app.inbox.service import roster
    from app.workspace_settings import permissions as p
    agent=db.get(Agent,row.agent_id)
    return any(v['available'] and str(agent.teamspace_id) in p.accessible_spaces(db,row.workspace_id,uuid.UUID(v['id'])) for v in roster(db,row.workspace_id))
def origin(value):
    try:return Configuration.origins([value])[0]
    except (ValueError,IndexError):raise HTTPException(422,'Use a valid website origin.') from None
def effective(db,identifier,preview_hash=''):
    row=db.get(Channel,identifier)
    if not row:raise HTTPException(404,'Webchat is unavailable.')
    if preview_hash:
        preview=db.get(Preview,preview_hash)
        if not preview or preview.channel_id!=row.id or preview.expires_at<=utcnow():raise HTTPException(410,'This preview expired. Open a new Preview from Publish.')
        return row,preview.config,preview.runtime
    if not row.enabled:raise HTTPException(404,'Webchat is unavailable.')
    return row,row.config,row.runtime
def allowed_host(config,host):
    normalized=origin(host)
    if config['whitelist'] and normalized not in config['domains']:raise HTTPException(403,'This website is not approved for this Webchat.')
    return normalized
def session(db,identifier,token,lock=False):
    if not token or len(token)>128:raise HTTPException(401,'Webchat session is required.')
    q=select(Visitor).where(Visitor.channel_id==identifier,Visitor.token_hash==token_hash(token))
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    visitor=db.scalar(q)
    if not visitor or visitor.expires_at<=utcnow():raise HTTPException(401,'Webchat session expired. Start a new session.')
    row,config,runtime=effective(db,identifier,visitor.preview_hash)
    if not visitor.preview_hash:allowed_host(config,visitor.host_origin)
    return visitor,row,config,runtime
def public_config(db,row,config,runtime,preview=''):
    c=deepcopy(config);a=runtime['agent'];base='/api/webchat/'+str(row.id)+'/assets/'
    def url(v):return base+v+('?preview='+preview if preview else '') if v else None
    c['appearance']['agent_icon_url']=url(c['appearance']['agent_icon']) or ('data:image/png;base64,'+a['avatar_base64'] if a.get('avatar_base64') else None)
    c['appearance']['launcher_icon_url']=url(c['appearance']['launcher_icon'])
    c['appearance']['title']=c['appearance']['title'] or a['name']
    c.pop('domains',None);c.pop('whitelist',None);c.pop('icon',None)
    c['prechat']['fields']=[{**f,**runtime.get('fields',{}).get(f['key'],{'type':'email' if f['key']=='email' else 'tel' if f['key']=='phone' else 'text','options':[]})} for f in c['prechat']['fields']]
    return {'channel_id':str(row.id),'config':c,'languages':a['languages'],'speech_languages':[v for v in a.get('speech_languages',[]) if v in a['languages']] or [a['default_language']],
        'speech_to_text':a.get('speech_to_text_enabled',False),'text_to_speech':a['voice_enabled'],'include_audio_transcript':a.get('include_audio_transcript',True),'voice_name':a['voice_name'],'voice_speed':a['voice_speed'],'preview':bool(preview),'available':not c['hide_offline'] or online(db,row)}
def new_session(db,identifier,body,preview,request):
    row,config,runtime=effective(db,identifier,token_hash(preview) if preview else '')
    host=origin(body.host_origin) if preview else allowed_host(config,body.host_origin)
    rate(db,'sessions:'+str(identifier)+':'+(request.client.host if request.client else 'unknown'),30)
    db.scalar(select(Channel).where(Channel.id==identifier).with_for_update())
    visitor=db.scalar(select(Visitor).where(Visitor.token_hash==token_hash(body.token)))
    if visitor:
        if visitor.channel_id!=identifier or visitor.host_origin!=host or visitor.preview_hash!=(token_hash(preview) if preview else ''):raise HTTPException(409,'Start a separate visitor session for this channel or website.')
        if visitor.expires_at<=utcnow():raise HTTPException(401,'This visitor session expired. Reset Webchat to start again.')
    else:
        visitor=Visitor(channel_id=identifier,token_hash=token_hash(body.token),host_origin=host,preview_hash=token_hash(preview) if preview else '',expires_at=utcnow()+timedelta(days=30));db.add(visitor)
    db.commit();return {'session_id':str(visitor.id),'expires_at':visitor.expires_at}
def link(db,visitor,identifier):
    row=db.scalar(select(Link).where(Link.session_id==visitor.id,Link.conversation_id==identifier))
    if not row:raise HTTPException(404,'Conversation not found in this visitor session.')
    conversation=db.get(AgentConversation,identifier)
    if not conversation:raise HTTPException(404,'Conversation not found.')
    return row,conversation
def receipt(db,visitor,request_id,value,conversation=None):
    db.scalar(select(Visitor).where(Visitor.id==visitor.id).with_for_update())
    previous=db.get(Receipt,(visitor.id,request_id),populate_existing=True);fingerprint=digest(value)
    if previous:
        if previous.digest!=fingerprint:raise HTTPException(409,'This request ID was used for another message.')
        if previous.status=='running':raise HTTPException(409,'This request is already processing or needs review. It will not be replayed. Refresh the conversation before sending again.')
        if previous.status=='failed':raise HTTPException(409,previous.error or 'The earlier turn could not finish. Refresh before sending a new message.')
        return previous,True
    active=db.scalar(select(Receipt.request_id).where(Receipt.session_id==visitor.id,Receipt.status=='running',Receipt.created_at>utcnow()-timedelta(minutes=3)))
    if active:raise HTTPException(409,'Wait for the current message to finish.')
    row=Receipt(session_id=visitor.id,request_id=request_id,digest=fingerprint,conversation_id=conversation,status='running');db.add(row);db.commit();return row,False
def person(db,visitor,row,values,config,runtime):
    from app.custom_fields.service import option_snapshot
    from app.custom_fields.schemas import FieldCreate
    from app.teamspace.records import is_banned
    allowed={v['key']:v for v in config['prechat']['fields']}
    if set(values)-set(allowed):raise HTTPException(422,'Submit only configured pre-chat fields.')
    if config['prechat']['enabled']:
        for key,f in allowed.items():
            if f['required'] and (key not in values or values[key] is None or values[key]==''):raise HTTPException(422,'Complete '+f['label']+'.')
    clean={};custom={};definitions=fields(db,row.workspace_id)
    for key,value in values.items():
        if key in {'name','email','phone'}:
            if not isinstance(value,str) or len(value)>{'name':200,'email':320,'phone':30}[key] or any(ord(c)<32 for c in value):raise HTTPException(422,'Use a valid '+key+'.')
            value=value.strip()
            if key=='email' and value and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):raise HTTPException(422,'Enter a valid email address.')
            if key=='phone' and value and not re.fullmatch(r'\+?[0-9 ()\-]{6,30}',value):raise HTTPException(422,'Enter a valid phone number.')
            clean[key]=value
        elif key in definitions:
            field=definitions[key]
            try:FieldCreate.model_validate({'scope':'person','type':field.type,'name':field.name,'options':field.options,'default_value':value})
            except ValueError:raise HTTPException(422,'Check '+allowed[key]['label']+'.') from None
            custom[key]=option_snapshot(field,value,'webchat')
        else:raise HTTPException(409,'This pre-chat field was changed. Refresh Webchat.')
    p=db.get(SupportPerson,visitor.person_id) if visitor.person_id else None
    if is_banned(p):raise HTTPException(403,'This person cannot exchange messages while a ban is active.')
    if not p:
        p=SupportPerson(workspace_id=row.workspace_id,teamspace_id=uuid.UUID(runtime['agent']['teamspace_id']) if runtime['agent'].get('teamspace_id') else None,identity_key='webchat:'+str(visitor.id),name='',email='',phone='');db.add(p);db.flush();visitor.person_id=p.id
    for key,value in clean.items():setattr(p,key,value)
    p.custom_fields={**(p.custom_fields or {}),**custom};p.updated_at=utcnow();db.flush();return p

@dataclass
class WebActor:
    user_id:uuid.UUID
    conversation_id:uuid.UUID
    agent_id:uuid.UUID
    session_id:uuid.UUID
    runtime:dict

def actor(row,visitor,record):return WebActor(row.actor_id,record.conversation_id,row.agent_id,visitor.id,record.runtime)
def runtime_objects(value):
    from app.workflows.numbering import walk
    data=deepcopy(value['agent']);avatar=data.pop('avatar_base64',None)
    for key in ['id','workspace_id','teamspace_id']:
        if data.get(key):data[key]=uuid.UUID(data[key])
    data['avatar_data']=base64.b64decode(avatar) if avatar else None
    return SimpleNamespace(**data),SimpleNamespace(nodes=value['nodes'],revision=value['revision'],next_node_number=1+max([n.get('number',0) for n in walk(value['nodes'])] or [0]))
def execute(db,visitor,row,record,body,request,uploads=None):
    from app.routes.workflows import process_event
    try:
        process_event(row.workspace_id,row.agent_id,body,request,db,upload_payloads=uploads,webchat_actor=actor(row,visitor,record))
        rec=db.get(Receipt,(visitor.id,body.client_event_id));rec.status='completed';record=db.get(Link,record.conversation_id);record.started=True;db.commit()
    except Exception:
        db.rollback();rec=db.get(Receipt,(visitor.id,body.client_event_id))
        if rec:rec.status='failed';rec.error='This turn could not finish. Refresh the conversation before sending a new message.';db.commit()
        raise HTTPException(503,'Webchat could not finish this turn. Refresh the conversation before sending again.') from None

def public_file(file):return {'id':str(file.id),'name':file.file_name,'content_type':file.content_type,'size_bytes':file.size_bytes}
def clean_files(value,db,conversation):
    out=[]
    for item in value or []:
        try:identifier=uuid.UUID(str(item.get('id')))
        except (ValueError,AttributeError,TypeError):
            url=item.get('url','') if isinstance(item,dict) else ''
            if isinstance(url,str) and url.startswith(('https://','http://')):out.append({'name':str(item.get('name','File'))[:255],'external_url':url,'content_type':item.get('content_type','application/octet-stream')})
            continue
        file=db.scalar(select(AgentAttachment).options(load_only(AgentAttachment.id,AgentAttachment.file_name,AgentAttachment.content_type,AgentAttachment.size_bytes,AgentAttachment.conversation_id)).where(AgentAttachment.id==identifier,AgentAttachment.conversation_id==conversation.id))
        if file:out.append(public_file(file))
    return out
def snapshot(db,visitor,row,record,request,before=None):
    from app.routes.workflows import result_payload
    conversation=db.get(AgentConversation,record.conversation_id)
    _,document=runtime_objects(record.runtime)
    query=select(AgentMessage).where(AgentMessage.conversation_id==conversation.id,AgentMessage.role.in_(['user','assistant']))
    if before:
        pivot=db.scalar(query.where(AgentMessage.id==before))
        if not pivot:raise HTTPException(404,'Public message cursor not found.')
        query=query.where(AgentMessage.sequence<pivot.sequence)
    page=list(db.scalars(query.order_by(AgentMessage.sequence.desc()).limit(101)))
    rows={'messages':[{**m.message_data,'id':str(m.id),'role':m.role,'content':m.content,'created_at':m.created_at.isoformat(),'sequence':str(m.sequence)} for m in reversed(page[:100])],'has_more':len(page)>100}
    raw=result_payload(document,conversation,conversation.workflow_state,[],[],None,True)
    outgoing=[]
    for m in rows['messages']:
        if m['role'] not in {'user','assistant'}:continue
        item={k:m[k] for k in ['id','role','content','created_at','sequence']};item['quick_replies']=m.get('quick_replies',[])[:10]
        if m['role']=='user' and m.get('source_request_id'):item['client_message_id']=m['source_request_id']
        item['attachments']=clean_files(m.get('attachments') or ([m['attachment']] if isinstance(m.get('attachment'),dict) and m['attachment'].get('id') else []),db,conversation)
        if m.get('graph'):item['graph']=m['graph']
        if m.get('carousel'):
            cards=[]
            for card in m['carousel'].get('cards',[]):
                images=clean_files([card['image']],db,conversation) if card.get('image') else []
                buttons=[]
                for button in card.get('buttons',[]):
                    if button.get('type')=='reply':buttons.append({k:button.get(k,'') for k in ['type','label','message']})
                    elif isinstance(button.get('url'),str) and button['url'].startswith(('https://','http://')):buttons.append({k:button.get(k,'') for k in ['type','label','url']})
                cards.append({'id':card.get('id'),'title':card.get('title',''),'subtitle':card.get('subtitle',''),'image':images[0] if images else None,'buttons':buttons})
            item['carousel']={'cards':cards}
        if m.get('source')=='live_agent':item.update({'author_name':m.get('author_name','Live agent'),'live_agent':True,'rating':m.get('visitor_rating')})
        outgoing.append(item)
    survey={k:raw['survey'][k] for k in ['answer_type','question_number','total_fields','enable_skip','answer_token'] if k in raw['survey']} if raw['survey'] else None
    feedback={k:raw['feedback'][k] for k in ['token','title','request_message','fields','rating_enabled','rating_buttons','typed_rating','submit_button'] if k in raw['feedback']} if raw['feedback'] else None
    if feedback:
        feedback['fields']=[{k:v for k,v in f.items() if k in {'id','question','label','title','type','reply_type','required','placeholder','options','choices','answer_type'}} for f in feedback.get('fields',[])]
    return {'conversation_id':str(conversation.id),'messages':outgoing,'has_more':rows['has_more'],'closed':raw['closed'],'language':raw['language'],'survey':survey,'feedback':feedback,'feedback_confirmation':raw['feedback_confirmation'],
        'live_status':conversation.live_status,'external':bool(raw['external_handoff'] and raw['external_handoff'].get('status') in {'sending','transferred','uncertain'}),'workflow_revision':document.revision}
