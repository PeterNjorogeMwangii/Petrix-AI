"""Native message rendering and an ordered, durable Meta delivery outbox."""
import json,re,uuid
from copy import deepcopy
from datetime import timedelta
from urllib.parse import urlsplit
from fastapi import HTTPException
from sqlalchemy import select,exists
from sqlalchemy.orm import aliased
from app.core.security import utcnow
from app.models import (AgentConversation,AgentMessage,AgentAttachment,WhatsAppChannel as Channel,
    WhatsAppContact as Contact,WhatsAppConversation as Link,WhatsAppOutbox as Outbox)
from app.whatsapp import service as s,credentials as crypto
from app.whatsapp.client import GraphError


def notice(db,conversation,text,**metadata):
    message=AgentMessage(conversation_id=conversation.id,role='assistant',content=text,
        message_data={'source':'whatsapp_notice','quick_replies':[],**metadata})
    db.add(message);db.flush();return message


def feedback_question(conversation,record):
    request=conversation.workflow_state.get('feedback_request')
    if not request or conversation.workflow_state.get('feedback_submitted'):return None
    token=request['token'];progress=deepcopy(record.feedback_state or {})
    if progress.get('token')!=token:
        progress={'token':token,'rating':None,'rating_done':not request.get('rating_enabled',True),'answers':{},'index':0,'prompt_key':''}
    if not progress['rating_done']:
        step='rating';text=request.get('request_message') or request.get('title') or 'How would you rate this conversation?'
        text+='\nReply with a rating from 1 to 5.';options=list(map(str,range(1,6)))
    elif progress['index']<len(request.get('fields',[])):
        field=request['fields'][progress['index']];step=field['id'];text=field['question'];options=field.get('choices',[]) if field['reply_type']=='choice' else []
        if not field['required']:text+='\nReply /skip to skip this optional question.'
    else:return None
    record.feedback_state=progress
    return {'text':text+'\nReply /new to start a new conversation.','options':options,'token':token,'step':step}


def feedback_prompt(db,conversation,record):
    question=feedback_question(conversation,record)
    if not question:return
    progress=deepcopy(record.feedback_state)
    key=question['token']+':'+question['step']
    if progress.get('prompt_key')==key:return
    notice(db,conversation,question['text'],source='whatsapp_feedback',quick_replies=question['options'],
        whatsapp_feedback={'token':question['token'],'step':question['step']})
    progress['prompt_key']=key;record.feedback_state=progress


def safe_link(value):
    try:
        parsed=urlsplit(value)
        return value if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password else ''
    except (ValueError,TypeError):return ''


def parts(message,conversation):
    data=message.message_data or {};text=message.content
    if data.get('transferred_history'):return []
    if data.get('graph'):
        graph=data['graph'];text+='\n'+str(graph.get('title') or 'Chart')
        for key,label in [('data',''),('bar_data',graph.get('bar_label') or 'Bar'),('line_data',graph.get('line_label') or 'Line')]:
            rows=graph.get(key) or []
            if rows:text+='\n'+(label+'\n' if label else '')+'\n'.join(', '.join(str(k)+': '+str(v) for k,v in row.items()) for row in rows[:20])
    carousel=data.get('carousel') or {};extra=[];card_options=[]
    for card in carousel.get('cards',[])[:10]:
        lines=[card.get('title',''),card.get('subtitle','')]
        for button in card.get('buttons',[]):
            if button.get('type')=='reply' and button.get('message'):card_options.append({'label':button.get('label') or button['message'],'value':button['message']})
            url=safe_link(button.get('url',''))
            if url:lines.append(button.get('label','Link')+': '+url)
        text+='\n'+'\n'.join(v for v in lines if v)
        if card.get('image'):extra.append(card['image'])
    # WhatsApp uses single-star emphasis. Plain user-facing text stays bounded.
    text=re.sub(r'\*\*([^*]+)\*\*',r'*\1*',text).strip()
    buttons=[]
    for choice in (list(data.get('quick_replies',[]))+card_options)[:10]:
        label=choice if isinstance(choice,str) else choice.get('label','')
        value=choice if isinstance(choice,str) else choice.get('value') or label
        if label and value:buttons.append({'label':str(label),'value':str(value)[:4000]})
    marker={}
    if data.get('whatsapp_feedback'):marker={'feedback':data['whatsapp_feedback']}
    elif data.get('source')=='ask_survey' and conversation.workflow_state.get('survey'):
        marker={'survey_answer_token':conversation.workflow_state['survey'].get('answer_token')}
    result=[]
    if text and (not buttons or len(text)>1024):
        result.extend({'type':'text','text':{'body':text[i:i+4000],'preview_url':False}} for i in range(0,len(text),4000))
    if buttons:
        result.append({'_options':buttons,'_marker':marker,'_body':text if len(text)<=1024 else 'Choose an option.'})
    for file in list(data.get('attachments',[]))+extra:
        if not isinstance(file,dict):continue
        identifier=file.get('id');url=safe_link(file.get('url',''))
        if identifier:result.append({'_attachment_id':str(identifier)})
        elif url:
            mime=file.get('content_type','');kind='image' if mime.startswith('image/') else 'audio' if mime.startswith('audio/') else 'video' if mime.startswith('video/') else 'document'
            result.append({'type':kind,kind:{'link':url,**({'filename':str(file.get('name','Document'))[:200]} if kind=='document' else {})}})
    return result[:80]


