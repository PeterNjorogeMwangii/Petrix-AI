"""Permission-scoped metadata for aggregate reports; message bodies are not loaded."""
import json,uuid
from collections import defaultdict
from datetime import date,datetime,time,timedelta,timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import (Agent,AgentConversation,AgentMessage,SupportTicket,TicketEmail,TicketInboundEmail,
    SupportPerson,WorkspaceSettings,SupportTeam,User,Membership,TicketTag,TicketTagGroup,ConversationField,
    RecordLabels,AnalyticsRecordEvent,Teamspace)
from app.workspace_settings import permissions as p
from app.teamspace import records as r
from app.people.service import chat_channels
from app.analytics.tracking import chat_status

PERMISSIONS={'messaging':'analytics_messaging','chats':'analytics_chats','csat':'analytics_csat',
    'tickets':'analytics_tickets','agents':'analytics_users_teams'}
TITLES={'live':'Live report','messaging':'Messaging','chats':'Chats','csat':'CSAT','tickets':'Tickets','agents':'Live agents'}


def zone(db,workspace_id):
    policy=db.get(WorkspaceSettings,workspace_id)
    try:return ZoneInfo(policy.timezone if policy else 'Africa/Nairobi')
    except (ZoneInfoNotFoundError,ValueError):return ZoneInfo('Africa/Nairobi')


def bounds(db,workspace_id,start=None,end=None,live=False):
    tz=zone(db,workspace_id);today=utcnow().astimezone(tz).date()
    start=today if live else start or today.replace(day=1);end=today if live else end or today
    if start>end or (end-start).days>365:raise HTTPException(422,'Choose a date range of at most 366 days, with the start before the end.')
    if end>today:raise HTTPException(422,'Reports cannot include future dates.')
    return start,end,datetime.combine(start,time.min,tz).astimezone(timezone.utc),datetime.combine(end+timedelta(days=1),time.min,tz).astimezone(timezone.utc),tz


def spaces(db,workspace_id,user_id,report,space_id=None):
    accessible=p.accessible_spaces(db,workspace_id,user_id)
    if space_id and str(space_id) not in accessible:raise HTTPException(404,'Teamspace not found.')
    keys=list(PERMISSIONS.values()) if report=='live' else [PERMISSIONS[report]]
    allowed={uuid.UUID(s) for s in accessible if (not space_id or s==str(space_id)) and any(p.allowed(db,workspace_id,key,user_id,teamspace_id=s) for key in keys)}
    if not allowed:raise HTTPException(403,'Your role cannot view this Analytics report.')
    return allowed


def options(db,workspace_id,user_id):
    accessible=p.accessible_spaces(db,workspace_id,user_id);reports=[]
    for key,grant in PERMISSIONS.items():
        if any(p.allowed(db,workspace_id,grant,user_id,teamspace_id=s) for s in accessible):reports.append({'key':key,'name':TITLES[key]})
    if reports:reports.insert(0,{'key':'live','name':'Live report'})
    if not reports and not any(p.allowed(db,workspace_id,'view_custom_analytics',user_id,teamspace_id=s) for s in accessible):raise HTTPException(403,'Your role cannot view Analytics.')
    scope={uuid.UUID(s) for s in accessible if any(p.allowed(db,workspace_id,k,user_id,teamspace_id=s) for k in [*PERMISSIONS.values(),'view_custom_analytics'])}
    default=p.default_space(db,workspace_id)
    inside=lambda row:getattr(row,'teamspace_id',None) in scope or getattr(row,'teamspace_id',None) is None and default in scope
    teams=list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False))))
    members=db.execute(select(User,Membership).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id)).all()
    users=[{'id':str(u.id),'name':u.full_name or u.email.split('@')[0]} for u,m in members if m.teamspace_ids is None or {str(s) for s in scope}&set(m.teamspace_ids)]
    fields=list(r.field_definitions(db,workspace_id,'conversation'))
    return {'reports':reports,'timezone':str(zone(db,workspace_id)),'today':utcnow().astimezone(zone(db,workspace_id)).date(),
        'teamspaces':[{'id':str(s.id),'name':s.name,'is_default':s.is_default} for s in db.scalars(select(Teamspace).where(Teamspace.id.in_(scope)).order_by(Teamspace.name))],
        'ai_agents':[{'id':str(a.id),'name':a.name} for a in db.scalars(select(Agent).where(Agent.workspace_id==workspace_id)) if inside(a)],
        'users':users,'teams':[{'id':str(t.id),'name':t.name} for t in teams if set(t.teamspace_ids if t.teamspace_ids is not None else [str(default)])&{str(i) for i in scope}],
        'tags':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(TicketTag).where(TicketTag.workspace_id==workspace_id,TicketTag.archived.is_(False))) if inside(t)],
        'groups':[{'id':str(t.id),'name':t.name} for t in db.scalars(select(TicketTagGroup).where(TicketTagGroup.workspace_id==workspace_id,TicketTagGroup.archived.is_(False))) if inside(t)],
        'fields':[{'key':f.key,'name':f.name,'type':f.type,'options':f.options,'default_value':f.default_value} for f in fields],
        'can_custom':any(p.allowed(db,workspace_id,'view_custom_analytics',user_id,teamspace_id=s) for s in accessible)}


