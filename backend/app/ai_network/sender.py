import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit
from fastapi import HTTPException
from sqlalchemy import select, Text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from app.core.security import utcnow
from app.models import AgentAttachment, AgentConversation, AgentMessage, AIOutgoingSession
from app.ai_network import service, privacy
from app.workflows import attachments

MESSAGE_FIELDS={'quick_replies','source','author_name','node_id','survey','attachments','carousel','graph','source_request_id'}
RESULT_FIELDS={'workflow_revision','language','closed','feedback_submitted','waiting','variables','custom_fields','survey','feedback','feedback_confirmation','channel',
    'external_handoff','automation_error','closed_at','close_source','csat_rating','attachments','matching','live_chat','notice','trace','ai_handoff'}


def _client(request,row,operation,payload=None):
    settings=request.app.state.settings
    if operation=='start':
        if not row.connection_id:raise HTTPException(409,'The AI transfer connection was removed.')
        connection=request.state.ai_connection if hasattr(request.state,'ai_connection') else None
        if connection is None:
            # Materialize the secret before releasing the caller's transaction.
            raise HTTPException(409,'The AI transfer connection is unavailable.')
        credential=service.decrypt(connection.secret_ciphertext,settings);identifier=row.target_id
    else:
        credential=service.decrypt(row.token_ciphertext,settings);identifier=row.remote_session_id
    return request.app.state.ai_network_client.call(request,row.domain,operation,identifier,credential,payload)


def public(row):
    if not row:return None
    leaf=(row.last_result.get('result') or {}).get('ai_handoff') or {}
    remote=row.last_result.get('result') or {}
    finished=bool(remote.get('closed') and not remote.get('feedback') and not (remote.get('live_chat') or {}).get('resume_active'))
    return {'status':'closed' if row.status=='active' and finished else row.status,'agent_id':leaf.get('agent_id',str(row.target_id)),'name':leaf.get('name',row.target_name),
        'domain':leaf.get('domain',row.domain),'pending':bool(row.pending) or row.status=='starting','error':row.error,
        'languages':leaf.get('languages',row.last_result.get('languages',[]))}


def _rewrite_files(value,conversation,file_map=None):
    file_map=file_map or {}
    if type(value) is list:return [_rewrite_files(v,conversation,file_map) for v in value]
    if type(value) is not dict:return value
    updated={key:_rewrite_files(child,conversation,file_map) for key,child in value.items()}
    if all(key in value for key in ('id','url','name','content_type')):
        identifier=uuid.UUID(str(value['id']));url=value['url']
        if not isinstance(url,str) or len(url)>2000:raise ValueError()
        if url.startswith('/api/'):
            if not re.fullmatch(r'/api/workspaces/[a-f0-9-]{36}/agents/[a-f0-9-]{36}/tester/(?:attachments/[a-f0-9-]{36}|conversations/[a-f0-9-]{36}/ai-files/[a-f0-9-]{36})',url):raise ValueError()
            if str(identifier) in file_map:
                updated['id']=file_map[str(identifier)];updated['url']='/api/workspaces/{}/agents/{}/tester/attachments/{}'.format(conversation.workspace_id,conversation.agent_id,updated['id'])
            else:updated['url']='/api/workspaces/{}/agents/{}/tester/conversations/{}/ai-files/{}'.format(conversation.workspace_id,conversation.agent_id,conversation.id,identifier)
            updated['external']=False
        elif urlsplit(url).scheme not in {'https','http'} or urlsplit(url).username:raise ValueError()
    return updated


