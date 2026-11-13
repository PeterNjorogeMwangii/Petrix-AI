import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import agent_service
from app.agent_schemas import LANGUAGES
from app.auth_service import current_session, require_csrf, user_context, workspace_payload
from app.database import get_db
from app.inbox import service
from app.inbox.schemas import AssignmentUpdate, ChatMutation, ChatReply, MemberAdd, ProfileUpdate
from app.models import AgentConversation, Membership, SupportTeam, User, Workspace
from app.workflows.schemas import TesterEvent

router=APIRouter(prefix='/api',tags=['Inbox'])


def access(request,db,workspace_id,mutation=False,manage=False):
    session=current_session(request,db)
    if mutation:require_csrf(request,session)
    workspace,role=agent_service.workspace_for(db,workspace_id,session.user_id,manage=manage,lock=manage)
    return session,workspace,role


@router.get('/workspaces')
def workspaces(request:Request,db:Session=Depends(get_db)):
    session=current_session(request,db)
    return {'workspaces':[workspace_payload(workspace,role,db) for workspace,role in db.execute(select(Workspace,Membership.role)
        .join(Membership,Membership.workspace_id==Workspace.id).where(Membership.user_id==session.user_id).order_by(Membership.created_at,Membership.id))]}


@router.post('/workspaces/{workspace_id}/select')
def select_workspace(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    session.active_workspace_id=workspace_id;db.commit()
    return user_context(db,db.get(User,session.user_id),workspace_id)


@router.get('/workspaces/{workspace_id}/support-options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id)
    return {'languages':[{'code':code,'name':name} for code,name in LANGUAGES], 'agents':[{key:value for key,value in person.items() if key!='last_assigned_at'} for person in service.roster(db,workspace_id)],
        'teams':[{'id':str(team.id),'name':team.name,'member_ids':team.member_ids} for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))]}


