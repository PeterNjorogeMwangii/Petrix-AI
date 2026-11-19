"""Durable WhatsApp intake and ordered delivery. Uncertain work is never replayed."""
import argparse,hashlib,threading,time,uuid
from copy import deepcopy
from datetime import timedelta
from fastapi import Request,HTTPException
from sqlalchemy import select,exists,func,text
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.postgresql import insert
from app.core.security import utcnow
from app.models import (AgentConversation,AgentMessage,WhatsAppChannel as Channel,WhatsAppContact as Contact,
    WhatsAppConversation as Link,WhatsAppInbound as Inbound,WhatsAppOutbox as Outbox)
from app.whatsapp import service as s,credentials as crypto,delivery
from app.whatsapp.client import GraphError
from app.workflows.schemas import TesterEvent


def request_for(application):
    return Request({'type':'http','app':application,'method':'POST','path':'/internal/whatsapp','query_string':b'',
        'scheme':'https','server':('localhost',8000),'client':('127.0.0.1',0),'headers':[]})


def status(db,entry):
    value=entry.payload
    row=db.scalar(select(Outbox).where(Outbox.channel_id==entry.channel_id,Outbox.provider_id==entry.provider_id).with_for_update())
    if not row:
        try:identifier=uuid.UUID(value.get('biz_opaque_callback_data') or '')
        except (ValueError,TypeError,AttributeError):return
        row=db.scalar(select(Outbox).where(Outbox.id==identifier,Outbox.channel_id==entry.channel_id,
            Outbox.status.in_(['sending','uncertain','sent','delivered','read','failed','cancelled'])).with_for_update())
    if not row:return
    link=db.get(Link,row.conversation_id);contact=db.get(Contact,link.contact_id) if link and link.contact_id else None
    if not contact:return
    # A signed callback must also match this channel's recipient and send ID.
    who=value.get('recipient_user_id') or value.get('recipient_id')
    if who and who not in {contact.bsuid,contact.wa_id}:return
    if row.provider_id and row.provider_id!=entry.provider_id:return
    other=db.scalar(select(Outbox.id).where(Outbox.provider_id==entry.provider_id,Outbox.id!=row.id))
    if other:return
    row.provider_id=entry.provider_id
    rank={'sent':1,'delivered':2,'read':3}
    incoming=value['status']
    if incoming=='failed':
        if row.status not in {'delivered','read'}:
            row.status='failed';row.error='Meta reported delivery failure (code '+str(value.get('code') or 0)+').'
    elif not (row.status=='failed' and incoming=='sent') and rank.get(incoming,0)>=rank.get(row.status,0):row.status=incoming;row.error=''
    row.updated_at=utcnow();delivery.update_message(db,row.message_id)


def choice(db,entry,conversation):
    identifier=entry.payload.get('button_id','')
    if not identifier:return entry.payload.get('text',''),{}
    if not identifier.startswith('reply:'):return entry.payload.get('text',''),{}
    try:source=uuid.UUID(identifier.split(':')[1])
    except (ValueError,IndexError):raise HTTPException(409,'That answer is no longer available. Reply to the current question.') from None
    outbox=db.scalar(select(Outbox).where(Outbox.id==source,Outbox.channel_id==entry.channel_id,Outbox.conversation_id==conversation.id))
    mapped=(outbox.buttons or {}).get(identifier) if outbox else None
    if not mapped:raise HTTPException(409,'That answer belongs to another conversation. Reply to the current question.')
    pending=conversation.workflow_state.get('survey') or {}
    if mapped.get('survey_answer_token') and mapped['survey_answer_token']!=pending.get('answer_token'):
        raise HTTPException(409,'That survey question has already changed. Reply to the current question.')
    if mapped.get('feedback'):
        active=delivery.feedback_question(conversation,db.get(Link,conversation.id))
        if not active or mapped['feedback']!={'token':active['token'],'step':active['step']}:
            raise HTTPException(409,'That feedback question has already changed. Reply to the current question.')
    return mapped['value'],mapped