def apply_result(db,conversation,row,result):
    if not isinstance(result,dict) or not isinstance(result.get('messages'),list) or len(result['messages'])>500:raise ValueError()
    if len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())>2*1024*1024:raise ValueError()
    view={key:deepcopy(result[key]) for key in RESULT_FIELDS if key in result}
    if type(view.get('closed')) is not bool or type(view.get('variables')) is not dict or not isinstance(view.get('language'),str):raise ValueError()
    view['variables']=privacy.public_variables(view['variables'])
    view['custom_fields']={}
    view=_rewrite_files(view,conversation,row.last_result.get('file_map',{}))
    new=[]
    for raw in result['messages']:
        if raw.get('transferred_history'):continue
        if raw.get('role') not in {'user','assistant','event'} or not isinstance(raw.get('content'),str) or len(raw['content'])>16000:raise ValueError()
        remote_id=uuid.UUID(raw['id']);created=datetime.fromisoformat(raw['created_at'])
        if created.tzinfo is None:raise ValueError()
        local_id=uuid.uuid5(row.transfer_id,str(remote_id))
        request_id=raw.get('source_request_id')
        existing=db.get(AgentMessage,local_id)
        if not existing and request_id:
            existing=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==conversation.id,
                AgentMessage.message_data['source_request_id'].as_string()==str(request_id)))
        if existing:continue
        message=_rewrite_files({key:deepcopy(raw[key]) for key in MESSAGE_FIELDS if key in raw},conversation,row.last_result.get('file_map',{}))
        if raw['role']=='assistant':message.setdefault('author_name',row.target_name)
        # A snapshot poll and an acknowledged retry may both have missed the
        # initial read. The deterministic ID must be claimed atomically.
        claimed=db.execute(insert(AgentMessage).values(id=local_id,conversation_id=conversation.id,role=raw['role'],content=raw['content'],created_at=created,message_data=message)
            .on_conflict_do_nothing(index_elements=[AgentMessage.id]).returning(AgentMessage.id)).scalar_one_or_none()
        if claimed and raw['role']!='user':new.append({'id':str(local_id),'role':raw['role'],'content':raw['content'],'created_at':created.isoformat(),**message})
    row.last_result={**row.last_result,'result':view};row.error='';row.updated_at=utcnow()
    # The source interpreter never resumes. Only the receiver's public chat state
    # is projected; its private workflow stack, credentials and schema stay there.
    conversation.workflow_state={**conversation.workflow_state,'closed':view.get('closed',False),'language':view.get('language','en'),
        'ai_handoff':public(row),'stack':[]}
    return new


def response(db,conversation,row,messages=None):
    from app.routes.workflows import snapshot_payload
    view=deepcopy(row.last_result.get('result') or {})
    if not view:view=snapshot_payload(db,conversation)
    if messages is None:
        from app.inbox.service import messages as history
        messages=history(db,conversation)['messages']
    return {**view,'conversation_id':str(conversation.id),'messages':messages,'ai_handoff':public(row),
        'notice':row.error or view.get('notice','')}


def resume_start(db,conversation,row,request):
    connection=db.get(service.AITransferConnection,row.connection_id) if row.connection_id else None
    request.state.ai_connection=connection
    db.commit()
    try:
        result=_client(request,row,'start',row.start_payload)
        if result.get('version')!=1 or not isinstance(result.get('token'),str) or not 32<=len(result['token'])<=200:raise ValueError()
        session_id=uuid.UUID(result['session_id'])
        agent=service.verified_agent(result.get('agent'),row.target_id)
        row.target_name=agent['name']
        row.remote_session_id=session_id;row.token_ciphertext=service.encrypt(result['token'],request.app.state.settings);row.status='active'
        row.last_result={'languages':result['agent'].get('languages',[])}
        new=apply_result(db,conversation,row,result['result'])
        row.start_payload={};db.commit()
        return response(db,conversation,row,new)
    except (HTTPException,ValueError,KeyError,TypeError,OverflowError):
        db.rollback();row=db.get(AIOutgoingSession,conversation.id)
        row.error='The receiving AI agent has not confirmed the handoff. Retry the saved transfer; the originating workflow remains paused.';db.commit()
        return response(db,conversation,row,[])