class Dataset:
    def __init__(self,db,workspace_id,user_id,scope,filters):
        self.db,self.workspace_id,self.user_id,self.scope,self.filters=db,workspace_id,user_id,scope,filters
        self.default=p.default_space(db,workspace_id)
        self.agents={a.id:a for a in db.scalars(select(Agent).where(Agent.workspace_id==workspace_id))}
        self.teams={str(t.id):t for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))}
        self.users={str(u.id):u.full_name or u.email.split('@')[0] for u in db.scalars(select(User).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id))}
        self.fields={f.key:f for f in r.field_definitions(db,workspace_id,'conversation')}
        self.labels={(v.kind,v.record_id):v for v in db.scalars(select(RecordLabels).where(RecordLabels.workspace_id==workspace_id,RecordLabels.kind.in_(['chats','tickets'])))}
        self.validate()
        self._facts={};self._messages={};self._events={}

    def validate(self):
        f=self.filters
        if set(f.ai_agents)-set(self.agents) or {str(i) for i in f.live_agents}-set(self.users) or {str(i) for i in f.teams}-set(self.teams):raise HTTPException(422,'Choose agents and teams in this workspace.')
        for values,model in [(f.tags,TicketTag),(f.groups,TicketTagGroup)]:
            known=set(self.db.scalars(select(model.id).where(model.workspace_id==self.workspace_id,model.archived.is_(False),model.id.in_(values))))
            if set(values)-known:raise HTTPException(422,'Choose active tags/groups in this workspace.')
        if any(v.key not in self.fields for v in f.custom_fields):raise HTTPException(422,'Choose active conversation custom fields.')
        from app.custom_fields.schemas import FieldCreate
        for value in f.custom_fields:
            field=self.fields[value.key]
            try:FieldCreate.model_validate({'scope':'conversation','name':field.name,'type':field.type,'options':field.options,'default_value':value.value})
            except ValueError:raise HTTPException(422,'Use a value matching the custom-field type and options.') from None

    def allowed(self,row,kind):return r.record_space(self.db,kind,row) in self.scope
    def agent_id(self,row,kind):
        if kind=='chats':return row.agent_id
        chat=self.db.get(AgentConversation,row.conversation_id) if row.conversation_id else None
        return chat.agent_id if chat else None
    def team_ids(self,row,kind):
        if kind=='chats':return list((row.live_context or {}).get('routing',{}).get('team_ids',[]))
        from app.tickets.workspace import team_ids
        return team_ids(row)
    def live(self,row):return row.live_status!='none' or bool((row.live_context or {}).get('handoff_at'))
    def rating(self,row):
        value=(row.workflow_state or {}).get('csat_rating')
        return value if type(value) is int and 1<=value<=5 else None
    def channels(self,row,kind):return chat_channels(self.db,row) if kind=='chats' else {'email'}
    def matches(self,row,kind):
        if not self.allowed(row,kind):return False
        f=self.filters;state=row.workflow_state or {} if kind=='chats' else {}
        if kind=='chats':
            if not f.include_tests and not 'tester' in f.channels and state.get('channel')=='tester':return False
            if f.chat_type!='all' and self.live(row)!=(f.chat_type=='live'):return False
            rating=self.rating(row)
            if (f.ratings or f.unrated) and not (rating in f.ratings or f.unrated and rating is None):return False
        if f.creator_type!='all' and kind=='tickets' and (row.origin=='manual')!=(f.creator_type=='live'):return False
        if f.ai_agents and self.agent_id(row,kind) not in f.ai_agents:return False
        assigned=row.live_assigned_user_id if kind=='chats' else row.assignee_id if row.assignee_type=='user' else None
        if f.live_agents and assigned not in f.live_agents:return False
        if f.teams and not set(map(str,f.teams))&set(self.team_ids(row,kind)):return False
        if f.channels and not set(f.channels)&set(self.channels(row,kind)):return False
        status=chat_status(row) if kind=='chats' else row.status
        # Pending is the operational label for an in-progress ticket in these reports.
        status='pending' if status=='in_progress' else status
        normal='open' if status=='ai' else status
        if f.statuses and not any(v==status or ('pending' if v=='in_progress' else v)==normal for v in f.statuses):return False
        if f.tags or f.groups:
            label=r.label_payload(self.db,self.workspace_id,kind,row)
            if f.tags and not set(map(str,f.tags))&{t['id'] for t in label['effective_tags']}:return False
            if f.groups:
                effective=set(label['group_ids'])
                for tag in label['effective_tags']:
                    value=self.db.get(TicketTag,uuid.UUID(tag['id']))
                    if value:effective.update(r.tag_group_ids(self.db,value))
                if not effective&set(map(str,f.groups)):return False
        for field in f.custom_fields:
            value=(row.custom_fields or {}).get(field.key)
            actual=value.get('value') if isinstance(value,dict) and 'value' in value else value if value is not None else self.fields[field.key].default_value
            if actual!=field.value:return False
        return True

    def rows(self,kind):
        if kind not in self._facts:
            model=AgentConversation if kind=='chats' else SupportTicket
            self._facts[kind]=[row for row in self.db.scalars(select(model).where(model.workspace_id==self.workspace_id).order_by(model.created_at,model.id).execution_options(yield_per=200)) if self.matches(row,kind)]
        return self._facts[kind]

    def messages(self,kind,ids):
        missing=[i for i in ids if (kind,i) not in self._messages]
        for offset in range(0,len(missing),500):
            batch=missing[offset:offset+500]
            for i in batch:self._messages[(kind,i)]=[]
            if kind=='chats':
                query=select(AgentMessage.id,AgentMessage.conversation_id,AgentMessage.role,AgentMessage.author_id,AgentMessage.created_at,AgentMessage.message_data['source'].as_string().label('source'),AgentMessage.message_data['source_request_id'].as_string().label('request_id')).where(AgentMessage.conversation_id.in_(batch),func.coalesce(AgentMessage.message_data['transferred_history'].as_boolean(),False).is_(False),~((AgentMessage.role=='assistant') & AgentMessage.message_data['author_name'].as_string().is_not(None) & (func.coalesce(AgentMessage.message_data['source'].as_string(),'')!='live_agent')))
                for m in self.db.execute(query):self._messages[(kind,m.conversation_id)].append(SimpleNamespace(id=m.id,identity=m.request_id or str(m.id),role=m.role,author_id=m.author_id,at=m.created_at,source=m.source or 'ai'))
            else:
                for e in self.db.execute(select(TicketEmail.id,TicketEmail.ticket_id,TicketEmail.kind,TicketEmail.actor_id,TicketEmail.sent_at).where(TicketEmail.ticket_id.in_(batch),TicketEmail.recipient_type=='customer',TicketEmail.status=='sent')):
                    if e.sent_at:self._messages[(kind,e.ticket_id)].append(SimpleNamespace(id=e.id,identity=str(e.id),role='assistant',author_id=e.actor_id,at=e.sent_at,source='live_agent' if e.kind=='reply' else 'email'))
                for e in self.db.execute(select(TicketInboundEmail.id,TicketInboundEmail.ticket_id,TicketInboundEmail.created_at).where(TicketInboundEmail.ticket_id.in_(batch))):self._messages[(kind,e.ticket_id)].append(SimpleNamespace(id=e.id,identity=str(e.id),role='user',author_id=None,at=e.created_at,source='email'))
            for i in batch:self._messages[(kind,i)].sort(key=lambda m:(m.at,str(m.id)))
        return {i:self._messages.get((kind,i),[]) for i in ids}

    def events(self,kind,ids):
        missing=[i for i in ids if (kind,i) not in self._events]
        column=AnalyticsRecordEvent.conversation_id if kind=='chats' else AnalyticsRecordEvent.ticket_id
        for offset in range(0,len(missing),500):
            batch=missing[offset:offset+500]
            for i in batch:self._events[(kind,i)]=[]
            for e in self.db.scalars(select(AnalyticsRecordEvent).where(column.in_(batch)).order_by(AnalyticsRecordEvent.created_at,AnalyticsRecordEvent.id)):
                self._events[(kind,getattr(e,'conversation_id' if kind=='chats' else 'ticket_id'))].append(e)
        return {i:self._events.get((kind,i),[]) for i in ids}

    def dimension(self,row,kind,name,at,tz):
        local=at.astimezone(tz)
        if name=='hour':return local.strftime('%H:00')
        if name=='day':return local.date().isoformat()
        if name=='week':return (local.date()-timedelta(days=local.weekday())).isoformat()
        if name=='month':return local.strftime('%Y-%m')
        if name=='chat_type':return 'Livechats' if kind=='chats' and self.live(row) else 'AI chats' if kind=='chats' else 'Tickets'
        if name=='creator_type':return 'Live agents' if kind=='tickets' and row.origin=='manual' else 'AI agents'
        if name=='status':
            value=chat_status(row) if kind=='chats' else 'pending' if row.status=='in_progress' else row.status
            return ('open' if value=='ai' else value).title()
        if name=='rating':return str(self.rating(row)) if kind=='chats' and self.rating(row) else 'Unrated'
        if name=='ai_agent':
            agent=self.agents.get(self.agent_id(row,kind));return agent.name if agent else 'No AI agent'
        if name=='live_agent':
            assigned=row.live_assigned_user_id if kind=='chats' else row.assignee_id if row.assignee_type=='user' else None
            return self.users.get(str(assigned),'Unassigned')
        if name=='team':return ', '.join(self.teams[i].name for i in self.team_ids(row,kind) if i in self.teams) or 'Unassigned'
        if name=='channel':return ', '.join(sorted(self.channels(row,kind)))
        return 'Total'
