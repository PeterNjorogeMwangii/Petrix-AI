import base64
import json
from types import SimpleNamespace
import uuid
from datetime import timedelta
from sqlalchemy.dialects.postgresql import insert
from app.models import RateLimit
from app.core.security import token_hash
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app import agent_service
from app.core.security import utcnow
from app.database import get_db
from app.models import AINetworkCredential, AITransferConnection, AIOutgoingSession, AIInboundSession, AgentConversation
from app.routes.agents import access
from app.ai_network import service, receiver, sender
from app.ai_network.schemas import ConnectAgent, CredentialUpdate
from app.workflows.numbering import walk

router=APIRouter(prefix='/api',tags=['AI agent network'])


@router.get('/workspaces/{workspace_id}/agents/{agent_id}/network/credentials')
def network_credentials(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,role,agent=access(request,db,workspace_id,agent_id=agent_id)
    if not agent_service.can_manage(db,workspace_id,role):raise HTTPException(403,'Manage AI Agents is required to read connection credentials.')
    return service.credential_payload(agent,db.get(AINetworkCredential,agent.id),request.app.state.settings)


@router.post('/workspaces/{workspace_id}/agents/{agent_id}/network/credentials')
def update_credentials(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:CredentialUpdate,request:Request,db:Session=Depends(get_db)):
    session,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    return service.update_credentials(db,agent,session.user_id,body,request.app.state.settings)


@router.post('/workspaces/{workspace_id}/agents/{agent_id}/network/credentials/reveal')
def reveal_secret(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=db.get(AINetworkCredential,agent_id)
    if not row:raise HTTPException(409,'Enable incoming handoffs to generate this agent secret.')
    return {'secret':service.decrypt(row.secret_ciphertext,request.app.state.settings)}


@router.get('/workspaces/{workspace_id}/agents/{agent_id}/network/transfer-connections')
def connections(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,role,agent=access(request,db,workspace_id,agent_id=agent_id)
    if not agent_service.can_manage(db,workspace_id,role):raise HTTPException(403,'Manage AI Agents is required for transfer connections.')
    return {'connections':[service.connection_payload(row) for row in db.scalars(select(AITransferConnection).where(AITransferConnection.source_id==agent_id,AITransferConnection.workspace_id==workspace_id).order_by(AITransferConnection.target_name,AITransferConnection.id))]}


@router.post('/workspaces/{workspace_id}/agents/{agent_id}/network/transfer-connections')
def connect(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:ConnectAgent,request:Request,db:Session=Depends(get_db)):
    session,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    domain=service.canonical_domain(body.domain,request.app.state.settings)
    if service.is_local(domain,request.app.state.settings) and body.agent_id==agent_id:raise HTTPException(422,'Choose another AI agent.')
    if body.domain.startswith('http:') and not service.is_local(domain,request.app.state.settings):raise HTTPException(422,'External receiving agents require HTTPS.')
    # Do not hold a database connection while calling another local instance/session.
    db.commit()
    try:
        verified=request.app.state.ai_network_client.call(request,domain,'verify',body.agent_id,body.secret.get_secret_value())
        target=service.verified_agent(verified['agent'],body.agent_id)
        if verified.get('version')!=1 or target['id']!=str(body.agent_id) or not isinstance(target['name'],str) or not 1<=len(target['name'])<=100:raise ValueError()
        if service.canonical_domain(verified['domain'],request.app.state.settings)!=domain:raise ValueError()
    except (HTTPException,ValueError,KeyError,TypeError):raise HTTPException(422,'Unable to verify the AI agent. Check its ID, secret, enabled Network settings, and platform domain.') from None
    # Recheck membership/permission after the network request, then serialize saves.
    session,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=db.scalar(select(AITransferConnection).where(AITransferConnection.source_id==agent_id,AITransferConnection.target_id==body.agent_id,AITransferConnection.domain==domain))
    if not row:
        row=AITransferConnection(workspace_id=workspace_id,source_id=agent_id,target_id=body.agent_id,target_name=target['name'],domain=domain,secret_ciphertext='',verified_at=utcnow());db.add(row)
    row.target_name=target['name'];row.secret_ciphertext=service.encrypt(body.secret.get_secret_value(),request.app.state.settings);row.verified_at=utcnow()
    agent_service.record_change(db,agent,session.user_id,'ai_transfer_connected','Verified transfer connection to {}.'.format(row.target_name));db.commit()
    return service.connection_payload(row)


@router.post('/workspaces/{workspace_id}/agents/{agent_id}/network/transfer-connections/{connection_id}/delete',status_code=204)
def delete_connection(workspace_id:uuid.UUID,agent_id:uuid.UUID,connection_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id);row=service.owned_connection(db,agent,connection_id)
    from app.workflows.service import get_document
    if any(node['type']=='ai_agent_handoff' and node['config']['connection_id']==str(row.id) for node in walk(get_document(db,agent_id).nodes)):
        raise HTTPException(409,'Remove this transfer connection from the workflow before deleting it.')
    if db.scalar(select(AIOutgoingSession.conversation_id).where(AIOutgoingSession.connection_id==row.id,AIOutgoingSession.status=='starting')):
        raise HTTPException(409,'A saved transfer is still awaiting confirmation.')
    db.delete(row);agent_service.record_change(db,agent,session.user_id,'ai_transfer_disconnected','AI transfer connection removed.');db.commit()


def owned_conversation(db,request,workspace_id,agent_id,conversation_id,mutation=False):
    session,_,_,_=access(request,db,workspace_id,mutation=mutation,agent_id=agent_id,lock=False)
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==conversation_id,AgentConversation.agent_id==agent_id,
        AgentConversation.workspace_id==workspace_id,AgentConversation.user_id==session.user_id))
    if not conversation:raise HTTPException(404,'Conversation not found.')
    return conversation


@router.post('/workspaces/{workspace_id}/agents/{agent_id}/tester/conversations/{conversation_id}/ai-retry')
def retry(workspace_id:uuid.UUID,agent_id:uuid.UUID,conversation_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    conversation=owned_conversation(db,request,workspace_id,agent_id,conversation_id,True);row=db.get(AIOutgoingSession,conversation_id)
    if not row:raise HTTPException(409,'This chat has no AI transfer to retry.')
    return sender.retry(db,conversation,row,request)


@router.get('/workspaces/{workspace_id}/agents/{agent_id}/tester/conversations/{conversation_id}/ai-files/{file_id}')
def proxy_file(workspace_id:uuid.UUID,agent_id:uuid.UUID,conversation_id:uuid.UUID,file_id:uuid.UUID,request:Request,inline:bool=False,db:Session=Depends(get_db)):
    conversation=owned_conversation(db,request,workspace_id,agent_id,conversation_id);row=db.get(AIOutgoingSession,conversation_id)
    if not row:raise HTTPException(404,'Attachment not found.')
    result=sender.download(db,conversation,row,file_id,request);raw=base64.b64decode(result['data'])
    from app.workflows.assets import file_response
    return file_response(SimpleNamespace(data=raw,file_name=result['name'],size_bytes=len(raw),content_type=result['content_type']),request,inline)


async def bounded_json(request,maximum=2*1024*1024):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>maximum:raise HTTPException(413,'Network request is too large.')
    try:return json.loads(data,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):raise HTTPException(422,'Invalid network JSON.') from None


def peer_rate(db,request,identifier=None):
    now=utcnow();identity=str(identifier) if identifier else (request.client.host if request.client else 'unknown')
    key=token_hash('ai-network-read:{}:{}'.format(identity,int(now.timestamp())//60))
    statement=insert(RateLimit).values(key=key,count=1,expires_at=now+timedelta(minutes=2))
    count=db.execute(statement.on_conflict_do_update(index_elements=[RateLimit.key],set_={'count':RateLimit.count+1}).returning(RateLimit.count)).scalar_one();db.commit()
    if count>(600 if identifier else 120):raise HTTPException(429,'Too many AI network requests. Wait a minute and try again.')


def secret(request):return request.headers.get('x-petrix-agent-secret','')[:201]
def bearer(request):
    value=request.headers.get('authorization','')
    return value[7:208] if value.startswith('Bearer ') else ''


@router.post('/ai-network/agents/{agent_id}/verify')
def peer_verify(agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    peer_rate(db,request)
    return receiver.verify(db,agent_id,secret(request),request.app.state.settings)


@router.post('/ai-network/agents/{agent_id}/handoffs')
async def peer_start(agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    peer_rate(db,request)
    service.authenticate_agent(db,agent_id,secret(request));payload=await bounded_json(request)
    try:return await run_in_threadpool(receiver.start,db,agent_id,secret(request),payload,request)
    except ValidationError:raise HTTPException(422,'Invalid network handoff payload.') from None


@router.post('/ai-network/sessions/{session_id}/events')
async def peer_event(session_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    peer_rate(db,request,session_id)
    db.info['network_settings']=request.app.state.settings;row,agent,key=receiver.session_auth(db,session_id,bearer(request));payload=await bounded_json(request,30*1024*1024)
    try:return await run_in_threadpool(receiver.event,db,row,agent,key,payload,request)
    except ValidationError:raise HTTPException(422,'Invalid network event payload.') from None


@router.get('/ai-network/sessions/{session_id}/snapshot')
def peer_snapshot(session_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    peer_rate(db,request,session_id)
    db.info['network_settings']=request.app.state.settings;row,_,_=receiver.session_auth(db,session_id,bearer(request))
    return {'version':1,'result':receiver.snapshot(db,row,request)}


@router.get('/ai-network/sessions/{session_id}/files/{file_id}')
def peer_file(session_id:uuid.UUID,file_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    peer_rate(db,request,session_id)
    db.info['network_settings']=request.app.state.settings;row,_,_=receiver.session_auth(db,session_id,bearer(request))
    return receiver.download(db,row,file_id,request)