def start(db,conversation,plan,state,request,origin_request_id=None):
    row=db.get(AIOutgoingSession,conversation.id)
    if row:raise HTTPException(409,'This conversation has already been handed off.')
    target=plan.connection
    history=db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==conversation.id).order_by(AgentMessage.created_at.desc(),AgentMessage.id.desc()).limit(40)).all()
    sanitized=[];size=0
    for item in history:
        content=privacy.redact_history(item.content,state.get('variables',{}))
        if item.role in {'user','assistant'} and size+len(content.encode())<=16384:
            sanitized.append({'role':item.role,'content':content[:4000]});size+=len(content.encode())
    row=AIOutgoingSession(conversation_id=conversation.id,connection_id=target.id,transfer_id=uuid.UUID(str(origin_request_id)) if origin_request_id else uuid.uuid4(),
        target_id=target.target_id,target_name=target.target_name,domain=target.domain,status='starting',remote_session_id=None,
        token_ciphertext='',start_payload={},pending={},last_result={},error='',updated_at=utcnow())
    row.start_payload={**plan.payload,'transfer_id':str(row.transfer_id),'history':list(reversed(sanitized))}
    state['ai_handoff']=public(row);state['stack']=[];state.pop('survey',None)
    conversation.workflow_state=deepcopy(state);db.add(row)
    try:db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'This AI transfer request was already started. Read the saved conversation state or retry the same start request.') from None
    return resume_start(db,conversation,row,request)


def resume_pending(db,conversation,row,request):
    pending=deepcopy(row.pending)
    wire={'request_id':pending['request_id'],'event':pending['event'],'files':[]}
    for identifier in pending.get('file_ids',[]):
        file=db.get(AgentAttachment,uuid.UUID(identifier))
        if not file or file.conversation_id!=conversation.id:raise HTTPException(409,'The saved network upload is unavailable.')
        wire['files'].append({'name':file.file_name,'content_type':file.content_type,'data':base64.b64encode(file.data).decode()})
    db.commit()
    try:
        result=_client(request,row,'event',wire)
        if result.get('version')!=1:raise ValueError()
        if result.get('error'):
            row.error='The receiving agent could not process the saved request. Check its workflow/chat status before sending a new message.'
            row.pending={};row.last_result={**row.last_result,'last_request_id':pending['request_id'],'last_digest':pending['digest']};
            failed=response(db,conversation,row,[]);row.last_result={**row.last_result,'last_response':deepcopy(failed)};db.commit()
            return failed
        remote_files=result['result'].get('attachments',[])
        if pending.get('file_ids') and len(remote_files)==len(pending['file_ids']):
            mapping={str(file['id']):identifier for file,identifier in zip(remote_files,pending['file_ids'])}
            row.last_result={**row.last_result,'file_map':{**row.last_result.get('file_map',{}),**mapping}}
        new=apply_result(db,conversation,row,result['result']);row.pending={}
        result_view=response(db,conversation,row,new)
        row.last_result={**row.last_result,'last_request_id':pending['request_id'],'last_digest':pending['digest'],'last_response':deepcopy(result_view)}
        db.commit();return result_view
    except (HTTPException,ValueError,KeyError,TypeError,OverflowError):
        db.rollback();row=db.get(AIOutgoingSession,conversation.id);row.error='The receiving AI agent is unavailable. Your saved request will not be duplicated when retried.';db.commit()
        return response(db,conversation,row,[])


