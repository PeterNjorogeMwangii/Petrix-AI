"""Merge overlapping session leases into one measured agent timeline."""
from collections import defaultdict
from datetime import timedelta
from sqlalchemy import select
from app.core.security import utcnow
from app.models import AnalyticsActivity,AuthSession,Membership,User,SupportAgent,SupportTicket,WorkspaceSettings
from app.analytics.metrics import metric

PRIORITY={'online':3,'away':2,'offline':1,'logged_out':0,'unknown':-1,'upcoming':-2}


def intervals(db,workspace_id,start,end,user_ids,scope=None):
    result=defaultdict(list)
    query=select(AnalyticsActivity,AuthSession).join(AuthSession,AuthSession.id==AnalyticsActivity.session_id).where(AnalyticsActivity.workspace_id==workspace_id,AnalyticsActivity.user_id.in_(user_ids),AnalyticsActivity.started_at<end,AnalyticsActivity.lease_until>start)
    for row,session in db.execute(query):
        until=min(row.ended_at or row.lease_until,row.lease_until,session.expires_at,session.revoked_at or end,end)
        since=max(start,row.started_at)
        capacity=max((v for key,v in row.capacities.items() if scope is None or key in {str(i) for i in scope}),default=0) if row.capacities else row.capacity
        if until>since:result[row.user_id].append((since,until,row.state,capacity,row.last_seen_at))
    return result


def merge(values,start,end,coverage,now=None):
    now=now or utcnow();points={start,end,min(end,max(start,now)),min(end,max(start,coverage))}
    for a,b,_,_,_ in values:points.update([max(start,a),min(end,b)])
    points=sorted(points);segments=[];durations={k:0.0 for k in PRIORITY}
    for a,b in zip(points,points[1:]):
        if b<=a:continue
        covering=[v for v in values if v[0]<b and v[1]>a]
        chosen=max(covering,key=lambda v:(PRIORITY[v[2]],v[3])) if covering else None
        state='upcoming' if a>=now else chosen[2] if chosen else 'unknown' if a<coverage else 'logged_out'
        capacity=chosen[3] if chosen and state=='online' else 0
        durations[state]+=(b-a).total_seconds()
        if segments and segments[-1]['state']==state and segments[-1]['capacity']==capacity:segments[-1]['end']=b
        else:segments.append({'start':a,'end':b,'state':state,'capacity':capacity})
    return segments,durations


def users(dataset,start,end,team_ids,states,agent_id,offset,limit):
    db=dataset.db;scope={str(s) for s in dataset.scope};members=db.execute(select(User,Membership).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==dataset.workspace_id).order_by(User.created_at,User.id)).all()
    members=[(u,m) for u,m in members if m.teamspace_ids is None or set(m.teamspace_ids)&scope]
    teams=list(dataset.teams.values());policy=db.get(WorkspaceSettings,dataset.workspace_id);coverage=policy.analytics_started_at if policy else utcnow()
    raw=intervals(db,dataset.workspace_id,start,end,[u.id for u,_ in members],dataset.scope);now=utcnow();current=intervals(db,dataset.workspace_id,now-timedelta(seconds=90),now+timedelta(microseconds=1),[u.id for u,_ in members],dataset.scope)
    chats=dataset.rows('chats');tickets=dataset.rows('tickets');events={kind:dataset.events(kind,[r.id for r in rows]) for kind,rows in [('chats',chats),('tickets',tickets)]}
    cutoff=min(now,end);work=defaultdict(lambda:{'chats':0,'tickets':0});current_day=start<=now<end
    for kind,rows in [('chats',chats),('tickets',tickets)]:
        for row in rows:
            if row.created_at>=cutoff:continue
            known=[e for e in events[kind][row.id] if e.created_at<=cutoff]
            assignments=[e for e in known if e.event=='assignment'];statuses=[e for e in known if e.event=='status']
            if assignments:
                assigned=assignments[-1].assigned_user_id
                status=statuses[-1].status if statuses else assignments[-1].status
            elif current_day:
                assigned=row.live_assigned_user_id if kind=='chats' else row.assignee_id if row.assignee_type=='user' else None
                status=__import__('app.analytics.tracking',fromlist=['chat_status']).chat_status(row) if kind=='chats' else row.status
            else:continue
            if assigned and status in {'open','pending','in_progress','overdue'}:work[assigned][kind]+=1
    result=[];all_segments={}
    for user,_ in members:
        own_teams=[{'id':str(t.id),'name':t.name} for t in teams if str(user.id) in (t.member_ids or []) and scope&set(t.teamspace_ids if t.teamspace_ids is not None else [str(dataset.default)])]
        if team_ids and not set(team_ids)&{t['id'] for t in own_teams} or agent_id and str(user.id)!=str(agent_id):continue
        segments,durations=merge(raw[user.id],start,end,coverage,now)
        active=[v for v in current[user.id] if v[0]<=now<v[1]]
        state=max(active,key=lambda v:PRIORITY[v[2]])[2] if active else 'logged_out'
        if states and state not in states:continue
        last=db.scalar(select(AnalyticsActivity.last_seen_at).where(AnalyticsActivity.workspace_id==dataset.workspace_id,AnalyticsActivity.user_id==user.id,AnalyticsActivity.state=='online',AnalyticsActivity.started_at<end).order_by(AnalyticsActivity.last_seen_at.desc()).limit(1))
        result.append({'id':str(user.id),'name':user.full_name or user.email.split('@')[0],'teams':own_teams,'status':state,'last_online':min(last,cutoff) if last else None,
            'assigned_chats':work[user.id]['chats'],'assigned_tickets':work[user.id]['tickets'],'active_seconds':round(durations['online']+durations['away'],3),
            'durations':{k:round(v,3) for k,v in durations.items()},'segments':segments})
        all_segments[user.id]=segments
    return {'items':result[offset:offset+limit],'total':len(result),'offset':offset,'limit':limit,'tracking_started_at':coverage},all_segments


def capacity(dataset,rows,start,end,tz,segments):
    now=utcnow();events=dataset.events('chats',[r.id for r in rows]);samples=[]
    for hour in range(24):
        local=start.astimezone(tz).replace(hour=hour,minute=0,second=0,microsecond=0)
        stamp=local.astimezone(start.tzinfo)
        if stamp>now:break
        point=min(now,stamp+timedelta(hours=1)-timedelta(microseconds=1))
        potential=sum(next((s['capacity'] for s in values if s['start']<=point<s['end'] and s['state']=='online'),0) for values in segments.values())
        actual=0
        for row in rows:
            known=[e for e in events[row.id] if e.event=='assignment' and e.created_at<=point]
            if known and known[-1].assigned_user_id and known[-1].status in {'open','pending','overdue'}:actual+=1
            elif not known and row.created_at<=point and point>=now-timedelta(seconds=1) and row.live_status=='active' and row.live_assigned_user_id:actual+=1
        samples.extend([{'x':'{:02d}:00'.format(hour),'series':'Potential capacity','y':potential},{'x':'{:02d}:00'.format(hour),'series':'Actual capacity','y':actual}])
    return samples
