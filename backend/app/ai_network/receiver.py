import base64
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
import re
import uuid
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from app import agent_service
from app.core.security import new_token, token_hash, utcnow
from app.models import Agent, AgentAttachment, AgentConversation, AgentMessage, AINetworkCredential, AIInboundSession, AIInboundRequest
from app.ai_network import privacy, service
from app.ai_network.schemas import StartHandoff, PeerEvent
from app.workflows.schemas import TesterEvent


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def verify(db,agent_id,secret,settings):
    agent,_=service.authenticate_agent(db,agent_id,secret)
    return {'version':1,'agent':{'id':str(agent.id),'name':agent.name,'languages':agent.languages,'default_language':agent.default_language},'domain':settings.api_base_url}


def session_auth(db,identifier,token):
    import hmac
    row=db.get(AIInboundSession,identifier)
    if not row or row.expires_at<=utcnow() or not hmac.compare_digest(row.token_hash,token_hash(token or '')):raise HTTPException(401,'Network session authentication failed.')
    credential=db.get(AINetworkCredential,row.agent_id)
    if not credential or not credential.enabled or credential.revision!=row.credential_revision:raise HTTPException(401,'Network session was revoked.')
    agent,credential=service.authenticate_agent(db,row.agent_id,service.decrypt(credential.secret_ciphertext,db.info['network_settings']))
    return row,agent,credential


def snapshot(db,row,request):
    from app.routes.workflows import snapshot_payload
    conversation=db.get(AgentConversation,row.conversation_id)
    # A receiving agent may itself have handed off. Keep that chain transparent.
    from app.ai_network.sender import refresh
    outgoing=db.get(service.AIOutgoingSession,conversation.id)
    if outgoing and outgoing.status!='failed':
        db.commit();result=refresh(db,conversation,outgoing,request)
    else:result=snapshot_payload(db,conversation)
    if row.initial_error and not result.get('notice'):result['notice']=row.initial_error
    result['variables']=privacy.public_variables(result.get('variables',{}))
    return result


def start(db,agent_id,secret,payload,request):
    settings=request.app.state.settings
    agent,credential=service.authenticate_agent(db,agent_id,secret)
    value=StartHandoff.model_validate(payload)
    trail=[hop.model_dump(mode='json') for hop in value.trail]
    canonical=service.canonical_domain(settings.api_base_url,settings)
    if any(hop['agent_id']==str(agent_id) and service.canonical_domain(hop['domain'],settings)==canonical for hop in trail):
        raise HTTPException(422,'AI handoff loop detected.')
    row=db.scalar(select(AIInboundSession).where(AIInboundSession.agent_id==agent_id,AIInboundSession.transfer_id==value.transfer_id).with_for_update())
    fingerprint=digest(value.model_dump(mode='json'))
    if row:
        if row.start_digest!=fingerprint:raise HTTPException(409,'This transfer ID was already used with different context.')
        if row.status!='ready':raise HTTPException(409,'The receiving agent is still starting. Retry this same transfer later.')
        if row.credential_revision!=credential.revision or row.expires_at<=utcnow():raise HTTPException(401,'This network session was revoked or expired.')
        db.commit()
        return {'version':1,'session_id':str(row.id),'token':service.decrypt(row.token_ciphertext,settings),'agent':verify(db,agent_id,secret,settings)['agent'],'result':snapshot(db,row,request)}
    # Serialize duplicate starts through the receiving credential row. Refresh all
    # state after taking it so two authenticated requests cannot start two chats.
    db.scalar(select(AINetworkCredential).where(AINetworkCredential.agent_id==agent_id).with_for_update())
    existing=db.scalar(select(AIInboundSession.id).where(AIInboundSession.agent_id==agent_id,AIInboundSession.transfer_id==value.transfer_id))
    if existing:
        db.rollback();return start(db,agent_id,secret,payload,request)
    context=privacy.context(value.context)
    language=value.language if value.language in agent.languages else agent.default_language
    conversation=AgentConversation(workspace_id=agent.workspace_id,agent_id=agent.id,user_id=credential.actor_id,agent_name=agent.name,
        workflow_state={'variables':{'_transferred_context':context},'language':language,'channel':value.channel})
    db.add(conversation);db.flush()
    for item in value.history:
        if item.content:db.add(AgentMessage(conversation_id=conversation.id,role=item.role,content=privacy.redact_history(item.content,value.context),message_data={'transferred_history':True}))
    token=new_token();row=AIInboundSession(agent_id=agent.id,conversation_id=conversation.id,transfer_id=value.transfer_id,
        credential_revision=credential.revision,token_hash=token_hash(token),token_ciphertext=service.encrypt(token,settings),
        trail=trail+[{'agent_id':str(agent.id),'domain':canonical}],start_digest=fingerprint,status='running',initial_error='',expires_at=utcnow()+timedelta(days=7))
    db.add(row);db.commit()
    from app.routes.workflows import process_event
    try:
        process_event(agent.workspace_id,agent.id,TesterEvent(event='chat_started',language=language,channel=value.channel),request,db,
            network_actor=service.NetworkActor(credential.actor_id,conversation.id,agent.id))
    except HTTPException:
        db.rollback();row=db.get(AIInboundSession,row.id);row.initial_error='The receiving agent accepted the conversation but could not complete its starting workflow. Check its workflow or send a new message.'
    except Exception:
        db.rollback();row=db.get(AIInboundSession,row.id);row.initial_error='The receiving agent accepted the conversation but its starting workflow needs attention.'
    row.status='ready';db.commit()
    return {'version':1,'session_id':str(row.id),'token':token,'agent':{'id':str(agent.id),'name':agent.name,'languages':agent.languages,'default_language':agent.default_language},'result':snapshot(db,row,request)}


