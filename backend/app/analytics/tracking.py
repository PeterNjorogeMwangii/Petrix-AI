"""Transactional assignment/status evidence and server-timed presence leases."""
from datetime import timedelta
from sqlalchemy import event,inspect,select
from sqlalchemy.orm import Session
from app.core.security import utcnow
from app.models import (AgentConversation,SupportTicket,AnalyticsRecordEvent,AnalyticsActivity,
    AuthSession,UserProfile,SupportAgent)


def chat_status(row):
    if ((row.workflow_state or {}).get('external_handoff') or {}).get('status') in {'sending','transferred','uncertain'}:return 'external'
    if row.live_status=='closed':return 'resolved' if row.live_display_status=='resolved' else 'closed'
    if row.live_display_status in {'pending','overdue'}:return row.live_display_status
    return {'pending':'pending','active':'open'}.get(row.live_status,'closed' if (row.workflow_state or {}).get('closed') else 'ai')


def capture(db,flush_context,instances):
    pending=[]
    for row in list(db.new)+list(db.dirty):
        if not isinstance(row,(AgentConversation,SupportTicket)) or row in db.deleted:continue
        new=row in db.new;state=inspect(row);chat=isinstance(row,AgentConversation)
        changed=lambda name:state.attrs[name].history.has_changes()
        if chat:
            status=chat_status(row);context=row.live_context or {};teams=context.get('routing',{}).get('team_ids',[])
            assigned=row.live_assigned_user_id
            old=state.attrs.live_context.history.deleted
            old_teams=(old[0] or {}).get('routing',{}).get('team_ids',[]) if old else teams
            assignment=(new and row.live_status in {'pending','active'}) or (not new and (changed('live_assigned_user_id') or changed('live_status') or old_teams!=teams))
            status_changed=new or changed('live_status') or changed('live_display_status')
        else:
            status=row.status or 'open';assigned=row.assignee_id if row.assignee_type=='user' else None
            teams=list(row.assigned_team_ids or [])+([str(row.assignee_id)] if row.assignee_type=='team' and row.assignee_id else [])
            assignment=new or any(changed(k) for k in ['assignee_id','assignee_type','assigned_team_ids'])
            status_changed=new or changed('status')
        value={'workspace_id':row.workspace_id,'assigned_user_id':assigned,'team_ids':list(dict.fromkeys(teams)),
            'status':status,'created_at':utcnow(),'detail':{}}
        if assignment:pending.append((row,chat,{**value,'event':'assignment'}))
        if status_changed:pending.append((row,chat,{**value,'event':'status'}))
        if chat and changed('workflow_state'):
            old=state.attrs.workflow_state.history.deleted
            previous=old[0] if old else {}
            current=row.workflow_state or {};rating=current.get('csat_rating')
            if current.get('feedback_submitted') and (not previous.get('feedback_submitted') or previous.get('csat_rating')!=rating):
                pending.append((row,True,{**value,'event':'feedback','detail':{'rating':rating}}))
    db.info['_analytics_events']=pending


def store(db,flush_context):
    for row,chat,values in db.info.pop('_analytics_events',[]):
        db.add(AnalyticsRecordEvent(**values,**{'conversation_id' if chat else 'ticket_id':row.id}))


def install():
    if not event.contains(Session,'before_flush',capture):
        event.listen(Session,'before_flush',capture);event.listen(Session,'after_flush',store)


def close(db,session_id,now=None):
    now=now or utcnow()
    for row in db.scalars(select(AnalyticsActivity).where(AnalyticsActivity.session_id==session_id,AnalyticsActivity.ended_at.is_(None)).with_for_update()):
        row.ended_at=min(now,row.lease_until)


def capacities_for(db,workspace_id,user_id):
    """Read current limits without initializing or changing assignment policy."""
    from app.models import TeamspaceOperations,ChatAssignment
    from app.workspace_settings import permissions as p
    teams=p.teams_for(db,workspace_id,user_id);default=p.default_space(db,workspace_id)
    result={}
    for value in p.accessible_spaces(db,workspace_id,user_id):
        applicable=[t for t in teams if value in (t.teamspace_ids if t.teamspace_ids is not None else [str(default)])]
        if not any(all(p.record_allowed(db,workspace_id,user_id,'livechats',action,user_id,t.id,value) for action in ['view','manage']) for t in applicable):continue
        import uuid
        ops=db.get(TeamspaceOperations,uuid.UUID(value));legacy=db.get(ChatAssignment,workspace_id)
        result[value]=ops.assignment.get('limit',10) if ops else legacy.max_active_chats if legacy and value==str(default) else 10
    return result


def touch(db,workspace_id,session):
    now=utcnow()
    from app.workspace_settings.routes import policy
    policy(db,workspace_id)
    locked=db.scalar(select(AuthSession).where(AuthSession.id==session.id).with_for_update().execution_options(populate_existing=True))
    if not locked or locked.revoked_at or locked.expires_at<=now:
        from fastapi import HTTPException
        raise HTTPException(401,'Your session has expired. Please sign in again.')
    account=db.get(UserProfile,session.user_id);state=account.presence if account else 'online'
    support=db.get(SupportAgent,(workspace_id,session.user_id));capacity=0;capacities={}
    if support and support.available and state=='online':
        support.last_seen_at=now;support.presence_session_id=session.id
        if not support.online_since:support.online_since=now
        capacities=capacities_for(db,workspace_id,session.user_id);capacity=max(capacities.values(),default=0)
    prior=db.scalar(select(AnalyticsActivity).where(AnalyticsActivity.workspace_id==workspace_id,
        AnalyticsActivity.session_id==session.id,AnalyticsActivity.ended_at.is_(None)).order_by(AnalyticsActivity.started_at.desc()).limit(1))
    if prior and (prior.lease_until<now or prior.state!=state or prior.capacity!=capacity or prior.capacities!=capacities):
        prior.ended_at=min(now,prior.lease_until);prior=None
    if not prior:
        prior=AnalyticsActivity(workspace_id=workspace_id,user_id=session.user_id,session_id=session.id,state=state,
            capacity=capacity,capacities=capacities,started_at=now,last_seen_at=now,lease_until=min(session.expires_at,now+timedelta(seconds=90)))
        db.add(prior)
    else:prior.last_seen_at=now;prior.lease_until=min(session.expires_at,now+timedelta(seconds=90))
    return prior
