"""Workspace-scoped queues with current eligibility and expiring session presence."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import uuid
from fastapi import HTTPException
from sqlalchemy import func, select
from app import agent_service
from app.core.security import utcnow
from app.models import Agent,AgentChange, AgentConversation, AgentMessage, AuthSession, ChatAssignment, Membership, SupportAgent, SupportTeam, User

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
        from app.models import UserProfile
        preference=db.get(UserProfile,user_id)
        value=SupportAgent(workspace_id=workspace_id,user_id=user_id,languages=[preference.language if preference else 'en'],available=False,revision=1)
        db.add(value);db.flush()
    return value


def roster(db,workspace_id):
    members=db.execute(select(User,Membership.role).join(Membership,Membership.user_id==User.id)
        .where(Membership.workspace_id==workspace_id).order_by(User.created_at,User.id)).all()
    profiles={p.user_id:p for p in db.scalars(select(SupportAgent).where(SupportAgent.workspace_id==workspace_id))}
    from app.models import UserProfile
    account_profiles={p.user_id:p for p in db.scalars(select(UserProfile).where(UserProfile.user_id.in_([u.id for u,_ in members])))}
    preferences={key:value.presence for key,value in account_profiles.items()}
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
        online=bool(preferences.get(user.id,'online')=='online' and profile and profile.available and profile.last_seen_at and profile.last_seen_at>=now-timedelta(seconds=PRESENCE_SECONDS)
            and sessions.get(profile.presence_session_id)==user.id and team_ids)
        values.append({'id':str(user.id),'name':user.full_name or user.email.split('@')[0], 'email':user.email,'role':role,
            'team_ids':team_ids,'languages':profile.languages if profile else [account_profiles[user.id].language if user.id in account_profiles else 'en'],'available':online,
            'presence':preferences.get(user.id,'online'),'active_chats':loads.get(user.id,0),'last_assigned_at':profile.last_assigned_at if profile else None})
    return values


def eligible(db,workspace_id,routing,people=None,space_id=None):
    people=[p for p in (roster(db,workspace_id) if people is None else people) if p['team_ids']]
    if routing['assignment_mode']=='specific':
        teams=set(routing.get('team_ids',[]));users=set(routing.get('user_ids',[]))
        people=[p for p in people if p['id'] in users or teams.intersection(p['team_ids'])]
    if routing.get('assign_by_language'):
        people=[p for p in people if routing['language'] in p['languages']]
    from app.workspace_settings import permissions as grants
    space_id=space_id or grants.default_space(db,workspace_id)
    def permitted(person):
        identifier=uuid.UUID(person['id']);teams=routing.get('team_ids',[]) or [None]
        return all(any(grants.record_allowed(db,workspace_id,identifier,'livechats',action,identifier,t,space_id) for t in teams) for action in ['view','manage'])
    return [person for person in people if permitted(person)]


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
    was_available=profile.available
    if body.available:
        from app.profile.service import profile as account_profile
        account=account_profile(db,session.user_id)
        if account.presence!='online':account.presence='online';account.revision+=1
    if profile.languages!=body.languages or profile.available!=body.available:
        profile.languages=body.languages;profile.available=body.available;profile.revision+=1
    if body.available and (not was_available or not profile.online_since or not profile.last_seen_at or profile.last_seen_at<utcnow()-timedelta(seconds=PRESENCE_SECONDS)):profile.online_since=utcnow()
    profile.last_seen_at=utcnow();profile.presence_session_id=session.id
    from app.analytics.tracking import touch
    touch(db,workspace_id,session)
    db.commit();return profile_payload(db,workspace_id,session.user_id)


def heartbeat(db,workspace_id,session):
    profile=db.get(SupportAgent,(workspace_id,session.user_id))
    if profile and profile.available:
        if not profile.last_seen_at or profile.last_seen_at<utcnow()-timedelta(seconds=PRESENCE_SECONDS):profile.online_since=utcnow()
        profile.last_seen_at=utcnow();profile.presence_session_id=session.id
        from app.analytics.tracking import touch
        touch(db,workspace_id,session);db.commit()
    return profile_payload(db,workspace_id,session.user_id)


class HandoffService:
    def __init__(self,db,workspace_id,conversation=None):self.db,self.workspace_id,self.conversation=db,workspace_id,conversation

    def offer(self,config,context):
        from app.teamspace.assignment import offer
        result=offer(self.db,self.workspace_id,config,context,self.conversation)
        if result is not None:return result
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
    old=conversation.live_context or {}
    context={**request,'return_pending':False,'resume_active':False,'handoff_at':utcnow().isoformat(),'voice_mode':False}
    context.setdefault('priority',False)
    if conversation.live_status!='none':
        context['priority']=bool(old.get('priority'));context['priority_type']=old.get('priority_type','skip_queue')
    context['primary_language']=old.get('primary_language') or old.get('routing',{}).get('language') or request['routing']['language']
    context['languages']=list(dict.fromkeys([context['primary_language'],*old.get('languages',[]),request['routing']['language']]))
    conversation.live_display_status=''
    conversation.live_status='active' if request['assigned_id'] else 'pending'
    conversation.live_assigned_user_id=uuid.UUID(request['assigned_id']) if request['assigned_id'] else None
    conversation.live_context=context;conversation.live_revision+=1;conversation.live_updated_at=utcnow()
    state['closed']=False;state['feedback_submitted']=False
    for key in ('closed_at','closed_by','close_source','close_automation','feedback_automation','feedback_request','feedback_confirmation','csat_rating'):
        state.pop(key,None)


def can_view(db,conversation,user_id):
    from app.workspace_settings import permissions as grants
    if grants.has_custom(db,conversation.workspace_id,user_id):
        if conversation.live_status=='pending':return str(user_id) in {person['id'] for person in eligible(db,conversation.workspace_id,conversation.live_context['routing'],space_id=__import__('app.teamspace.records',fromlist=['record_space']).record_space(db,'chats',conversation))}
        agent=db.get(Agent,conversation.agent_id) if conversation.agent_id else None
        teams=(conversation.live_context or {}).get('routing',{}).get('team_ids',[])
        return any(grants.record_allowed(db,conversation.workspace_id,user_id,'livechats','view',conversation.live_assigned_user_id,t,agent.teamspace_id if agent else None) for t in (teams or [None]))

    if not db.scalar(select(Membership.id).where(Membership.workspace_id==conversation.workspace_id,Membership.user_id==user_id)):return False
    from app.models import ExternalConversation
    external=db.get(ExternalConversation,conversation.id)
    if external and external.status in {'sending','transferred','uncertain'}:
        from app import agent_service
        role=db.scalar(select(Membership.role).where(Membership.workspace_id==conversation.workspace_id,Membership.user_id==user_id))
        return conversation.user_id==user_id or conversation.live_assigned_user_id==user_id or agent_service.can_manage(db,conversation.workspace_id,role)
    if conversation.live_status in {'active','closed'}:return conversation.live_assigned_user_id==user_id
    if conversation.live_status=='pending':return str(user_id) in {p['id'] for p in eligible(db,conversation.workspace_id,conversation.live_context['routing'])}
    return False


def chat(db,workspace_id,identifier,user_id,lock=False):
    query=select(AgentConversation).where(AgentConversation.id==identifier,AgentConversation.workspace_id==workspace_id)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    conversation=db.scalar(query)
    if not conversation or not can_view(db,conversation,user_id):raise HTTPException(404,'Live chat not found.')
    if lock:require_manage(db,conversation,user_id)
    return conversation


def require_manage(db,conversation,user_id):
    from app.workspace_settings import permissions as grants
    if not grants.has_custom(db,conversation.workspace_id,user_id):return
    agent=db.get(Agent,conversation.agent_id) if conversation.agent_id else None
    teams=(conversation.live_context or {}).get('routing',{}).get('team_ids',[])
    if not any(grants.record_allowed(db,conversation.workspace_id,user_id,'livechats','manage',user_id if conversation.live_status=='pending' else conversation.live_assigned_user_id,t,agent.teamspace_id if agent else None) for t in (teams or [None])):raise HTTPException(403,'Your role cannot manage this livechat.')


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
        query=query.where(AgentMessage.sequence<marker.sequence)
    rows=db.scalars(query.order_by(AgentMessage.sequence.desc()).limit(101)).all()
    return {'messages':[{'id':str(row.id),'role':row.role,'content':row.content,'created_at':row.created_at.isoformat(),**row.message_data} for row in reversed(rows[:100])],
        'has_more':len(rows)>100}


def summary(conversation):
    external=conversation.workflow_state.get('external_handoff')
    if external and external.get('status') in {'sending','transferred','uncertain'}:
        return {'id':str(conversation.id),'agent_id':str(conversation.agent_id) if conversation.agent_id else None,'agent_name':conversation.agent_name,'language':conversation.workflow_state.get('language','en'),
            'created_at':conversation.created_at.isoformat(),'updated_at':external['updated_at'],'status':'external','revision':external['revision'],'assigned_user_id':None,'external_handoff':external}
    return {'id':str(conversation.id),'agent_id':str(conversation.agent_id) if conversation.agent_id else None,'agent_name':conversation.agent_name,
        'created_at':conversation.created_at.isoformat(),'language':conversation.workflow_state.get('language','en'),'priority':bool(conversation.live_context.get('priority')),**public_status(conversation)}


def claim(db,conversation,session,expected):
    settings=assignment(db,conversation.workspace_id,lock=True)
    if conversation.live_revision!=expected or conversation.live_status!='pending':raise HTTPException(409,'This chat was already claimed or changed. Reload the queue.')
    person=next((p for p in eligible(db,conversation.workspace_id,conversation.live_context['routing'],space_id=__import__('app.teamspace.records',fromlist=['record_space']).record_space(db,'chats',conversation)) if p['id']==str(session.user_id)),None)
    if not person or not person['available']:raise HTTPException(409,'Become available in Inbox before claiming this chat.')
    from app.teamspace.assignment import policy
    from app.teamspace.records import record_space,check_ban
    check_ban(db,conversation)
    current=policy(db,conversation.workspace_id,record_space(db,'chats',conversation)).assignment
    voluntary=current.get('algorithm',1)>=2 and current.get('mode')=='manual'
    ignore=conversation.live_context.get('priority') and conversation.live_context.get('priority_type',current.get('priority_type'))=='ignore_limit'
    if not voluntary and not ignore and person['active_chats']>=current.get('limit',settings.max_active_chats):raise HTTPException(409,'You have reached the active chat limit.')
    conversation.live_status='active';conversation.live_assigned_user_id=session.user_id;conversation.live_revision+=1;conversation.live_updated_at=utcnow()
    ensure_profile(db,conversation.workspace_id,session.user_id).last_assigned_at=utcnow()
    audit(db,conversation,session.user_id,'claimed');db.commit()


def require_assignee(conversation,user_id,expected):
    if conversation.live_status!='active' or conversation.live_assigned_user_id!=user_id or conversation.live_revision!=expected:
        raise HTTPException(409,'This live chat changed or is no longer assigned to you. Reload before continuing.')


def reply(db,conversation,session,body,settings=None):
    duplicate=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==conversation.id,
        AgentMessage.message_data['client_message_id'].as_string()==str(body.client_message_id)))
    identifiers=list(map(str,body.attachment_ids))
    if duplicate:
        if duplicate.author_id!=session.user_id or duplicate.content!=body.content or duplicate.message_data.get('attachment_ids',[])!=identifiers or duplicate.message_data.get('takeover',False)!=body.takeover or ('alias_id' in body.model_fields_set and duplicate.message_data.get('alias_id')!=(str(body.alias_id) if body.alias_id else None)):raise HTTPException(409,'This reply request ID was already used with different contents.')
        return
    if conversation.workflow_state.get('channel')=='whatsapp':
        from app.whatsapp.delivery import context as whatsapp_context
        whatsapp_context(db,conversation)
    from app.teamspace.records import check_ban,record_space
    from app.inbox.workspace import ensure_budget,attachments,attachment_payload
    check_ban(db,conversation)
    if settings:ensure_budget(db,conversation.workspace_id,settings,'people_messages')
    if conversation.live_status not in {'pending','active'} or conversation.workflow_state.get('external_handoff',{}).get('status') in {'sending','transferred','uncertain'}:raise HTTPException(409,'This conversation is not available for human replies.')
    if conversation.live_revision!=body.expected_revision:raise HTTPException(409,'This livechat changed. Reload before sending.')
    mine=conversation.live_status=='active' and conversation.live_assigned_user_id==session.user_id
    if not mine:
        if not body.takeover:raise HTTPException(409,'Take over this livechat before sending a reply.')
        from app.workspace_settings.permissions import require
        from app.teamspace.configuration import require_plan
        if settings:require_plan(db,conversation.workspace_id,settings,1)
        require(db,conversation.workspace_id,'transfer_livechats',session.user_id,teamspace_id=record_space(db,'chats',conversation))
        assignment(db,conversation.workspace_id,lock=True)
        routing={**conversation.live_context.get('routing',{}),'assignment_mode':'specific','user_ids':[str(session.user_id)],'team_ids':[]}
        people=eligible(db,conversation.workspace_id,routing,space_id=record_space(db,'chats',conversation))
        person=next((a for a in people if a['id']==str(session.user_id) and a['available']),None)
        if not person:raise HTTPException(409,'Become available and join an eligible support team before taking over.')
        from app.teamspace.assignment import policy
        current=policy(db,conversation.workspace_id,record_space(db,'chats',conversation)).assignment
        if not (conversation.live_context.get('priority') and conversation.live_context.get('priority_type')=='ignore_limit') and person['active_chats']>=current.get('limit',10):raise HTTPException(409,'You have reached the active chat limit.')
        conversation.live_assigned_user_id=session.user_id;conversation.live_status='active';conversation.live_display_status='';conversation.live_revision+=1
        conversation.live_context={**conversation.live_context,'assigned_id':str(session.user_id),'voice_mode':False}
        ensure_profile(db,conversation.workspace_id,session.user_id).last_assigned_at=utcnow();audit(db,conversation,session.user_id,'taken_over')
    from app.profile import service as personas
    identity=personas.snapshot(db,conversation.workspace_id,'chats',conversation.id,session.user_id,body.alias_id,'alias_id' in body.model_fields_set)
    assets=attachments(db,conversation,'chats',session.user_id,body.attachment_ids)
    from app.models import AgentAttachment
    from app.workflows.attachments import metadata
    delivered_files=[]
    for asset in assets:
        file=AgentAttachment(id=asset.id,conversation_id=conversation.id,file_name=asset.file_name,content_type=asset.content_type,size_bytes=asset.size_bytes,data=asset.data)
        db.add(file);db.delete(asset);delivered_files.append(metadata(file,conversation.workspace_id,conversation.agent_id))
    personas.remember(db,conversation.workspace_id,'chats',conversation.id,session.user_id,uuid.UUID(identity['alias_id']) if identity['alias_id'] else None)
    db.add(AgentMessage(conversation_id=conversation.id,author_id=session.user_id,role='assistant',content=body.content,
        message_data={'source':'live_agent','author_name':identity['name'],'alias_id':identity['alias_id'],'avatar_url':identity['avatar_url'],'client_message_id':str(body.client_message_id),'takeover':body.takeover,'attachment_ids':identifiers,'attachments':delivered_files,'quick_replies':[]}))
    conversation.live_context={**conversation.live_context,'voice_mode':False}
    conversation.live_updated_at=utcnow()
    if conversation.workflow_state.get('channel')=='whatsapp':
        from app.whatsapp.delivery import enqueue
        enqueue(db,conversation,settings,commit=False)
    db.commit()


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