@router.get('/workspaces/{workspace_id}/members')
def members(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,role=access(request,db,workspace_id)
    return {'members':[{key:p[key] for key in ('id','name','email','role')} for p in service.roster(db,workspace_id)],'can_manage':role in {'owner','admin'}}


@router.post('/workspaces/{workspace_id}/members',status_code=201)
def add_member(workspace_id:uuid.UUID,body:MemberAdd,request:Request,db:Session=Depends(get_db)):
    session,_,role=access(request,db,workspace_id,mutation=True)
    if role not in {'owner','admin'}:raise HTTPException(403,'Only owners and administrators can add workspace members.')
    agent_service.workspace_for(db,workspace_id,session.user_id,lock=True)
    user=db.scalar(select(User).where(User.email==body.email.strip().lower()))
    if not user:raise HTTPException(422,'This person needs to create a Petrix AI account first. Then add their account email here.')
    if db.scalar(select(Membership.id).where(Membership.workspace_id==workspace_id,Membership.user_id==user.id)):
        raise HTTPException(409,'This account is already a member of the workspace.')
    db.add(Membership(workspace_id=workspace_id,user_id=user.id,role='member'));db.commit()
    return {'id':str(user.id),'name':user.full_name or user.email.split('@')[0],'email':user.email,'role':'member'}


@router.get('/workspaces/{workspace_id}/chat-assignment')
def get_assignment(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,role=access(request,db,workspace_id)
    value=service.assignment(db,workspace_id)
    return {'mode':value.mode,'max_active_chats':value.max_active_chats,'revision':value.revision,'can_manage':agent_service.can_manage(db,workspace_id,role)}


@router.post('/workspaces/{workspace_id}/chat-assignment')
def set_assignment(workspace_id:uuid.UUID,body:AssignmentUpdate,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,manage=True)
    value=service.assignment(db,workspace_id,lock=True)
    if value.revision!=body.expected_revision:raise HTTPException(409,'Chat assignment changed. Reload before saving.')
    if value.mode!=body.mode or value.max_active_chats!=body.max_active_chats:
        value.mode=body.mode;value.max_active_chats=body.max_active_chats;value.revision+=1
    db.commit();return {'mode':value.mode,'max_active_chats':value.max_active_chats,'revision':value.revision,'can_manage':True}


@router.get('/workspaces/{workspace_id}/inbox/profile')
def profile(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id)
    return service.profile_payload(db,workspace_id,session.user_id)


@router.post('/workspaces/{workspace_id}/inbox/profile')
def update_profile(workspace_id:uuid.UUID,body:ProfileUpdate,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    return service.update_profile(db,workspace_id,session,body)


@router.post('/workspaces/{workspace_id}/inbox/heartbeat')
def heartbeat(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    return service.heartbeat(db,workspace_id,session)


@router.get('/workspaces/{workspace_id}/inbox/chats')
def list_chats(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id)
    # Bound the initial queue page, exposing only conversations this person may handle.
    conversations=db.scalars(select(AgentConversation).where(AgentConversation.workspace_id==workspace_id,
        AgentConversation.live_status.in_(['pending','active','closed']),
        (AgentConversation.live_status=='pending') | (AgentConversation.live_assigned_user_id==session.user_id)).order_by(AgentConversation.live_updated_at.desc(),AgentConversation.id.desc()).limit(500)).all()
    people=service.roster(db,workspace_id);cache={};visible=[]
    for conversation in conversations:
        if conversation.live_status=='pending':
            routing=conversation.live_context['routing'];key=json.dumps(routing,sort_keys=True)
            if key not in cache:cache[key]={person['id'] for person in service.eligible(db,workspace_id,routing,people=people)}
            if str(session.user_id) not in cache[key]:continue
        visible.append(service.summary(conversation))
    return {'chats':visible}


@router.get('/workspaces/{workspace_id}/inbox/chats/{conversation_id}')
def detail(workspace_id:uuid.UUID,conversation_id:uuid.UUID,request:Request,before:uuid.UUID=Query(default=None),db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id)
    conversation=service.chat(db,workspace_id,conversation_id,session.user_id)
    return {**service.summary(conversation),**service.messages(db,conversation,before),'custom_fields':conversation.custom_fields,
        'automation_error':(conversation.workflow_state.get('close_automation') or {}).get('error') or (conversation.workflow_state.get('feedback_automation') or {}).get('error')}


@router.post('/workspaces/{workspace_id}/inbox/chats/{conversation_id}/claim')
def claim(workspace_id:uuid.UUID,conversation_id:uuid.UUID,body:ChatMutation,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    conversation=service.chat(db,workspace_id,conversation_id,session.user_id,lock=True)
    service.claim(db,conversation,session,body.expected_revision)
    return {**service.summary(conversation),**service.messages(db,conversation)}


@router.post('/workspaces/{workspace_id}/inbox/chats/{conversation_id}/reply')
def reply(workspace_id:uuid.UUID,conversation_id:uuid.UUID,body:ChatReply,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    conversation=service.chat(db,workspace_id,conversation_id,session.user_id,lock=True)
    service.reply(db,conversation,session,body)
    return {**service.summary(conversation),**service.messages(db,conversation)}


@router.post('/workspaces/{workspace_id}/inbox/chats/{conversation_id}/close')
def close_chat(workspace_id:uuid.UUID,conversation_id:uuid.UUID,body:ChatMutation,request:Request,db:Session=Depends(get_db)):
    session,_,_=access(request,db,workspace_id,mutation=True)
    conversation=service.chat(db,workspace_id,conversation_id,session.user_id)
    if not conversation.agent_id:raise HTTPException(409,'The AI agent for this live chat is unavailable.')
    from app.routes.workflows import process_event
    result=process_event(workspace_id,conversation.agent_id,TesterEvent(event='chat_closed',conversation_id=conversation.id),request,db,
        inbox_actor=session,inbox_revision=body.expected_revision)
    return {**service.summary(conversation),**service.messages(db,conversation),'automation_error':result.get('automation_error')}
