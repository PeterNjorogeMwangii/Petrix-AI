"""Workspace-scoped queues with current eligibility and expiring session presence."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select
from app import agent_service
from app.core.security import utcnow
from app.models import AgentChange, AgentConversation, AgentMessage, AuthSession, ChatAssignment, Membership, SupportAgent, SupportTeam, User

PRESENCE_SECONDS = 90


def assignment(db, workspace_id, lock=False):
    query=select(ChatAssignment).where(ChatAssignment.workspace_id==workspace_id)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    value=db.scalar(query)
    if not value:raise HTTPException(409,'Chat assignment storage is missing. Run python -m alembic upgrade head from backend.')
    return value


def ensure_profile(db,workspace_id,user_id):
    value=db.get(SupportAgent,(workspace_id,user_id))
    if not value:
        value=SupportAgent(workspace_id=workspace_id,user_id=user_id,languages=['en'],available=False,revision=1)
        db.add(value);db.flush()
    return value


def roster(db,workspace_id):
    members=db.execute(select(User,Membership.role).join(Membership,Membership.user_id==User.id)
        .where(Membership.workspace_id==workspace_id).order_by(User.created_at,User.id)).all()
    profiles={p.user_id:p for p in db.scalars(select(SupportAgent).where(SupportAgent.workspace_id==workspace_id))}
    teams=db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False))).all()
    session_ids=[p.presence_session_id for p in profiles.values() if p.presence_session_id]
    now=utcnow()
    sessions={s.id:s.user_id for s in db.scalars(select(AuthSession).where(AuthSession.id.in_(session_ids),AuthSession.revoked_at.is_(None),AuthSession.expires_at>now))} if session_ids else {}
    loads=dict(db.execute(select(AgentConversation.live_assigned_user_id,func.count()).where(AgentConversation.workspace_id==workspace_id,
        AgentConversation.live_status=='active').group_by(AgentConversation.live_assigned_user_id)).all())
    values=[]
    for user,role in members:
        profile=profiles.get(user.id)
        team_ids=[str(team.id) for team in teams if str(user.id) in (team.member_ids or [])]
        online=bool(profile and profile.available and profile.last_seen_at and profile.last_seen_at>=now-timedelta(seconds=PRESENCE_SECONDS)
            and sessions.get(profile.presence_session_id)==user.id and team_ids)
        values.append({'id':str(user.id),'name':user.full_name or user.email.split('@')[0], 'email':user.email,'role':role,
            'team_ids':team_ids,'languages':profile.languages if profile else ['en'],'available':online,
            'active_chats':loads.get(user.id,0),'last_assigned_at':profile.last_assigned_at if profile else None})
    return values


def eligible(db,workspace_id,routing,people=None):
    people=[p for p in (roster(db,workspace_id) if people is None else people) if p['team_ids']]
    if routing['assignment_mode']=='specific':
        teams=set(routing.get('team_ids',[]));users=set(routing.get('user_ids',[]))
        people=[p for p in people if p['id'] in users or teams.intersection(p['team_ids'])]
    if routing.get('assign_by_language'):
        people=[p for p in people if routing['language'] in p['languages']]
    return people


def profile_payload(db,workspace_id,user_id):
    profile=db.get(SupportAgent,(workspace_id,user_id))
    person=next((p for p in roster(db,workspace_id) if p['id']==str(user_id)),None)
    return {**person,'revision':profile.revision if profile else 0,'requested_available':profile.available if profile else False,
        'presence_seconds':PRESENCE_SECONDS}


def update_profile(db,workspace_id,session,body):
    assignment(db,workspace_id,lock=True)
    profile=db.get(SupportAgent,(workspace_id,session.user_id))
    if (profile.revision if profile else 0)!=body.expected_revision:raise HTTPException(409,'Your support profile changed. Reload before saving.')
    if body.available and not next((p['team_ids'] for p in roster(db,workspace_id) if p['id']==str(session.user_id)),[]):
        raise HTTPException(422,'Join a support team in Settings → Teams before becoming available.')
    profile=profile or ensure_profile(db,workspace_id,session.user_id)
    if profile.languages!=body.languages or profile.available!=body.available:
        profile.languages=body.languages;profile.available=body.available;profile.revision+=1
    profile.last_seen_at=utcnow();profile.presence_session_id=session.id
    db.commit();return profile_payload(db,workspace_id,session.user_id)


def heartbeat(db,workspace_id,session):
    profile=db.get(SupportAgent,(workspace_id,session.user_id))
    if profile and profile.available:
        profile.last_seen_at=utcnow();profile.presence_session_id=session.id;db.commit()
    return profile_payload(db,workspace_id,session.user_id)


class HandoffService:
    def __init__(self,db,workspace_id):self.db,self.workspace_id=db,workspace_id

    def offer(self,config,context):
        settings=assignment(self.db,self.workspace_id,lock=True)
        routing={key:deepcopy(config[key]) for key in ('assignment_mode','user_ids','team_ids','assign_by_language')}
        routing['language']=context.language
        if config['team_variable']:
            value=context.variables.get(config['team_variable'])
            identifier=value.get('id') if type(value) is dict else value
            try:identifier=str(uuid.UUID(str(identifier)))
            except (ValueError,TypeError,AttributeError):return None,'team_not_set'
            team=self.db.scalar(select(SupportTeam).where(SupportTeam.id==uuid.UUID(identifier),SupportTeam.workspace_id==self.workspace_id,SupportTeam.archived.is_(False)))
            if not team:return None,'team_unavailable'
            routing['team_ids']=list(dict.fromkeys(routing['team_ids']+[identifier]))
        people=eligible(self.db,self.workspace_id,routing)
        online=[p for p in people if p['available'] and p['active_chats']<settings.max_active_chats]
        if not people:return None,'no_eligible_agents'
        if config['availability_check'] and not online:return None,'no_available_agents'
        chosen=None
        if settings.mode!='manual' and online:
            def order(person):
                stamp=person['last_assigned_at'].timestamp() if person['last_assigned_at'] else 0
                return (person['active_chats'],stamp,person['id']) if settings.mode=='least_busy' else (stamp,person['id'])
            person=min(online,key=order);chosen=person['id']
            profile=ensure_profile(self.db,self.workspace_id,uuid.UUID(chosen));profile.last_assigned_at=utcnow()
        return {'routing':routing,'eligible_ids':[p['id'] for p in people],'assigned_id':chosen},None


def apply_handoff(db,conversation,state):
    request=state.pop('handoff_request',None)
    if not request:return
    context={**request,'return_pending':False,'resume_active':False}
    conversation.live_status='active' if request['assigned_id'] else 'pending'
    conversation.live_assigned_user_id=uuid.UUID(request['assigned_id']) if request['assigned_id'] else None
    conversation.live_context=context;conversation.live_revision+=1;conversation.live_updated_at=utcnow()
    state['closed']=False;state['feedback_submitted']=False
    for key in ('closed_at','closed_by','close_source','close_automation','feedback_automation','feedback_request','feedback_confirmation','csat_rating'):
        state.pop(key,None)


def can_view(db,conversation,user_id):
    if not db.scalar(select(Membership.id).where(Membership.workspace_id==conversation.workspace_id,Membership.user_id==user_id)):return False
    if conversation.live_status in {'active','closed'}:return conversation.live_assigned_user_id==user_id
    if conversation.live_status=='pending':return str(user_id) in {p['id'] for p in eligible(db,conversation.workspace_id,conversation.live_context['routing'])}
    return False


def chat(db,workspace_id,identifier,user_id,lock=False):
    query=select(AgentConversation).where(AgentConversation.id==identifier,AgentConversation.workspace_id==workspace_id)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    conversation=db.scalar(query)
    if not conversation or not can_view(db,conversation,user_id):raise HTTPException(404,'Live chat not found.')
    return conversation


def public_status(conversation):
    name=None
    context=conversation.live_context or {}
    return {'status':conversation.live_status,'revision':conversation.live_revision,'assigned_user_id':str(conversation.live_assigned_user_id) if conversation.live_assigned_user_id else None,
        'handoff_node_id':context.get('node_id'),'return_status':context.get('return_status'),'resume_active':bool(context.get('resume_active')),'updated_at':conversation.live_updated_at.isoformat() if conversation.live_updated_at else None}


def messages(db,conversation,before=None):
    query=select(AgentMessage).where(AgentMessage.conversation_id==conversation.id)
    if before:
        marker=db.scalar(select(AgentMessage).where(AgentMessage.id==before,AgentMessage.conversation_id==conversation.id))
        if not marker:raise HTTPException(404,'Message cursor not found.')
        query=query.where((AgentMessage.created_at<marker.created_at)|((AgentMessage.created_at==marker.created_at)&(AgentMessage.id<marker.id)))
    rows=db.scalars(query.order_by(AgentMessage.created_at.desc(),AgentMessage.id.desc()).limit(101)).all()
    return {'messages':[{'id':str(row.id),'role':row.role,'content':row.content,'created_at':row.created_at.isoformat(),**row.message_data} for row in reversed(rows[:100])],
        'has_more':len(rows)>100}


def summary(conversation):
    return {'id':str(conversation.id),'agent_id':str(conversation.agent_id) if conversation.agent_id else None,'agent_name':conversation.agent_name,
        'created_at':conversation.created_at.isoformat(),'language':conversation.workflow_state.get('language','en'),**public_status(conversation)}


def claim(db,conversation,session,expected):
    settings=assignment(db,conversation.workspace_id,lock=True)
    if conversation.live_revision!=expected or conversation.live_status!='pending':raise HTTPException(409,'This chat was already claimed or changed. Reload the queue.')
    person=next((p for p in eligible(db,conversation.workspace_id,conversation.live_context['routing']) if p['id']==str(session.user_id)),None)
    if not person or not person['available']:raise HTTPException(409,'Become available in Inbox before claiming this chat.')
    if person['active_chats']>=settings.max_active_chats:raise HTTPException(409,'You have reached the active chat limit.')
    conversation.live_status='active';conversation.live_assigned_user_id=session.user_id;conversation.live_revision+=1;conversation.live_updated_at=utcnow()
    ensure_profile(db,conversation.workspace_id,session.user_id).last_assigned_at=utcnow()
    audit(db,conversation,session.user_id,'claimed');db.commit()


def require_assignee(conversation,user_id,expected):
    if conversation.live_status!='active' or conversation.live_assigned_user_id!=user_id or conversation.live_revision!=expected:
        raise HTTPException(409,'This live chat changed or is no longer assigned to you. Reload before continuing.')


def reply(db,conversation,session,body):
    duplicate=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==conversation.id,
        AgentMessage.message_data['client_message_id'].as_string()==str(body.client_message_id)))
    if duplicate:return
    require_assignee(conversation,session.user_id,body.expected_revision)
    user=db.get(User,session.user_id)
    db.add(AgentMessage(conversation_id=conversation.id,role='assistant',content=body.content,
        message_data={'source':'live_agent','author_name':user.full_name or 'Support agent','client_message_id':str(body.client_message_id),'quick_replies':[]}))
    conversation.live_updated_at=utcnow();db.commit()


def audit(db,conversation,actor_id,action):
    db.add(AgentChange(workspace_id=conversation.workspace_id,agent_id=conversation.agent_id,agent_name=conversation.agent_name,
        actor_id=actor_id,action='live_chat_'+action,summary='Live chat {} {}.'.format(str(conversation.id)[:8],action)))


def frozen_document(conversation):
    context=conversation.live_context
    return SimpleNamespace(agent_id=conversation.agent_id,revision=context['workflow_revision'],nodes=deepcopy(context['nodes']))


def validate_references(db,workspace_id,nodes):
    from app.workflows.numbering import walk
    teams={str(team.id) for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))}
    users={str(value) for value in db.scalars(select(Membership.user_id).where(Membership.workspace_id==workspace_id))}
    for node in walk(nodes):
        if node['kind']=='action' and node['type']=='live_agent_handoff':
            if not set(node['config']['team_ids'])<=teams or not set(node['config']['user_ids'])<=users:
                raise HTTPException(422,'Choose active teams and workspace members for every Live Agent Handoff action.')