def feedback(db,conversation,record,entry,content,execute):
    question=delivery.feedback_question(conversation,record)
    if not question:return False
    progress=deepcopy(record.feedback_state);request=conversation.workflow_state['feedback_request']
    if entry.payload['type'] not in {'text','button','interactive'}:
        raise HTTPException(422,'Reply to the feedback question with text or one of its options.')
    if question['step']=='rating':
        if content.strip() not in {'1','2','3','4','5'}:raise HTTPException(422,'Choose a rating from 1 to 5.')
        progress['rating']=int(content.strip());progress['rating_done']=True
    else:
        field=request['fields'][progress['index']];value=content.strip()
        if value=='/skip' and not field['required']:value=''
        elif field['reply_type']=='choice':
            if value not in field['choices']:raise HTTPException(422,'Choose one of the listed answers.')
        else:
            from app.workflows.actions.ask_survey.handler import typed_answer
            try:typed_answer({'answer_type':'text' if field['reply_type']=='open_response' else field['reply_type']},{'event':'message_received','content':value})
            except ValueError:raise HTTPException(422,'Enter a valid '+field['reply_type'].replace('_',' ')+' answer.') from None
        progress['answers'][field['id']]=value;progress['index']+=1
    db.add(AgentMessage(conversation_id=conversation.id,role='user',content=content,
        message_data={'source':'whatsapp_feedback','source_request_id':str(entry.id)}));db.flush()
    progress['prompt_key']='';record.feedback_state=progress;db.commit()
    if progress['rating_done'] and progress['index']>=len(request.get('fields',[])):
        execute(TesterEvent(event='collect_feedback',conversation_id=conversation.id,client_event_id=entry.id,
            feedback_token=request['token'],rating=progress['rating'],feedback_answers=progress['answers']))
        db.refresh(conversation)
        confirmation=conversation.workflow_state.get('feedback_confirmation')
        if confirmation:delivery.notice(db,conversation,confirmation)
    delivery.enqueue(db,conversation,db.info['whatsapp_settings'])
    return True


