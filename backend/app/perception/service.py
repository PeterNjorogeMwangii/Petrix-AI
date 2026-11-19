"""Perception configuration. Every edit is serialized and revision checked."""
import uuid
from collections import Counter
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,func,delete,case
from app.core.security import utcnow
from app.models import (Workspace,WorkspaceSettings,Teamspace,SupportTeam,TicketTag,ConversationField,User,
    PerceptionSettings as Config,PerceptionKeyword as Keyword,PerceptionSource as Source,
    PerceptionSourceKeyword as Link,PerceptionRun as Run,PerceptionEntry as Entry)
from app.workspace_settings import permissions as p
from app.perception.schemas import key,PLATFORMS,LANGUAGES


def scope(db,workspace,space,user,manage=False):
    row=db.scalar(select(Teamspace).where(Teamspace.workspace_id==workspace,Teamspace.id==space))
    if not row or str(space) not in p.accessible_spaces(db,workspace,user):raise HTTPException(404,'Teamspace not found.')
    p.require(db,workspace,'view_perception',user,teamspace_id=space)
    if manage:p.require(db,workspace,'manage_perception',user,teamspace_id=space)
    return row

def lock(db,workspace):db.scalar(select(Workspace).where(Workspace.id==workspace).with_for_update())
def check(row,revision):
    if revision!=row.revision:raise HTTPException(409,'This record changed. Reload before saving.')

def config(db,workspace,space,create=False):
    row=db.get(Config,space)
    if row:return row
    row=Config(teamspace_id=space,workspace_id=workspace,revision=1,use_custom=True,use_teams=False,translate=False,target_language='en',excluded_languages=[])
    if create:db.add(row);db.flush()
    return row

def config_payload(row):return {k:getattr(row,k) for k in ['revision','use_custom','use_teams','translate','target_language','excluded_languages']}

def team_rows(db,workspace,space):
    default=p.default_space(db,workspace)
    return [t for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace,SupportTeam.archived.is_(False)).order_by(SupportTeam.name)) if str(space) in (t.teamspace_ids if t.teamspace_ids is not None else [str(default)])]

def groups(db,workspace,space):
    rows=list(db.scalars(select(Keyword).where(Keyword.workspace_id==workspace,Keyword.teamspace_id==space).order_by(Keyword.name)))
    teams={t.id:t for t in team_rows(db,workspace,space)};out=[];seen=set()
    for row in rows:
        if row.kind=='team':
            team=teams.get(row.team_id)
            if not team:continue
            seen.add(team.id);name=team.name;variations=team.alternative_names
        else:name=row.name;variations=row.variations
        out.append({'id':str(row.id),'kind':row.kind,'team_id':str(row.team_id) if row.team_id else None,'name':name,'variations':variations,'tag_ids':row.tag_ids,'enabled':row.enabled,'revision':row.revision})
    for team in teams.values():
        if team.id not in seen:out.append({'id':str(uuid.uuid5(space,'perception-team:'+str(team.id))),'kind':'team','team_id':str(team.id),'name':team.name,'variations':team.alternative_names,'tag_ids':[],'enabled':False,'revision':0})
    return sorted(out,key=lambda x:(key(x['name']),x['id']))

def active_groups(db,source,linked=True):
    cfg=config(db,source.workspace_id,source.teamspace_id)
    allowed={str(v) for v in db.scalars(select(Link.keyword_id).where(Link.source_id==source.id))} if linked else None
    return [g for g in groups(db,source.workspace_id,source.teamspace_id) if g['enabled'] and (cfg.use_custom if g['kind']=='custom' else cfg.use_teams) and (allowed is None or g['id'] in allowed)]

def enabled_count(db,workspace):
    default=str(p.default_space(db,workspace));teams={t.id:t for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace,SupportTeam.archived.is_(False)))}
    count=0
    for row in db.scalars(select(Keyword).where(Keyword.workspace_id==workspace,Keyword.enabled.is_(True))):
        if row.kind=='custom':count+=1
        else:
            team=teams.get(row.team_id)
            if team and str(row.teamspace_id) in (team.teamspace_ids if team.teamspace_ids is not None else [default]):count+=1
    return count