def interactive(row,part):
    options=part['_options'];markers=part.get('_marker',{});choices=[];mapping={}
    for index,option in enumerate(options):
        identifier='reply:'+str(row.id)+':'+str(index)
        mapping[identifier]={'value':option['value'],**markers}
        choices.append({'id':identifier,'title':option['label'][:20 if len(options)<=3 else 24]})
    if len(options)<=3:
        return {'type':'interactive','interactive':{'type':'button','body':{'text':part.get('_body') or 'Choose an option.'},
            'action':{'buttons':[{'type':'reply','reply':v} for v in choices]}}},mapping
    return {'type':'interactive','interactive':{'type':'list','body':{'text':part.get('_body') or 'Choose an option.'},
        'action':{'button':'Choose an option','sections':[{'rows':choices}]}}},mapping


def enqueue(db,conversation,settings,commit=True):
    if not db.get(Link,conversation.id):return
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==conversation.id).with_for_update())
    record=db.scalar(select(Link).where(Link.conversation_id==conversation.id).with_for_update())
    if not record.channel_id:return
    channel=db.get(Channel,record.channel_id)
    if not channel:return
    feedback_prompt(db,conversation,record)
    messages=list(db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==conversation.id,AgentMessage.sequence>record.queued_sequence).order_by(AgentMessage.sequence)))
    for message in messages:
        if message.role=='assistant':
            for index,part in enumerate(parts(message,conversation)):
                row=Outbox(id=uuid.uuid4(),channel_id=channel.id,conversation_id=conversation.id,message_id=message.id,
                    message_sequence=message.sequence,part=index,payload=part,buttons={},connection_revision=channel.connection_revision)
                if '_options' in part:row.payload,row.buttons=interactive(row,part)
                db.add(row)
            message.message_data={**message.message_data,'delivery_status':'queued','delivery_error':''}
        record.queued_sequence=message.sequence
    if commit:db.commit()


def context(db,conversation):
    record=db.get(Link,conversation.id)
    if not record or not record.channel_id:raise HTTPException(409,'This WhatsApp channel was removed. Its conversation history is retained.')
    channel=db.get(Channel,record.channel_id);reason=s.inactive_reason(db,channel)
    if reason:raise HTTPException(409,reason)
    if record.phone_number_id!=channel.phone_number_id:raise HTTPException(409,'This conversation belongs to the previous WhatsApp phone number.')
    contact=db.get(Contact,record.contact_id) if record.contact_id else None
    if not contact or not contact.last_customer_at or contact.last_customer_at<utcnow()-timedelta(hours=24):
        raise HTTPException(409,'The WhatsApp 24-hour customer service window is closed. Wait for a new customer message or use an approved template through your WhatsApp business tools.')
    from app.teamspace.records import check_ban
    check_ban(db,conversation)
    return record,channel,contact


def validate_delivery(db,row,settings,allow_revision=False):
    conversation=db.get(AgentConversation,row.conversation_id)
    if not conversation:raise HTTPException(409,'The conversation is no longer available.')
    record,channel,contact=context(db,conversation)
    if not allow_revision and row.connection_revision!=channel.connection_revision:raise HTTPException(409,'The WhatsApp connection changed. Review this delivery before retrying it.')
    return channel,contact


def update_message(db,identifier):
    message=db.get(AgentMessage,identifier)
    if not message:return
    rows=list(db.scalars(select(Outbox).where(Outbox.message_id==identifier)))
    if not rows:return
    failed=next((r for r in rows if r.status in {'uncertain','failed','blocked'}),None)
    if failed:status=failed.status;error=failed.error
    elif all(r.status=='read' for r in rows):status='read';error=''
    elif all(r.status in {'delivered','read'} for r in rows):status='delivered';error=''
    elif all(r.status in {'sent','delivered','read'} for r in rows):status='sent';error=''
    elif all(r.status in {'sent','delivered','read','cancelled'} for r in rows) and any(r.status=='cancelled' for r in rows):
        status='cancelled';error='Unsent parts were cancelled. Previously accepted parts remain sent.' if any(r.status!='cancelled' for r in rows) else ''
    else:status='queued';error=''
    message.message_data={**message.message_data,'delivery_status':status,'delivery_error':error}