def forward_event(db,conversation,row,body,request,uploads=None,network_actor=None):
    if row.status=='starting':
        result=resume_start(db,conversation,row,request)
        return response(db,conversation,row) if body.event=='chat_started' else result
    identifier=str(body.client_event_id or (network_actor.request_id if network_actor else None) or uuid.uuid4())
    data=body.model_dump(mode='json',exclude={'conversation_id','client_event_id'})
    content_hashes=[hashlib.sha256(item['data']).hexdigest()+':'+item['file_name']+':'+item['content_type'] for item in uploads or []]
    fingerprint=hashlib.sha256(json.dumps({'event':data,'files':content_hashes},sort_keys=True).encode()).hexdigest()
    if identifier==str(row.transfer_id):return response(db,conversation,row) if body.event=='chat_started' else response(db,conversation,row,[])
    if row.last_result.get('last_request_id')==identifier and row.last_result.get('last_response'):
        if row.last_result.get('last_digest')!=fingerprint:raise HTTPException(409,'This request ID was already used with different input.')
        return row.last_result['last_response']
    if row.pending:
        if row.pending['digest']!=fingerprint:raise HTTPException(409,'The previous request is awaiting confirmation. Retry it before sending another message.')
        return resume_pending(db,conversation,row,request)
    previous_input=db.scalar(select(AgentMessage.id).where(AgentMessage.conversation_id==conversation.id,AgentMessage.message_data['source_request_id'].as_string()==identifier))
    if previous_input:raise HTTPException(409,'This request was already completed. Read the current conversation state.')
    file_ids=[]
    for item in uploads or []:
        file=AgentAttachment(conversation_id=conversation.id,**item);db.add(file);db.flush();file_ids.append(str(file.id))
    if body.event in {'message_received','attachment_received'}:
        metadata={'source_request_id':identifier}
        if file_ids:metadata['attachments']=[attachments.metadata(db.get(AgentAttachment,uuid.UUID(i)),conversation.workspace_id,conversation.agent_id) for i in file_ids]
        db.add(AgentMessage(conversation_id=conversation.id,role='user',content=body.content if body.event=='message_received' else 'Uploaded: '+', '.join(item['file_name'] for item in uploads or []),message_data=metadata))
    row.pending={'request_id':identifier,'digest':fingerprint,'event':data,'file_ids':file_ids};row.error='';db.commit()
    return resume_pending(db,conversation,row,request)


def refresh(db,conversation,row,request):
    if row.status=='starting':
        if row.error:return response(db,conversation,row)
        return resume_start(db,conversation,row,request)
    if row.pending:
        if row.error:return response(db,conversation,row)
        return resume_pending(db,conversation,row,request)
    db.commit()
    try:
        result=_client(request,row,'snapshot')
        if result.get('version')!=1:raise ValueError()
        apply_result(db,conversation,row,result['result']);db.commit()
    except (HTTPException,ValueError,KeyError,TypeError):
        db.rollback();row=db.get(AIOutgoingSession,conversation.id);row.error='The receiving AI agent is currently unavailable. This conversation remains assigned to it.';db.commit()
    return response(db,conversation,row)


def retry(db,conversation,row,request):
    row.error='';db.commit()
    if row.status=='starting':return resume_start(db,conversation,row,request)
    if row.pending:return resume_pending(db,conversation,row,request)
    return refresh(db,conversation,row,request)


def download(db,conversation,row,identifier,request):
    if row.status!='active':raise HTTPException(409,'The receiving AI session is unavailable.')
    cached=db.get(AgentAttachment,uuid.uuid5(row.transfer_id,str(identifier)))
    if cached and cached.conversation_id==conversation.id:return {'name':cached.file_name,'content_type':cached.content_type,'data':base64.b64encode(cached.data).decode()}
    # Only a file ID already sent in this conversation may be proxied.
    visible=db.scalar(select(AgentMessage.id).where(AgentMessage.conversation_id==conversation.id,
        AgentMessage.message_data.cast(Text).contains(str(identifier))))
    if not visible:raise HTTPException(404,'Attachment not found in this conversation.')
    db.commit();result=_client(request,row,'file',{'file_id':str(identifier)})
    try:
        data=base64.b64decode(result['data'],validate=True)
        if not 0<len(data)<=20*1024*1024 or not isinstance(result['name'],str) or len(result['name'])>255 or not isinstance(result['content_type'],str):raise ValueError()
    except (KeyError,ValueError,TypeError):raise HTTPException(503,'The receiving attachment is unavailable.') from None
    media=result['content_type']
    if len(media)>120 or not re.fullmatch(r'[A-Za-z0-9!#$&^_.+\-]+/[A-Za-z0-9!#$&^_.+\-]+',media):media='application/octet-stream'
    db.add(AgentAttachment(id=uuid.uuid5(row.transfer_id,str(identifier)),conversation_id=conversation.id,file_name=result['name'],content_type=media,size_bytes=len(data),data=data));db.commit()
    return {'name':result['name'],'content_type':media,'data':result['data']}