def active_cap(db,workspace):
    db.flush()
    if enabled_count(db,workspace)>50:raise HTTPException(422,'You can have a maximum of 50 enabled keyword groups in a workspace.')


def tags_valid(db,workspace,space,ids):
    default=p.default_space(db,workspace);rows=list(db.scalars(select(TicketTag).where(TicketTag.workspace_id==workspace,TicketTag.archived.is_(False),TicketTag.id.in_(ids))))
    if len(rows)!=len(set(ids)) or any((r.teamspace_id or default)!=space for r in rows):raise HTTPException(422,'Choose tags from this teamspace.')
    return list(dict.fromkeys(str(v) for v in ids))

def options(db,workspace,user,collector,ai):
    available=[t for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace).order_by(Teamspace.name)) if str(t.id) in p.accessible_spaces(db,workspace,user) and p.allowed(db,workspace,'view_perception',user,teamspace_id=t.id)]
    if not available:raise HTTPException(403,'Your role cannot view Perception.')
    policy=db.get(WorkspaceSettings,workspace);tz=policy.timezone if policy else 'Africa/Nairobi'
    return {'teamspaces':[{'id':str(t.id),'name':t.name,'is_default':t.is_default,'can_manage':p.allowed(db,workspace,'manage_perception',user,teamspace_id=t.id)} for t in available],
        'timezone':tz,'today':utcnow().astimezone(ZoneInfo(tz)).date(),'platforms':[{'key':k,'name':v,'supported':k in {'facebook','x'}} for k,v in PLATFORMS.items()],
        'languages':[{'code':k,'name':v} for k,v in LANGUAGES.items()],'collector_ready':collector.available,'ai_ready':ai.available,'keyword_limit':50,
        'availability':{'overview':'available','raw':'preview','alerts':'preview','keywords':'available','sources':'available','categorization':'preview','translation':'available'}}

def state(db,workspace,space):
    cfg=config(db,workspace,space);default=p.default_space(db,workspace)
    return {'config':config_payload(cfg),'tags':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(TicketTag).where(TicketTag.workspace_id==workspace,TicketTag.archived.is_(False))) if (t.teamspace_id or default)==space],
        'fields':[{'id':str(f.id),'name':f.name,'description':f.description,'type':f.type} for f in db.scalars(select(ConversationField).where(ConversationField.workspace_id==workspace,ConversationField.archived.is_(False),ConversationField.scope=='conversation')) if f.type in {'dropdown','single_select_grouped'}],
        'processing':dict(db.execute(select(Entry.processing,func.count()).where(Entry.workspace_id==workspace,Entry.teamspace_id==space).group_by(Entry.processing)).all()),'source_options':[{'id':str(v.id),'name':v.name} for v in db.scalars(select(Source).where(Source.workspace_id==workspace,Source.teamspace_id==space).order_by(Source.name))],'keyword_options':groups(db,workspace,space),'can_fields':p.allowed(db,workspace,'manage_custom_fields',db.info.get('actor_id'),teamspace_id=space)}

def keyword_list(db,workspace,space,kind,q,status,offset,limit):
    all_rows=groups(db,workspace,space);links=Counter(str(i) for i in db.scalars(select(Link.keyword_id).where(Link.teamspace_id==space)))
    counts=Counter(k for snapshot in db.scalars(select(Entry.keywords).where(Entry.workspace_id==workspace,Entry.teamspace_id==space)) for k in snapshot)
    cfg=config(db,workspace,space)
    rows=[{**g,'used_in_sources':links[g['id']],'scraped_data':counts[g['id']],'active':g['enabled'] and (cfg.use_custom if g['kind']=='custom' else cfg.use_teams)} for g in all_rows if g['kind']==kind and key(q) in key(g['name']+' '+' '.join(g['variations'])) and (status=='all' or g['enabled']==(status=='enabled'))]
    return {'items':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit,'enabled_count':enabled_count(db,workspace)}