def event(db,row,agent,credential,payload,request):
    value=PeerEvent.model_validate(payload)
    data=dict(value.event)
    if data.get('event')=='chat_started':raise HTTPException(422,'A network session is already started.')
    data['conversation_id']=str(row.conversation_id)
    body=TesterEvent.model_validate(data)
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==row.conversation_id).with_for_update())
    record=db.get(AIInboundRequest,(row.id,value.request_id));fingerprint=digest(value.model_dump(mode='json'))
    if record:
        if record.digest!=fingerprint:raise HTTPException(409,'This request ID was already used with different input.')
        if record.status!='completed':raise HTTPException(409,'This network request is already being processed; it will not be replayed.')
        return record.response
    uploads=None
    if value.files:
        from app.workflows import attachments
        uploads=[];total=0
        for item in value.files:
            try:
                raw=base64.b64decode(item['data'],validate=True)
                if not raw or len(raw)>attachments.MAX_FILE_BYTES:raise ValueError()
                name=item['name'];media=item['content_type']
                if not isinstance(name,str) or not 1<=len(name)<=255 or any(ord(c)<32 for c in name) or '/' in name or '\\' in name:raise ValueError()
                if not isinstance(media,str) or len(media)>120 or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+',media):raise ValueError()
            except (KeyError,ValueError,TypeError):raise HTTPException(422,'Invalid network attachment.') from None
            total+=len(raw)
            if total>attachments.MAX_BATCH_BYTES:raise HTTPException(413,'Upload no more than 20 MiB at a time.')
            uploads.append({'data':raw,'file_name':name,'content_type':media,'size_bytes':len(raw)})
        if body.event!='attachment_received':raise HTTPException(422,'Files require an attachment event.')
    record=AIInboundRequest(session_id=row.id,request_id=value.request_id,digest=fingerprint,status='running',response={});db.add(record);db.commit()
    from app.routes.workflows import process_event
    try:
        result=process_event(agent.workspace_id,agent.id,body,request,db,upload_payloads=uploads,
            network_actor=service.NetworkActor(credential.actor_id,row.conversation_id,agent.id,value.request_id))
        result['variables']=privacy.public_variables(result.get('variables',{}))
        response={'version':1,'result':result}
    except HTTPException as error:
        db.rollback();response={'version':1,'error':'The receiving agent could not process this request. Check its current chat/workflow state.','status_code':error.status_code}
    except Exception:
        db.rollback();response={'version':1,'error':'The receiving agent could not finish processing this request.','status_code':503}
    record=db.get(AIInboundRequest,(row.id,value.request_id));record.status='completed';record.response=deepcopy(response);db.commit()
    return response


def download(db,row,file_id,request):
    file=db.scalar(select(AgentAttachment).where(AgentAttachment.id==file_id,AgentAttachment.conversation_id==row.conversation_id))
    if file:return {'name':file.file_name,'content_type':file.content_type,'data':base64.b64encode(file.data).decode()}
    outgoing=db.get(service.AIOutgoingSession,row.conversation_id)
    if outgoing:
        from app.ai_network.sender import download as forward
        conversation=db.get(AgentConversation,row.conversation_id);db.commit()
        return forward(db,conversation,outgoing,file_id,request)
    raise HTTPException(404,'Attachment not found in this network conversation.')


def local_call(operation,identifier,credential,payload,request,db):
    db.info['network_settings']=request.app.state.settings
    if operation=='verify':return verify(db,uuid.UUID(str(identifier)),credential,request.app.state.settings)
    if operation=='start':return start(db,uuid.UUID(str(identifier)),credential,payload,request)
    row,agent,key=session_auth(db,uuid.UUID(str(identifier)),credential)
    if operation=='event':return event(db,row,agent,key,payload,request)
    if operation=='snapshot':return {'version':1,'result':snapshot(db,row,request)}
    if operation=='file':return download(db,row,uuid.UUID(str(payload['file_id'])),request)
    raise HTTPException(404,'Network operation not found.')
