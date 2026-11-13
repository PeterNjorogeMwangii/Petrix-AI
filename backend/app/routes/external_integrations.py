import uuid
from copy import deepcopy
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.security import utcnow
from app.routes.inbox import access
from app.routes.agents import access as agent_access
from app.models import AgentConversation, ExternalConversation
from app.external_handoff import service
from app.external_handoff.schemas import IntegrationUpdate, LinkConversation, Platform, Revision, ReviewTransfer

router=APIRouter(prefix='/api/workspaces/{workspace_id}',tags=['External integrations'])


@router.get('/external-integrations/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id)
    return {'platforms':[{'platform':p,'name':name,'active':service.active(service.integration(db,workspace_id,p))} for p,name in service.PLATFORMS.items()]}


@router.get('/external-integrations')
def list_integrations(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,manage=True)
    return {'integrations':[service.payload(service.integration(db,workspace_id,p),p) for p in service.PLATFORMS]}


@router.post('/external-integrations/{platform}')
def save_integration(workspace_id:uuid.UUID,platform:Platform,body:IntegrationUpdate,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,manage=True)
    return service.save(db,workspace_id,platform,body,request.app.state.settings)


@router.post('/external-integrations/{platform}/verify')
def verify_integration(workspace_id:uuid.UUID,platform:Platform,body:Revision,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,manage=True)
    row=service.integration(db,workspace_id,platform,lock=True)
    if not row or row.revision!=body.expected_revision:raise HTTPException(409,'Integration changed. Reload before verifying.')
    try:verified=request.app.state.external_handoff_client.verify(platform,row.config,service.credentials(row,request.app.state.settings))
    except HTTPException:raise
    except Exception:verified=False
    row.verified_at=utcnow() if verified else None;row.revision+=1;db.commit()
    return {**service.payload(row,platform),'verification_error':'' if verified else 'Unable to verify credentials and handoff access. Check the platform setup and permissions.'}


@router.post('/agents/{agent_id}/tester/conversations/{conversation_id}/external-link')
def link_conversation(workspace_id:uuid.UUID,agent_id:uuid.UUID,conversation_id:uuid.UUID,body:LinkConversation,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=agent_access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==conversation_id,AgentConversation.workspace_id==workspace_id,
        AgentConversation.agent_id==agent_id,AgentConversation.user_id==session.user_id).with_for_update())
    if not conversation:raise HTTPException(404,'Conversation not found.')
    result=service.link(db,conversation,body,request.app.state.settings,request.app.state.external_handoff_client)
    conversation.workflow_state={**conversation.workflow_state,'external_handoff':result};db.commit()
    return result


@router.post('/agents/{agent_id}/tester/conversations/{conversation_id}/external-review')
def review_transfer(workspace_id:uuid.UUID,agent_id:uuid.UUID,conversation_id:uuid.UUID,body:ReviewTransfer,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=agent_access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    conversation=db.scalar(select(AgentConversation).where(AgentConversation.id==conversation_id,AgentConversation.workspace_id==workspace_id,
        AgentConversation.agent_id==agent_id,AgentConversation.user_id==session.user_id).with_for_update())
    if not conversation:raise HTTPException(404,'Conversation not found.')
    binding=db.get(ExternalConversation,conversation_id)
    if not binding or binding.revision!=body.expected_revision or binding.status not in {'uncertain','sending'}:
        raise HTTPException(409,'Only an unconfirmed transfer can be reviewed. Reload its current state.')
    if binding.status=='sending' and binding.updated_at>utcnow()-timedelta(minutes=2):
        raise HTTPException(409,'The transfer is still in progress. Wait for its result before reviewing.')
    binding.status='transferred' if body.resolution=='transferred' else 'failed';binding.error='';binding.revision+=1;binding.updated_at=utcnow();conversation.live_updated_at=binding.updated_at
    state=deepcopy(conversation.workflow_state);state['closed']=binding.status=='transferred'
    if not state['closed']:state.pop('closed_at',None);state.pop('close_source',None)
    state['external_handoff']=service.public_status(db,conversation);conversation.workflow_state=state
    from app.models import AgentChange
    db.add(AgentChange(workspace_id=workspace_id,agent_id=agent_id,agent_name=conversation.agent_name,actor_id=session.user_id,
        action='external_handoff_review',summary='Reviewed external handoff: {}.'.format(binding.status)))
    db.commit();return state['external_handoff']