def affected(db,workspace,space,ids):
    return len(set(db.scalars(select(Link.source_id).where(Link.teamspace_id==space,Link.keyword_id.in_(ids)))))

def confirm(needed,confirmed,count=0):
    if needed and not confirmed:raise HTTPException(409,{'message':'Confirm this change to keywords used by sources. Sources without active keywords will pause.','requires_confirmation':True,'affected_sources':count})

def actor_name(db,user):
    row=db.get(User,user) if user else None
    return (row.full_name or row.email) if row else 'Petrix AI'

def event(db,source,name,user=None,detail=None):
    db.add(Run(workspace_id=source.workspace_id,source_id=source.id,trigger='event',event=name,status='success',actor_id=user,actor_name=actor_name(db,user),finished_at=utcnow(),detail=detail or {}))

def cancel_scheduled(db,source):
    for row in db.scalars(select(Run).where(Run.source_id==source.id,Run.status=='scheduled',Run.trigger=='recurring')):
        row.status='cancelled';row.finished_at=utcnow()
    source.next_run_at=None

def delta(source):return timedelta(**{source.interval_unit:source.interval_value})

def enqueue(db,source,trigger,start,end,user=None,scheduled=None):
    row=Run(workspace_id=source.workspace_id,source_id=source.id,trigger=trigger,range_start=start,range_end=end,scheduled_at=scheduled or utcnow(),actor_id=user,actor_name=actor_name(db,user))
    db.add(row);db.flush();return row

def reset_schedule(db,source,user=None,now=None):
    cancel_scheduled(db,source)
    if source.recurring:
        start=now or utcnow();source.next_run_at=start+delta(source);enqueue(db,source,'recurring',start,source.next_run_at,user,source.next_run_at)

def pause_empty(db,workspace,space,user=None):
    db.flush()
    for source in db.scalars(select(Source).where(Source.workspace_id==workspace,Source.teamspace_id==space,Source.method=='keyword',Source.recurring.is_(True))):
        if not active_groups(db,source):
            source.recurring=False;source.revision+=1;cancel_scheduled(db,source);event(db,source,'recurring_auto_paused',user)

def save_config(db,workspace,space,user,body):
    lock(db,workspace);row=config(db,workspace,space,True);check(row,body.expected_revision)
    affected_ids=[uuid.UUID(g['id']) for g in groups(db,workspace,space) if g['enabled'] and ((g['kind']=='custom' and row.use_custom and not body.use_custom) or (g['kind']=='team' and row.use_teams and not body.use_teams))]
    n=affected(db,workspace,space,affected_ids);confirm(n>0,body.confirmed,n)
    for k,v in body.model_dump(exclude={'expected_revision','confirmed'}).items():setattr(row,k,v)
    row.revision+=1;pause_empty(db,workspace,space,user);db.commit();return config_payload(row)

def save_keyword(db,workspace,space,user,body,identifier=None):
    lock(db,workspace);row=db.scalar(select(Keyword).where(Keyword.id==identifier,Keyword.workspace_id==workspace,Keyword.teamspace_id==space,Keyword.kind=='custom')) if identifier else None
    if identifier and not row:raise HTTPException(404,'Keyword group not found.')
    if row:
        check(row,body.expected_revision);n=affected(db,workspace,space,[row.id]);confirm(row.enabled and not body.enabled and n>0,body.confirmed,n)
    if db.scalar(select(Keyword.id).where(Keyword.teamspace_id==space,Keyword.kind=='custom',Keyword.name_key==key(body.name),Keyword.id!=(identifier or uuid.uuid4()))):raise HTTPException(409,'This keyword group name is already used.')
    tag_ids=tags_valid(db,workspace,space,body.tag_ids)
    if not row:row=Keyword(workspace_id=workspace,teamspace_id=space,name=body.name,name_key=key(body.name),kind='custom');db.add(row)
    else:row.revision+=1
    row.name=body.name;row.name_key=key(body.name);row.variations=body.variations;row.enabled=body.enabled;row.tag_ids=tag_ids
    active_cap(db,workspace);pause_empty(db,workspace,space,user);db.commit();return next(g for g in groups(db,workspace,space) if g['id']==str(row.id))