def process(db,entry,application):
    settings=application.state.settings;db.info['whatsapp_settings']=settings
    channel=db.get(Channel,entry.channel_id)
    if s.inactive_reason(db,channel) or entry.connection_revision!=channel.connection_revision:
        entry.status='ignored';entry.error='The channel is disabled, unavailable or its connection has changed.';db.commit();return
    person=db.get(Contact,entry.contact_id) if entry.contact_id else None
    if not person:entry.status='ignored';entry.error='This recipient is no longer available.';db.commit();return
    if entry.kind=='timeout':
        conversation=db.get(AgentConversation,entry.conversation_id);record=db.get(Link,entry.conversation_id)
        newest=db.scalar(select(func.max(AgentMessage.sequence)).where(AgentMessage.conversation_id==entry.conversation_id)) or 0
        last=db.scalar(select(func.max(AgentMessage.created_at)).where(AgentMessage.conversation_id==entry.conversation_id)) or (conversation.created_at if conversation else utcnow())
        last=max(last,person.last_customer_at or last)
        if not conversation or not record or conversation.workflow_state.get('closed') or not channel.config.get('auto_close') or newest!=entry.payload.get('sequence') or utcnow()-last<timedelta(minutes=channel.config['timeout_minutes']):
            entry.status='ignored';db.commit();return
    else:conversation,record=s.ensure_conversation(db,channel,person,entry)
    entry.conversation_id=conversation.id;db.commit()
    actor=s.WhatsAppActor(channel.actor_id,channel.agent_id,conversation.id,person.id,channel.id)
    request=request_for(application)
    from app.routes.workflows import process_event
    def execute(body,uploads=None):
        return process_event(channel.workspace_id,channel.agent_id,body,request,db,upload_payloads=uploads,whatsapp_actor=actor)
    if entry.kind=='timeout':
        execute(TesterEvent(event='chat_timeout',conversation_id=conversation.id,client_event_id=entry.id,channel='whatsapp'))
        return
    if not record.started:
        execute(TesterEvent(event='chat_started',client_event_id=uuid.uuid5(entry.id,'start'),language=channel.config['language'],channel='whatsapp'))
        record=db.get(Link,conversation.id);record.started=True;db.commit();db.refresh(conversation)
        # A chat-start workflow can close a conversation; the triggering user
        # message is still stored below, without reopening its closed workflow.
    content,marker=choice(db,entry,conversation)
    if feedback(db,conversation,record,entry,content,execute):return
    if conversation.workflow_state.get('closed'):
        db.add(AgentMessage(conversation_id=conversation.id,role='user',content=content or 'Attachment received.',message_data={'source_request_id':str(entry.id)}));db.flush()
        delivery.notice(db,conversation,'This conversation is closed. Send another message to start a new conversation.')
        delivery.enqueue(db,conversation,settings);return
    if entry.payload['type']=='unsupported':
        db.add(AgentMessage(conversation_id=conversation.id,role='user',content='Unsupported WhatsApp message.',message_data={'source_request_id':str(entry.id)}));db.flush()
        delivery.notice(db,conversation,'Please send text, an image, document, audio or video file.');delivery.enqueue(db,conversation,settings);return
    pending=conversation.workflow_state.get('survey') or {};answer=pending.get('answer_token')
    if entry.payload.get('media'):
        media=entry.payload['media'];token=crypto.secrets(channel,settings)['access_token']
        file=application.state.whatsapp_client.download(media.get('id',''),token,media.get('name',''))
        kind=entry.payload['type'];kind='image' if kind=='sticker' else 'file' if kind=='document' else kind
        if content:
            db.add(AgentMessage(conversation_id=conversation.id,role='user',content=content,message_data={'source':'whatsapp_caption','source_request_id':str(entry.id)}));db.flush();db.commit()
        execute(TesterEvent(event='attachment_received',conversation_id=conversation.id,client_event_id=entry.id,
            survey_answer_token=answer,attachment={'name':file['file_name'],'media_type':kind},channel='whatsapp'),[file])
    elif content:
        execute(TesterEvent(event='message_received',conversation_id=conversation.id,client_event_id=entry.id,
            content=content,survey_answer_token=answer,channel='whatsapp'))


def intake_one(application,statuses_only=False):
    with application.state.session_factory() as db:
        stale=list(db.scalars(select(Inbound).where(Inbound.status=='running',Inbound.lease_expires_at<utcnow()).with_for_update(skip_locked=True).limit(20)))
        for row in stale:
            row.status='uncertain';row.error='Processing was interrupted. Review the conversation, then dismiss this event to continue. External actions are not replayed.'
        if stale:db.commit();return True
        prior=aliased(Inbound)
        waiting=exists(select(prior.id).where(prior.contact_id==Inbound.contact_id,prior.position<Inbound.position,prior.status.in_(['pending','running','uncertain'])))
        query=select(Inbound).where(Inbound.status=='pending')
        if statuses_only:query=query.where(Inbound.kind=='status')
        else:query=query.where(Inbound.kind!='status',~waiting)
        entry=db.scalar(query.order_by(Inbound.position).with_for_update(skip_locked=True).limit(1))
        if not entry:return False
        identifier=entry.id;lease=uuid.uuid4();entry.status='running';entry.lease_token=lease;entry.lease_expires_at=utcnow()+timedelta(minutes=15);db.commit()
        try:
            if entry.kind=='status':status(db,entry)
            else:process(db,entry,application)
            entry=db.get(Inbound,identifier);entry.status='completed' if entry.status=='running' else entry.status;entry.completed_at=utcnow();db.commit()
        except (HTTPException,GraphError) as error:
            db.rollback();entry=db.get(Inbound,identifier)
            if not entry:return True
            # These failures have a known validation/provider outcome. Unknown
            # failures take the uncertain path and block later recipient events.
            entry.status='failed';entry.error=(str(error.detail) if isinstance(error,HTTPException) else str(error))[:300];entry.completed_at=utcnow()
            if entry.conversation_id:
                conversation=db.get(AgentConversation,entry.conversation_id)
                if conversation and entry.kind!='status':
                    known=db.scalar(select(AgentMessage.id).where(AgentMessage.conversation_id==conversation.id,
                        AgentMessage.message_data['source_request_id'].as_string()==str(entry.id)).limit(1))
                    if not known:
                        db.add(AgentMessage(conversation_id=conversation.id,role='user',content=entry.payload.get('text') or 'WhatsApp attachment received.',message_data={'source_request_id':str(entry.id)}));db.flush()
                    delivery.notice(db,conversation,entry.error);delivery.enqueue(db,conversation,application.state.settings,commit=False)
            db.commit()
        except Exception:
            db.rollback();entry=db.get(Inbound,identifier)
            if entry and entry.lease_token==lease:
                entry.status='uncertain';entry.error='Processing could not be confirmed. Review the conversation and dismiss this event to continue. Workflows are not replayed.';db.commit()
        return True


