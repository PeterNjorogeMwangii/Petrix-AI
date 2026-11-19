"""Report definitions; absent timing evidence stays null, not a fabricated zero."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from statistics import mean,median
from app.core.security import utcnow


def average(values,method=mean):return round(method(values),3) if values else None

def parsed(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        return result if result and result.tzinfo else None
    except (ValueError,TypeError):return None

def metric(key,label,value,unit='number',description=''):
    return {'key':key,'label':label,'value':value,'unit':unit,'description':description}


def aggregate(dataset,items,kind,group,segment,tz,at=None,value=None,fill=None):
    counts=defaultdict(float)
    for row in items:
        stamp=at(row) if at else row.created_at
        if not stamp:continue
        x=dataset.dimension(row,kind,group,stamp,tz)
        series=dataset.dimension(row,kind,segment,stamp,tz) if segment!='none' else 'Total'
        counts[(x,series)]+=value(row) if value else 1
    if fill:
        series={s for _,s in counts} or {'Total'}
        for x in fill:
            for name in series:counts.setdefault((x,name),0)
    return [{'x':x,'series':series,'y':round(y,3)} for (x,series),y in sorted(counts.items())]


def buckets(start,end,group):
    if group=='hour':return ['{:02d}:00'.format(i) for i in range(24)]
    if group not in {'day','week','month'}:return None
    values=[];current=start
    while current<=end:
        value=current.isoformat() if group=='day' else (current-timedelta(days=current.weekday())).isoformat() if group=='week' else current.strftime('%Y-%m')
        if value not in values:values.append(value)
        current+=timedelta(days=1)
    return values


def timings(dataset,rows,since=None,until=None):
    inside=lambda at:not since or since<=at<until
    messages=dataset.messages('chats',[r.id for r in rows]);events=dataset.events('chats',[r.id for r in rows])
    result={key:[] for key in ['ai_first','ai_response','live_first','live_response','duration','ai_duration','live_duration','messages','ai_messages','live_messages']}
    handoffs=[]
    for row in rows:
        history=messages[row.id];live=dataset.live(row);result['messages'].append(len(history));result['live_messages' if live else 'ai_messages'].append(len(history))
        ai=[m for m in history if m.role=='assistant' and m.source!='live_agent']
        if ai and inside(ai[0].at):result['ai_first'].append(max(0,(ai[0].at-row.created_at).total_seconds()))
        waiting=None
        for m in history:
            if m.role=='user':waiting=waiting or m.at
            elif m.role=='assistant' and waiting:
                
                if inside(m.at):result['live_response' if m.source=='live_agent' else 'ai_response'].append(max(0,(m.at-waiting).total_seconds()))
                waiting=None
        assignments=[e for e in events[row.id] if e.event=='assignment']
        # Legacy handoffs can use their recorded initial handoff time, but no synthetic transfer times.
        if not assignments and (row.live_context or {}).get('handoff_at'):
            from types import SimpleNamespace
            at=parsed(row.live_context['handoff_at'])
            if at:assignments=[SimpleNamespace(created_at=at,assigned_user_id=row.live_assigned_user_id,status='open',team_ids=(row.live_context or {}).get('routing',{}).get('team_ids',[]))]
        for index,event in enumerate(assignments):
            if not inside(event.created_at) or event.status not in {'open','pending','overdue'} or not (event.assigned_user_id or event.team_ids):continue
            until=assignments[index+1].created_at if index+1<len(assignments) else utcnow()+timedelta(seconds=1)
            reply=next((m for m in history if m.source=='live_agent' and event.created_at<=m.at<until and (not event.assigned_user_id or m.author_id==event.assigned_user_id)),None)
            if reply:
                seconds=max(0,(reply.at-event.created_at).total_seconds());result['live_first'].append(seconds);handoffs.append((event.created_at,seconds))
        closed=parsed((row.workflow_state or {}).get('closed_at'))
        if not closed:
            closed=next((e.created_at for e in reversed(events[row.id]) if e.event=='status' and e.status in {'closed','resolved'}),None)
        if closed and inside(closed):
            duration=max(0,(closed-row.created_at).total_seconds());result['duration'].append(duration);result['live_duration' if live else 'ai_duration'].append(duration)
    return result,handoffs


def satisfaction(rows,dataset):
    ratings=[dataset.rating(r) for r in rows if dataset.rating(r) is not None]
    return {'rated':len(ratings),'unrated':len(rows)-len(ratings),'average':average(ratings),
        'csat_pct':round(100*sum(r>=4 for r in ratings)/len(ratings),2) if ratings else None,
        'positive_pct':round(100*sum(r==5 for r in ratings)/len(ratings),2) if ratings else None}


def abandonment(rows,dataset):
    closed=[r for r in rows if (r.workflow_state or {}).get('closed') or r.live_status=='closed']
    messages=dataset.messages('chats',[r.id for r in closed])
    return [metric('closed_without_feedback','Closed without feedback',sum(not (r.workflow_state or {}).get('feedback_submitted') for r in closed)),
        metric('closed_without_replies','Closed without person replies',sum(not any(m.role=='user' for m in messages[r.id]) for r in closed))]


def response_metrics(result):
    return [metric('ai_first','Average AI first response time',average(result['ai_first'],median),'seconds','Median from chat creation to the first recorded AI reply.'),
        metric('ai_response','Average AI response time',average(result['ai_response'],median),'seconds','Median wait from the start of a person-message burst to the next AI reply.'),
        metric('live_first','Average live agent first response time',average(result['live_first']),'seconds','Mean from each recorded assignment to that assignee’s first reply; reassignments start a new interval.'),
        metric('live_response','Average live agent response time',average(result['live_response']),'seconds','Mean wait from a person-message burst to the next live-agent reply.')]


def ticket_metrics(dataset,rows):
    messages=dataset.messages('tickets',[r.id for r in rows]);events=dataset.events('tickets',[r.id for r in rows]);first=[];resolution=[]
    for row in rows:
        reply=next((m for m in messages[row.id] if m.source=='live_agent'),None)
        if reply:first.append(max(0,(reply.at-row.created_at).total_seconds()))
        resolved=next((e for e in events[row.id] if e.event=='status' and e.status=='resolved'),None)
        if resolved:resolution.append(max(0,(resolved.created_at-row.created_at).total_seconds()))
    return [metric('new_tickets','Total new tickets',len(rows)),
        metric('emails_per_ticket','Average number of emails per ticket',average([len(messages[r.id]) for r in rows]),description='Customer email events only: delivered outgoing messages and received emails; CC does not multiply the count.'),
        metric('first_reply','Average first response time',average(first),'seconds','Mean creation-to-first-delivered human reply. Initial automated notifications are excluded.'),
        metric('resolution','Average ticket resolution time',average(resolution),'seconds','Mean creation-to-first-recorded Resolved transition; unavailable legacy resolution times are excluded.')]