def save_team(db,workspace,space,user,team_id,body):
    lock(db,workspace);team=next((t for t in team_rows(db,workspace,space) if t.id==team_id),None)
    if not team:raise HTTPException(404,'Team not found in this teamspace.')
    identifier=uuid.uuid5(space,'perception-team:'+str(team.id));row=db.get(Keyword,identifier)
    if (row.revision if row else 0)!=body.expected_revision:raise HTTPException(409,'This team keyword group changed. Reload before saving.')
    n=affected(db,workspace,space,[identifier]);confirm(row and row.enabled and not body.enabled and n>0,body.confirmed,n)
    tags=tags_valid(db,workspace,space,body.tag_ids)
    if not row:row=Keyword(id=identifier,workspace_id=workspace,teamspace_id=space,team_id=team.id,name=team.name,name_key=str(team.id),kind='team');db.add(row)
    else:row.revision+=1
    row.enabled=body.enabled;row.tag_ids=tags
    active_cap(db,workspace);pause_empty(db,workspace,space,user);db.commit();return next(g for g in groups(db,workspace,space) if g['id']==str(identifier))

def bulk(db,workspace,space,user,body):
    lock(db,workspace);matches={g['id'] for g in groups(db,workspace,space) if g['kind']=='custom' and key(body.q) in key(g['name']+' '+' '.join(g['variations'])) and (body.status=='all' or g['enabled']==(body.status=='enabled'))}
    ids=matches if body.all_matching else {str(i) for i in body.ids}
    if not ids:raise HTTPException(422,'Select at least one keyword group.')
    if not ids<=matches:raise HTTPException(404,'A selected keyword group was not found in this filter.')
    n=affected(db,workspace,space,[uuid.UUID(i) for i in ids]);confirm(body.all_matching or body.operation=='delete' or body.operation=='disable' and (len(ids)>1 or n>0),body.confirmed,n)
    rows=list(db.scalars(select(Keyword).where(Keyword.id.in_([uuid.UUID(i) for i in ids]),Keyword.teamspace_id==space)))
    for row in rows:
        if body.operation=='delete':db.delete(row)
        else:row.enabled=body.operation=='enable';row.revision+=1
    active_cap(db,workspace);pause_empty(db,workspace,space,user);db.commit();return {'affected':len(rows),'affected_sources':n}

def source_for(db,workspace,space,identifier):
    row=db.scalar(select(Source).where(Source.workspace_id==workspace,Source.teamspace_id==space,Source.id==identifier))
    if not row:raise HTTPException(404,'Source not found.')
    return row

def range_values(source,body,now=None):
    now=now or utcnow();whole=source.platform=='facebook' and source.method=='keyword';tz=ZoneInfo(source.timezone)
    def parse(v,end=False):
        try:
            if len(v)==10:
                value=date.fromisoformat(v);return datetime.combine(value+timedelta(days=1) if end else value,time.min,timezone.utc if whole else tz).astimezone(timezone.utc)
            if whole:raise ValueError
            dt=datetime.fromisoformat(v.replace('Z','+00:00'))
            if not dt.tzinfo:
                # Reject nonexistent or ambiguous local wall times; an explicit offset resolves an ambiguous time.
                a=dt.replace(tzinfo=tz,fold=0);b=dt.replace(tzinfo=tz,fold=1)
                if a.utcoffset()!=b.utcoffset() or a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)!=dt:raise ValueError
                dt=a
            return dt.astimezone(timezone.utc)
        except ValueError:raise HTTPException(422,'Facebook keyword ranges require whole UTC dates.' if whole else 'Choose valid source-local dates and times; include an offset for a daylight-saving transition.') from None
    start=parse(body.start);end=parse(body.end,True)
    # Whole-day inclusive ranges may end at the next midnight, but cannot include a future calendar date.
    latest=now if len(body.end)>10 else datetime.combine(now.astimezone(timezone.utc if whole else tz).date()+timedelta(days=1),time.min,timezone.utc if whole else tz).astimezone(timezone.utc)
    minimum=datetime(2024,1,1,tzinfo=timezone.utc if whole else tz).astimezone(timezone.utc)
    if start<minimum or start>=end or end>latest or (end-start)>timedelta(days=366):raise HTTPException(422,'Choose a range from 1 January 2024, of at most one year, without future dates.')
    # Calendar-year maximum, including leap years, measured in source date semantics.
    local=start.astimezone(timezone.utc if whole else tz)
    try:anniversary=local.replace(year=local.year+1)
    except ValueError:anniversary=local.replace(year=local.year+1,day=28)
    if end>anniversary.astimezone(timezone.utc):raise HTTPException(422,'The maximum scraping date range is one year.')
    return start,end