def schedule_timeout(application):
    last=select(func.max(AgentMessage.created_at)).where(AgentMessage.conversation_id==AgentConversation.id).correlate(AgentConversation).scalar_subquery()
    cutoff=utcnow()-Channel.config['timeout_minutes'].as_integer()*text("INTERVAL '1 minute'")
    query=select(Link,Contact,Channel,AgentConversation).join(Contact,Link.contact_id==Contact.id).join(Channel,Link.channel_id==Channel.id).join(AgentConversation,Link.conversation_id==AgentConversation.id).where(
        Channel.enabled.is_(True),Channel.config['auto_close'].as_boolean().is_(True),AgentConversation.workflow_state['closed'].as_boolean().is_not(True),
        func.greatest(func.coalesce(last,AgentConversation.created_at),Contact.last_customer_at)<=cutoff).order_by(Link.created_at).limit(20)
    with application.state.session_factory() as db:
        for record,person,channel,conversation in db.execute(query):
            sequence=conversation.message_sequence;key=hashlib.sha256(('timeout:'+str(conversation.id)+':'+str(sequence)).encode()).hexdigest()
            statement=insert(Inbound).values(id=uuid.uuid4(),channel_id=channel.id,contact_id=person.id,conversation_id=conversation.id,event_key=key,provider_id='',kind='timeout',
                payload={'sequence':sequence},connection_revision=channel.connection_revision,status='pending',error='',created_at=utcnow())
            if db.execute(statement.on_conflict_do_nothing(index_elements=['channel_id','event_key']).returning(Inbound.id)).scalar():db.commit();return True
        return False


def run_one(application):
    if intake_one(application,True):return True
    turn=getattr(application.state,'whatsapp_send_turn',False);application.state.whatsapp_send_turn=not turn
    if turn and delivery.send_one(application):return True
    if intake_one(application):return True
    if not turn and delivery.send_one(application):return True
    return schedule_timeout(application)


def start(application):
    stop=threading.Event()
    def loop():
        import logging
        while not stop.is_set():
            try:
                if run_one(application):continue
            except Exception:logging.getLogger(__name__).error('WhatsApp worker tick failed; durable activity remains available for review.')
            stop.wait(1)
    thread=threading.Thread(target=loop,name='petrix-whatsapp',daemon=True);thread.start();return stop,thread


def main():
    from app.main import create_app
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args();application=create_app()
    try:
        while True:
            worked=run_one(application)
            if not args.watch:break
            if not worked:time.sleep(1)
    finally:application.state.engine.dispose()
if __name__=='__main__':main()