def materialize(db,row,channel,client,token):
    payload=deepcopy(row.payload)
    if '_attachment_id' not in payload:return payload
    try:identifier=uuid.UUID(payload['_attachment_id'])
    except (ValueError,TypeError):raise GraphError('The outgoing attachment is unavailable.') from None
    file=db.get(AgentAttachment,identifier)
    if not file or file.conversation_id!=row.conversation_id:raise GraphError('The outgoing attachment is unavailable.')
    mime=(file.content_type or 'application/octet-stream').split(';')[0].strip().lower()
    kind='image' if mime in {'image/jpeg','image/png'} else 'audio' if mime.startswith('audio/') else 'video' if mime.startswith('video/') else 'document'
    maximum=5*1024*1024 if kind=='image' else 16*1024*1024 if kind in {'audio','video'} else 20*1024*1024
    if not file.data or len(file.data)>maximum:raise GraphError('This attachment exceeds the WhatsApp channel media limit.')
    media_id=client.upload(channel.phone_number_id,token,mime,file.file_name,file.data)
    return {'type':kind,kind:{'id':media_id,**({'filename':file.file_name[:200]} if kind=='document' else {})}}


def send_one(application):
    settings=application.state.settings
    with application.state.session_factory() as db:
        stale=list(db.scalars(select(Outbox).where(Outbox.status=='sending',Outbox.lease_expires_at<utcnow()).with_for_update(skip_locked=True).limit(20)))
        for row in stale:row.status='uncertain';row.error='Delivery was interrupted and has not been confirmed. Check Meta status before sending again.';update_message(db,row.message_id)
        if stale:db.commit();return True
        earlier=aliased(Outbox)
        waiting=exists(select(earlier.id).where(earlier.conversation_id==Outbox.conversation_id,
            earlier.status.in_(['queued','sending','uncertain','failed','blocked']),
            (earlier.message_sequence<Outbox.message_sequence)|((earlier.message_sequence==Outbox.message_sequence)&(earlier.part<Outbox.part))))
        row=db.scalar(select(Outbox).where(Outbox.status=='queued',~waiting).order_by(Outbox.created_at,Outbox.message_sequence,Outbox.part).with_for_update(skip_locked=True).limit(1))
        if not row:return False
        try:channel,contact=validate_delivery(db,row,settings)
        except HTTPException as error:
            row.status='blocked';row.error=str(error.detail)[:300];row.updated_at=utcnow();update_message(db,row.message_id);db.commit();return True
        try:credentials=crypto.secrets(channel,settings);token=credentials['access_token']
        except (HTTPException,KeyError):
            row.status='blocked';row.error='Save and verify the channel credentials again.';update_message(db,row.message_id);db.commit();return True
        identifier=row.id;lease=uuid.uuid4();row.status='sending';row.lease_token=lease;row.lease_expires_at=utcnow()+timedelta(minutes=2);row.updated_at=utcnow()
        token=credentials['access_token'];destination=contact.bsuid or contact.wa_id;channel_id=channel.id;connection=channel.connection_revision
        db.commit()
        # Media upload is read-only with respect to the recipient. Sending is
        # recorded as in-flight first and never blindly retried after interruption.
        try:
            row=db.get(Outbox,identifier);channel=db.get(Channel,channel_id)
            payload=materialize(db,row,channel,application.state.whatsapp_client,token)
            db.commit()
            row=db.get(Outbox,identifier)
            if not row:return True
            validate_delivery(db,row,settings)
            channel=db.get(Channel,channel_id)
            if channel.connection_revision!=connection:raise HTTPException(409,'The WhatsApp connection changed before sending.')
            db.commit()
            provider_id=application.state.whatsapp_client.send(channel.phone_number_id,token,destination,payload,identifier)
            outcome='sent';failure=''
        except HTTPException as error:provider_id=None;outcome='blocked';failure=str(error.detail)[:300]
        except GraphError as error:provider_id=None;outcome='uncertain' if error.uncertain else 'failed';failure=str(error)[:300]
        except Exception:provider_id=None;outcome='uncertain';failure='The WhatsApp send could not be confirmed. Review it before sending again.'
        db.rollback()
        row=db.scalar(select(Outbox).where(Outbox.id==identifier).with_for_update())
        if not row or row.lease_token!=lease:return True
        if row.status not in {'sent','delivered','read'}:
            row.status=outcome;row.error=failure;row.updated_at=utcnow()
            if provider_id:row.provider_id=provider_id
        update_message(db,row.message_id);db.commit();return True