def validate_links(db,source,ids,recurring=False,existing=False):
    active={g['id'] for g in active_groups(db,source,False)}
    previous={str(v) for v in db.scalars(select(Link.keyword_id).where(Link.source_id==source.id))} if existing else set()
    good=active|previous
    if (not existing or recurring) and not (set(map(str,ids))&active):raise HTTPException(422,'Select at least one active keyword group.')
    if not set(map(str,ids))<=good:raise HTTPException(422,'Select active keyword groups from this teamspace.')
    conflict=db.scalar(select(Link.source_id).where(Link.teamspace_id==source.teamspace_id,Link.platform==source.platform,Link.keyword_id.in_(ids),Link.source_id!=source.id))
    if conflict:raise HTTPException(409,'A keyword group can be linked to only one keyword source per platform in a teamspace.')

def save_source(db,workspace,space,user,body,identifier=None):
    lock(db,workspace);row=source_for(db,workspace,space,identifier) if identifier else None
    if row:
        check(row,body.expected_revision)
        if row.platform!=body.platform or row.method!=body.method or row.url!=body.url:raise HTTPException(422,'Create another source to change its platform, method or page URL.')
    if db.scalar(select(Source.id).where(Source.teamspace_id==space,Source.platform==body.platform,Source.name_key==key(body.name),Source.id!=(identifier or uuid.uuid4()))):raise HTTPException(409,'This source name is already used on this platform.')
    if not row:
        row=Source(id=uuid.uuid4(),workspace_id=workspace,teamspace_id=space,platform=body.platform,method=body.method,url=body.url,name=body.name,name_key=key(body.name),timezone=body.timezone,revision=1,recurring=False,interval_unit='hours',interval_value=24);db.add(row)
    old_recurring=row.recurring;old_interval=(row.interval_unit,row.interval_value)
    if row.method=='keyword':validate_links(db,row,body.keyword_ids,body.recurring,bool(identifier))
    for k in ['name','timezone','recurring','interval_unit','interval_value']:setattr(row,k,getattr(body,k))
    row.name_key=key(row.name)
    if identifier:row.revision+=1
    if row.method=='keyword':
        db.flush();db.execute(delete(Link).where(Link.source_id==row.id))
        for i in body.keyword_ids:db.add(Link(source_id=row.id,keyword_id=i,teamspace_id=space,platform=row.platform))
    if identifier and body.historical:raise HTTPException(422,'Start a manual scrape from the source detail panel.')
    historic=range_values(row,body.historical) if body.historical else None
    if old_recurring!=row.recurring:event(db,row,'recurring_enabled' if row.recurring else 'recurring_disabled',user)
    if identifier and old_interval!=(row.interval_unit,row.interval_value):event(db,row,'interval_updated',user,{'unit':row.interval_unit,'value':row.interval_value})
    if not identifier or old_recurring!=row.recurring or old_interval!=(row.interval_unit,row.interval_value):reset_schedule(db,row,user)
    if historic:enqueue(db,row,'historical',*historic,user)
    db.commit();return source_payload(db,row)

