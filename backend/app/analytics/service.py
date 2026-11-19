"""Six operational reports with one shared, server-validated filter context."""
from collections import Counter,defaultdict
from datetime import timedelta
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import SupportPerson,WorkspaceSettings
from app.workspace_settings import permissions as p
from app.teamspace import records as r
from app.analytics import data as d,metrics as m,activity


def chart(key,title,points,kind='bar',unit='count',description=''):
    return {'key':key,'title':title,'data':points,'kind':kind,'unit':unit,'description':description}


def report(db,workspace_id,user_id,name,filters,start,end,group,segment,space_id,offset,limit,team_ids,states,agent_id,view,columns,rows_by,conversation_type):
    scope=d.spaces(db,workspace_id,user_id,name,space_id)
    first,last,since,until,tz=d.bounds(db,workspace_id,start,end,name=='live')
    ds=d.Dataset(db,workspace_id,user_id,scope,filters)
    result={'report':name,'title':d.TITLES[name],'timezone':str(tz),'start':first,'end':last,'generated_at':utcnow(),
        'range_start':since,'range_end':until,'summary':[],'sections':[],'charts':[],'notes':[],'table':None,'activity':None,'heatmap':None}
    policy=db.get(WorkspaceSettings,workspace_id)
    if policy and since<policy.analytics_started_at:result['notes'].append('Agent activity and assignment/resolution history are measured from '+policy.analytics_started_at.astimezone(tz).strftime('%d %b %Y, %H:%M')+'. Earlier unrecorded durations are shown as unavailable.')
    def cohort(kind):return [row for row in ds.rows(kind) if since<=row.created_at<until]
    def breakdown(kind,items,title):
        return chart('breakdown',title,m.aggregate(ds,items,kind,group,segment,tz,fill=m.buckets(first,last,group)), 'line' if group in {'hour','day','week','month'} else 'bar')
    def status_chart(kind,items):
        return chart('statuses','Chats by status' if kind=='chats' else 'Total tickets by status',m.aggregate(ds,items,kind,'status','none',tz),'pie')
    if name=='messaging':
        points=Counter();total=0;categories=Counter();seen_messages=set()
        for kind in ['chats','tickets']:
            items=ds.rows(kind);messages=ds.messages(kind,[v.id for v in items])
            for row in items:
                for event in messages[row.id]:
                    if not since<=event.at<until or (kind,event.identity) in seen_messages:continue
                    seen_messages.add((kind,event.identity))
                    category=('Email received' if event.role=='user' else 'Email sent') if kind=='tickets' else 'Person messages' if event.role=='user' else 'Live agent messages' if event.source=='live_agent' else 'AI messages'
                    x=ds.dimension(row,kind,group,event.at,tz);series=category if segment=='message_type' else ds.dimension(row,kind,segment,event.at,tz) if segment!='none' else 'Total'
                    points[(x,series)]+=1;categories[category]+=1;total+=1
        for x in m.buckets(first,last,group) or []:
            for series in {s for _,s in points} or {'Total'}:points.setdefault((x,series),0)
        result['summary']=[m.metric('messages','Total messaging interactions',total,description='Individual customer-facing messages, not weighted billing interactions.'),m.metric('daily_average','Average messages per day',round(total/((last-first).days+1),2))]
        result['charts']=[chart('messaging','Messaging interactions',[{'x':x,'series':s,'y':v} for (x,s),v in sorted(points.items())],'line' if group in {'hour','day','week','month'} else 'bar'),chart('types','Messages by type',[{'x':x,'series':'Total','y':v} for x,v in sorted(categories.items())],'pie')]
    elif name=='chats':
        items=cohort('chats');live=[v for v in items if ds.live(v)];ai=[v for v in items if not ds.live(v)]
        result['summary']=[m.metric('total_chats','Total new chats',len(items)),m.metric('ai_chats','AI Chats',len(ai)),m.metric('livechats','Livechats',len(live)),m.metric('ai_share','AI Chats %',round(100*len(ai)/len(items),2) if items else None,'percent'),m.metric('live_share','Livechats %',round(100*len(live)/len(items),2) if items else None,'percent')]
        timing,_=m.timings(ds,items)
        result['sections']=[{'title':'Responsiveness','metrics':m.response_metrics(timing)},
            {'title':'Engagement','metrics':[m.metric(k,label,m.average(timing[key]),unit,description) for k,label,key,unit,description in [
                ('messages','Average messages per chat','messages','number','All customer-facing messages in each created-chat cohort.'),('ai_messages','Average messages per AI chat','ai_messages','number',''),('live_messages','Average messages per livechat','live_messages','number',''),
                ('duration','Average chat duration','duration','seconds','Completed conversations with a recorded completion time.'),('ai_duration','Average AI chat duration','ai_duration','seconds',''),('live_duration','Average livechat duration','live_duration','seconds','')]]},
            {'title':'Abandonment','metrics':m.abandonment(items,ds)}]
        result['charts']=[status_chart('chats',items),breakdown('chats',items,'Chats breakdown')]
    elif name=='tickets':
        items=cohort('tickets');result['summary']=m.ticket_metrics(ds,items);result['charts']=[status_chart('tickets',items),breakdown('tickets',items,'Tickets breakdown')]
    elif name=='csat':
        all_rows=ds.rows('chats');events=ds.events('chats',[row.id for row in all_rows]);submissions={};missing=set()
        for row in all_rows:
            at=m.parsed((row.workflow_state or {}).get('feedback_submitted_at'))
            if not at:at=next((e.created_at for e in reversed(events[row.id]) if e.event=='feedback'),None)
            submissions[row.id]=at
            if ds.rating(row) is not None and not at:missing.add(row.id)
        items=[row for row in all_rows if since<=(submissions[row.id] or row.created_at)<until]
        total=m.satisfaction(items,ds);result['summary']=[m.metric('rated','Rated chats',total['rated']),m.metric('average','Average CSAT',total['average'],'rating'),m.metric('csat_pct','CSAT %',total['csat_pct'],'percent','Ratings 4 or 5 divided by rated chats.'),m.metric('positive_pct','Positive CSAT %',total['positive_pct'],'percent','Rating 5 divided by rated chats.')]
        result['sections']=[{'title':label,'metrics':[m.metric('average','Average CSAT',stats['average'],'rating'),m.metric('csat_pct','CSAT %',stats['csat_pct'],'percent'),m.metric('positive_pct','Positive CSAT %',stats['positive_pct'],'percent')]} for label,part in [('Overall',items),('AI agents',[v for v in items if not ds.live(v)]),('Live agents',[v for v in items if ds.live(v)])] for stats in [m.satisfaction(part,ds)]]
        distribution=Counter(('Rated ' if ds.rating(v) is not None else 'Unrated ')+('livechats' if ds.live(v) else 'AI chats') for v in items)
        rated=[v for v in items if ds.rating(v) is not None]
        result['charts']=[chart('distribution','Rating distribution',[{'x':x,'series':'Total','y':v} for x,v in sorted(distribution.items())],'pie'),chart('breakdown','Rating breakdown',m.aggregate(ds,rated,'chats',group,segment,tz,at=lambda row:submissions[row.id] or row.created_at,fill=['1','2','3','4','5'] if group=='rating' else m.buckets(first,last,group)))]
        feedback=sorted([v for v in items if (v.workflow_state or {}).get('feedback_submitted') or ds.rating(v) is not None],key=lambda row:(submissions[row.id] or row.created_at,str(row.id)),reverse=True)
        table=[]
        for row in feedback[offset:offset+limit]:
            can_open=True
            try:r.record(db,workspace_id,'chats',row.id,user_id)
            except __import__('fastapi').HTTPException:can_open=False
            person=db.get(SupportPerson,row.person_id) if row.person_id else None
            visible=person and r.person_visible(db,workspace_id,person,user_id) and p.allowed(db,workspace_id,'view_people_name',user_id,teamspace_id=r.record_space(db,'people',person))
            table.append({'id':str(row.id),'person_name':person.name or 'Unknown person' if visible else 'Person','submitted_at':submissions[row.id],'rating':ds.rating(row),'chat_type':'Livechat' if ds.live(row) else 'AI chat','can_open':can_open})
        result['table']={'items':table,'total':len(feedback),'offset':offset,'limit':limit}
        if any(v.id in missing for v in items):result['notes'].append('Legacy ratings without a submission timestamp are included by chat creation date; their submission date is shown as not recorded.')
    elif name=='agents':
        table,segments=activity.users(ds,since,until,team_ids,states,agent_id,offset,limit)
        result['table']=table;result['activity']=table['items'];result['view']=view
        result['summary']=[m.metric('agents','Live agents',table['total']),m.metric('recorded_days','Reporting days',(last-first).days+1)]
        counts=Counter();labels={};seen=set()
        for kind in ['chats','tickets']:
            if conversation_type!='all' and conversation_type!=kind:continue
            items=ds.rows(kind);events=ds.events(kind,[row.id for row in items])
            for row in items:
                for e in events[row.id]:
                    if e.event!='assignment' or e.status not in {'open','pending','in_progress','overdue'} or not since<=e.created_at<until:continue
                    if agent_id and e.assigned_user_id!=agent_id:continue
                    if team_ids and not set(team_ids)&set(e.team_ids):continue
                    col=ds.dimension(row,kind,columns,e.created_at,tz)
                    targets=e.team_ids or ['unassigned'] if rows_by=='team' else [str(e.assigned_user_id)] if e.assigned_user_id else ['unassigned']
                    for target in targets:
                        identity=(kind,row.id,target,col)
                        if identity in seen:continue
                        seen.add(identity);counts[(target,col)]+=1
                        labels[target]=(ds.teams[target].name if target in ds.teams else 'Unassigned team') if rows_by=='team' else ds.users.get(target,'Unassigned')
        cols=m.buckets(first,last,columns) or sorted({c for _,c in counts});row_ids=sorted({key for key,_ in counts},key=lambda key:labels[key])
        result['heatmap']={'columns':cols,'rows':[{'id':key,'name':labels[key],'values':[counts[(key,c)] for c in cols]} for key in row_ids],'maximum':max(counts.values(),default=0),'description':'Distinct conversations assigned per agent/team and period; transfers can appear under more than one assignee.'}
    elif name=='live':
        has=lambda key:any(p.allowed(db,workspace_id,key,user_id,teamspace_id=s) for s in scope)
        allowed=lambda row,kind,key:p.allowed(db,workspace_id,key,user_id,teamspace_id=r.record_space(db,kind,row))
        if has('analytics_chats'):
            all_chats=[row for row in ds.rows('chats') if allowed(row,'chats','analytics_chats')];today=[v for v in all_chats if since<=v.created_at<until]
            pending=sum(v.live_status=='pending' and bool(v.live_assigned_user_id or ds.team_ids(v,'chats')) for v in all_chats)
            result['summary'] += [m.metric('unassigned_livechats','Unassigned livechats',pending,description='Pending human chats with an assigned agent or team; unrouted pending chats are excluded.'),m.metric('open_livechats','Open livechats',sum(d.chat_status(v) in {'open','overdue'} for v in all_chats)),m.metric('open_ai','Open AI chats',sum(d.chat_status(v)=='ai' for v in all_chats))]
            points=m.aggregate(ds,today,'chats','hour','chat_type',tz,fill=['{:02d}:00'.format(i) for i in range(24)])
            result['charts'].append(chart('chats','Chats',points,'line'))
            result['sections'].append({'title':'Chats','metrics':[m.metric('new_ai','New AI chats',sum(not ds.live(v) for v in today)),m.metric('new_live','New livechats',sum(ds.live(v) for v in today))]})
            timing,waits=m.timings(ds,all_chats,since,until);result['sections'].append({'title':'Responsiveness','metrics':[m.metric('handoff_wait','Average handoff wait time',m.average([v for _,v in waits]),'seconds'),*m.response_metrics(timing)]})
            by_hour=defaultdict(list)
            for at,seconds in waits:by_hour[at.astimezone(tz).strftime('%H:00')].append(seconds)
            result['charts'].append(chart('handoff','Handoff wait time',[{'x':x,'series':'Wait time (seconds)','y':m.average(v)} for x,v in sorted(by_hour.items())],'line','seconds'))
            chat_events=ds.events('chats',[v.id for v in all_chats])
            feedback_at=lambda v:m.parsed(v.workflow_state.get('feedback_submitted_at')) or next((e.created_at for e in reversed(chat_events[v.id]) if e.event=='feedback'),v.created_at)
            rated_today=[v for v in all_chats if since<=feedback_at(v)<until]
            ai=m.satisfaction([v for v in rated_today if not ds.live(v)],ds);live=m.satisfaction([v for v in rated_today if ds.live(v)],ds)
            closed_today=[v for v in all_chats if since<=(m.parsed(v.workflow_state.get('closed_at')) or next((e.created_at for e in reversed(chat_events[v.id]) if e.event=='status' and e.status in {'closed','resolved'}),v.created_at))<until]
            result['sections'].append({'title':'Customer satisfaction & abandonment','metrics':[m.metric('ai_csat','Average AI CSAT',ai['average'],'rating'),m.metric('live_csat','Average live agent CSAT',live['average'],'rating'),*m.abandonment(closed_today,ds)]})
        if has('analytics_tickets'):
            all_tickets=[row for row in ds.rows('tickets') if allowed(row,'tickets','analytics_tickets')];today=[v for v in all_tickets if since<=v.created_at<until]
            result['summary'] += [m.metric('unassigned_tickets','Unassigned tickets',sum(v.status=='in_progress' for v in all_tickets),description='Pending/in-progress tickets regardless of assignee.'),m.metric('open_tickets','Open tickets',sum(v.status=='open' for v in all_tickets))]
            result['sections'].append({'title':'Tickets','metrics':[m.metric('new_tickets','New tickets',len(today))]})
            result['charts'].append(chart('tickets','Tickets',m.aggregate(ds,today,'tickets','hour','none',tz,fill=['{:02d}:00'.format(i) for i in range(24)]),'line'))
        person_scope=[s for s in scope if p.allowed(db,workspace_id,'view_people_all',user_id,teamspace_id=s) and any(p.allowed(db,workspace_id,key,user_id,teamspace_id=s) for key in ['analytics_chats','analytics_tickets'])]
        if person_scope:
            people=list(db.scalars(select(SupportPerson).where(SupportPerson.workspace_id==workspace_id,func.coalesce(SupportPerson.teamspace_id,ds.default).in_(person_scope))))
            active=set()
            for kind,grant in [('chats','analytics_chats'),('tickets','analytics_tickets')]:
                if not has(grant):continue
                items=[v for v in ds.rows(kind) if allowed(v,kind,grant)];history=ds.messages(kind,[v.id for v in items])
                for row in items:
                    if row.person_id and any(since<=e.at<until for e in history[row.id]):active.add(row.person_id)
            visible={v.id for v in people}
            result['sections'].append({'title':'People','metrics':[m.metric('new_people','New people',sum(since<=v.created_at<until for v in people)),m.metric('active_people','Active people',len(active&visible))]})
        if has('analytics_users_teams'):
            user_scope={s for s in scope if p.allowed(db,workspace_id,'analytics_users_teams',user_id,teamspace_id=s)}
            users_ds=d.Dataset(db,workspace_id,user_id,user_scope,filters);table,segments=activity.users(users_ds,since,until,team_ids,states,agent_id,offset,limit);result['table']=table
            if team_ids or states or agent_id:
                _,segments=activity.users(users_ds,since,until,[],[],None,0,100)
            from app.inbox.service import roster
            from app.analytics.tracking import capacities_for
            import uuid
            eligible_ids=set(segments)
            potential=0
            for person in roster(db,workspace_id):
                identifier=uuid.UUID(person['id'])
                if person['available'] and identifier in eligible_ids:
                    limits=capacities_for(db,workspace_id,identifier)
                    potential+=max((value for key,value in limits.items() if key in {str(s) for s in user_scope}),default=0)
            assigned=sum(v.live_status=='active' and v.live_assigned_user_id is not None for v in users_ds.rows('chats'))
            result['sections'].append({'title':'Live agent capacity','metrics':[m.metric('potential','Total potential capacity',potential),m.metric('actual','Total actual capacity',assigned)]})
            result['charts'].append(chart('capacity','Live agent capacity',activity.capacity(users_ds,users_ds.rows('chats'),since,until,tz,segments),'line','slots'))
    if name=='live':result['charts'].sort(key=lambda c:['chats','tickets','handoff','capacity'].index(c['key']))
    return result