def toggle_source(db,workspace,space,user,identifier,body):
    lock(db,workspace);row=source_for(db,workspace,space,identifier);check(row,body.expected_revision)
    if body.recurring and row.method=='keyword' and not active_groups(db,row):raise HTTPException(422,'Add active keywords before enabling recurring scraping.')
    if row.recurring!=body.recurring:
        row.recurring=body.recurring;row.revision+=1;event(db,row,'recurring_enabled' if row.recurring else 'recurring_disabled',user);reset_schedule(db,row,user)
    db.commit();return source_payload(db,row)

def run_payload(row):return {k:getattr(row,k) for k in ['id','trigger','event','status','range_start','range_end','scheduled_at','started_at','finished_at','attempts','error','detail','actor_name']}

def source_payload(db,row):
    group_map={g['id']:g for g in groups(db,row.workspace_id,row.teamspace_id)}
    ids=[str(i) for i in db.scalars(select(Link.keyword_id).where(Link.source_id==row.id))]
    def last(trigger):
        query=select(Run).where(Run.source_id==row.id,Run.event=='scrape',Run.trigger.in_(trigger))
        if trigger==['recurring']:query=query.order_by(case((Run.status.in_(['in_progress','success','failed']),0),else_=1),Run.started_at.desc().nulls_last())
        v=db.scalar(query.order_by(Run.scheduled_at.desc(),Run.id.desc()).limit(1));return run_payload(v) if v else None
    return {**{k:getattr(row,k) for k in ['id','platform','method','name','url','timezone','recurring','interval_unit','interval_value','next_run_at','revision']},'keyword_ids':ids,'scope':[{'id':i,'name':group_map[i]['name'],'active':group_map[i]['enabled'] and (config(db,row.workspace_id,row.teamspace_id).use_custom if group_map[i]['kind']=='custom' else config(db,row.workspace_id,row.teamspace_id).use_teams)} for i in ids if i in group_map], 'auto':last(['recurring']),'manual':last(['manual','historical'])}

def sources(db,workspace,space,platform,offset,limit):
    query=select(Source).where(Source.workspace_id==workspace,Source.teamspace_id==space,Source.platform==platform)
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    return {'items':[source_payload(db,r) for r in db.scalars(query.order_by(Source.created_at.desc(),Source.id).offset(offset).limit(limit))],'total':total}

def source_detail(db,row,offset,limit):
    query=select(Run).where(Run.source_id==row.id)
    return {**source_payload(db,row),'logs':{'items':[run_payload(v) for v in db.scalars(query.order_by(Run.scheduled_at.desc(),Run.id.desc()).offset(offset).limit(limit))],'total':db.scalar(select(func.count()).select_from(query.subquery()))}}

def manual(db,row,user,body):
    lock(db,row.workspace_id);db.refresh(row)
    if row.method=='keyword' and not active_groups(db,row):raise HTTPException(422,'This source has no active keyword groups.')
    if db.scalar(select(Run.id).where(Run.source_id==row.id,Run.event=='scrape',Run.trigger.in_(['manual','historical']),Run.status.in_(['scheduled','in_progress']))):raise HTTPException(409,'A manual scrape is already queued or in progress.')
    start,end=range_values(row,body);run=enqueue(db,row,'manual',start,end,user);db.commit();return run_payload(run)

def retry(db,row,user,run_id):
    lock(db,row.workspace_id);db.refresh(row);run=db.scalar(select(Run).where(Run.source_id==row.id,Run.id==run_id))
    if not run:raise HTTPException(404,'Scrape run not found.')
    if run.event!='scrape' or run.status!='failed':raise HTTPException(409,'Only failed scrape runs can be retried.')
    if row.method=='keyword' and not active_groups(db,row):raise HTTPException(422,'Add active keywords before retrying this scrape.')
    if run.attempts>=10:raise HTTPException(422,'This run reached its retry limit. Start a new manual scrape.')
    run.status='scheduled';run.scheduled_at=utcnow();run.worker_token=None;run.lease_until=None;run.error='';run.actor_id=user;run.actor_name=actor_name(db,user);run.detail={**run.detail,'retry':True};db.commit();return run_payload(run)
