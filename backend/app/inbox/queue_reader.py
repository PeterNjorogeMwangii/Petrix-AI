"""Request-scoped Inbox reads: light candidates, batched evidence, page summaries.

No permission decisions survive the request. Detail and mutation endpoints retain
 their own current authorization. Counts and filters run after record visibility.
"""
import json,uuid
from types import SimpleNamespace
from sqlalchemy import select,func,or_,and_
from app.models import (Agent,AgentConversation as Chat,AgentMessage as Message,SupportTicket as Ticket,
    TicketEmail,TicketInboundEmail,SupportPerson,WorkspaceRole,ExternalConversation,AIInboundSession,
    AIOutgoingSession,TeamspaceOperations,RecordLabels,TicketTag,TicketTagGroup,TagGroupMembership)
from app.workspace_settings import permissions as p


def objects(db,query):
    return [SimpleNamespace(**dict(row._mapping)) for row in db.execute(query)]


def compact(value):return {k:v for k,v in (value or {}).items() if v is not None}


def projected_json(column,keys):
    values=[]
    for key in keys:values.extend([key,column[key]])
    return func.json_build_object(*values)


class Scope:
    """The current member's workspace/team grants, read once for this response."""
    def __init__(self,db,workspace_id,user_id):
        self.db=db;self.workspace_id=workspace_id;self.user_id=user_id
        self.member=p.membership(db,workspace_id,user_id)
        self.spaces=p.accessible_spaces(db,workspace_id,user_id)
        self.default=p.default_space(db,workspace_id)
        self.grants=p.workspace_grants(db,workspace_id,user_id)
        self.teams=p.teams_for(db,workspace_id,user_id)
        roles={v.id:v for v in db.scalars(select(WorkspaceRole).where(WorkspaceRole.workspace_id==workspace_id))}
        self.custom=bool(self.member and (self.member.custom_role_id or any(t.role_id for t in self.teams)))
        self.team_roles=[]
        for team in self.teams:
            role=roles.get(team.role_id)
            if role and role.kind=='team':
                spaces=set(team.teamspace_ids) if team.teamspace_ids is not None else {str(self.default)}
                self.team_roles.append((str(team.id),set(role.permissions),spaces&self.spaces))
        from app.tickets.workspace import legacy
        self.legacy=legacy(db,workspace_id,user_id)
        self.pending={};self.external=set()

    def allowed(self,key,space=None):
        if key in self.grants:return space is None or str(space) in self.spaces
        if key not in p.TEAM or space is None or str(space) not in self.spaces:return False
        return any(key in grants and str(space) in spaces for _,grants,spaces in self.team_roles)

    def record_allowed(self,kind,action,assigned=None,team=None,space=None):
        if space is None and self.member and self.member.teamspace_ids is not None:return False
        if space is not None and str(space) not in self.spaces:return False
        scopes={s for s in ('all','teams','own') if action+'_'+kind+'_'+s in self.grants}
        if 'all' in scopes or 'own' in scopes and str(assigned)==str(self.user_id) or 'teams' in scopes and str(team) in {str(t.id) for t in self.teams}:return True
        for team_id,grants,spaces in self.team_roles:
            if str(space) not in spaces:continue
            scoped={s for s in ('all','teams','own') if action+'_'+kind+'_'+s in grants}
            if scoped&{'all','teams'} and str(team)==team_id or 'own' in scoped and str(assigned)==str(self.user_id):return True
        return False

    def eligible(self,row):
        from app.inbox.service import eligible
        routing=row.live_context.get('routing') or {}
        space=row.space_id if self.custom else None
        key=(space,json.dumps(routing,sort_keys=True))
        if key not in self.pending:
            self.pending[key]=str(self.user_id) in {v['id'] for v in eligible(self.db,self.workspace_id,routing,**({'space_id':space} if self.custom else {}))}
        return self.pending[key]

    def live_allowed(self,row,action):
        teams=row.live_context.get('routing',{}).get('team_ids',[]) or [None]
        test=lambda assigned:any(self.record_allowed('livechats',action,assigned,team,row.space_id) for team in teams)
        if test(row.live_assigned_user_id):return True
        return row.live_status=='pending' and self.eligible(row) and test(self.user_id)

    def chat_allowed(self,row,action='view'):
        if row.live_status!='none':return self.live_allowed(row,'view') and self.live_allowed(row,action)
        if self.custom:
            can_view=any(self.record_allowed('livechats','view',row.live_assigned_user_id,t,row.agent_space) for t in row.live_context.get('routing',{}).get('team_ids',[]) or [None])
        else:
            can_view=row.id in self.external and (row.user_id==self.user_id or row.live_assigned_user_id==self.user_id or self.legacy)
        permitted=can_view or row.user_id==self.user_id and self.allowed('view_ai_agents',row.space_id) or self.record_allowed('livechats',action,row.live_assigned_user_id,None,row.space_id)
        if permitted and action=='manage' and self.custom:
            return any(self.record_allowed('livechats','manage',row.live_assigned_user_id,t,row.agent_space) for t in row.live_context.get('routing',{}).get('team_ids',[]) or [None])
        return bool(permitted)


class QueueReader:
    def __init__(self,db,workspace_id,user_id):
        self.db=db;self.workspace_id=workspace_id;self.user_id=user_id;self.scope=Scope(db,workspace_id,user_id)
        self.chats=[];self.tickets=[];self.people={};self.people_names={};self.manage_tickets=set();self.handoffs=set()
        self.chat_stats={};self.ticket_stats={};self.label_rows={};self.tags={};self.groups=set();self.tag_groups={};self.policies={}

    def candidates(self,section,search,at,space):
        from app.livechats.service import visibility
        from app.tickets.service import access_filter
        if section!='tickets':
            columns=[Chat.id,Chat.workspace_id,Chat.agent_id,Chat.user_id,Chat.person_id,Chat.agent_name,
                Chat.created_at,Chat.live_status,Chat.live_revision,Chat.live_assigned_user_id,Chat.live_updated_at,Chat.live_display_status,
                func.coalesce(Agent.teamspace_id,self.scope.default).label('space_id'),Agent.teamspace_id.label('agent_space'),
                projected_json(Chat.workflow_state,['channel','channel_provider','closed','external_handoff','language','webchat_name','webchat_icon','whatsapp_name','whatsapp_icon']).label('workflow_state'),
                projected_json(Chat.live_context,['routing','eligible_ids','handoff_at']).label('live_context')]
            query=select(*columns).outerjoin(Agent,Agent.id==Chat.agent_id).where(Chat.workspace_id==self.workspace_id,
                or_(visibility(self.db,self.workspace_id,self.user_id),and_(Chat.user_id==self.user_id,Chat.live_status=='none')))
            if search:query=query.where(Chat.created_at<=at)
            if space:query=query.where(func.coalesce(Agent.teamspace_id,self.scope.default)==space)
            self.chats=objects(self.db,query)
            for row in self.chats:
                row.workflow_state=compact(row.workflow_state);row.live_context=compact(row.live_context)
            ids=[v.id for v in self.chats if v.live_status=='none']
            if ids:self.scope.external=set(self.db.scalars(select(ExternalConversation.conversation_id).where(ExternalConversation.conversation_id.in_(ids),ExternalConversation.status.in_(['sending','transferred','uncertain']))))
            self.chats=[row for row in self.chats if self.scope.chat_allowed(row)]
        if section not in {'livechats','ai','test'}:
            columns=[Ticket.id,Ticket.workspace_id,Ticket.person_id,Ticket.agent_name,Ticket.created_at,Ticket.updated_at,Ticket.revision,
                Ticket.status,Ticket.assignee_id,Ticket.assignee_type,Ticket.assigned_team_ids,Ticket.full_name,Ticket.subject,Ticket.tags,
                func.substr(Ticket.description,1,180).label('description'),func.coalesce(Ticket.teamspace_id,Agent.teamspace_id,self.scope.default).label('space_id')]
            query=select(*columns).outerjoin(Chat,Chat.id==Ticket.conversation_id).outerjoin(Agent,Agent.id==Chat.agent_id).where(Ticket.workspace_id==self.workspace_id,access_filter(self.db,self.workspace_id,self.user_id,self.scope.legacy))
            if search:query=query.where(Ticket.created_at<=at)
            if space:query=query.where(func.coalesce(Ticket.teamspace_id,Agent.teamspace_id,self.scope.default)==space)
            self.tickets=objects(self.db,query)
        return [('chats',row) for row in self.chats]+[('tickets',row) for row in self.tickets]

    def search_ids(self,kind,rows,query,at):
        ids=[r.id for r in rows]
        if not ids:return set()
        match=lambda column:func.lower(column).contains(query.lower(),autoescape=True)
        if kind=='chats':
            return set(self.db.scalars(select(Message.conversation_id).where(Message.conversation_id.in_(ids),Message.created_at<=at,match(Message.content)).distinct()))
        direct=set(self.db.scalars(select(Ticket.id).where(Ticket.id.in_(ids),or_(match(Ticket.subject),match(Ticket.description)))))
        direct.update(self.db.scalars(select(TicketEmail.ticket_id).where(TicketEmail.ticket_id.in_(ids),TicketEmail.recipient_type=='customer',TicketEmail.created_at<=at,match(TicketEmail.body_text)).distinct()))
        direct.update(self.db.scalars(select(TicketInboundEmail.ticket_id).where(TicketInboundEmail.ticket_id.in_(ids),TicketInboundEmail.created_at<=at,match(TicketInboundEmail.content)).distinct()))
        return direct

    def evidence(self,rows,need_labels=False):
        chat_ids=[r.id for k,r in rows if k=='chats'];ticket_ids=[r.id for k,r in rows if k=='tickets']
        spaces={r.space_id for k,r in rows if k=='chats'}
        if spaces:self.policies={v.teamspace_id:v.assignment for v in self.db.scalars(select(TeamspaceOperations).where(TeamspaceOperations.workspace_id==self.workspace_id,TeamspaceOperations.teamspace_id.in_(spaces)))}
        if any(v.get('priority_enabled') for v in self.policies.values()):need_labels=True
        person_ids={r.person_id for _,r in rows if r.person_id}
        if need_labels:
            self.groups={str(v) for v in self.db.scalars(select(TicketTagGroup.id).where(TicketTagGroup.workspace_id==self.workspace_id,TicketTagGroup.archived.is_(False)))}
            self.tags={str(v.id):v for v in self.db.scalars(select(TicketTag).where(TicketTag.workspace_id==self.workspace_id,TicketTag.archived.is_(False)))}
            for tag in self.tags.values():self.tag_groups[str(tag.id)]=({str(tag.group_id)} if tag.group_id else set())&self.groups
            for tag,group in self.db.execute(select(TagGroupMembership.tag_id,TagGroupMembership.group_id).where(TagGroupMembership.tag_id.in_([v.id for v in self.tags.values()]))):
                if str(group) in self.groups:self.tag_groups[str(tag)].add(str(group))
            identifiers=chat_ids+ticket_ids+list(person_ids)
            if identifiers:self.label_rows={(v.kind,v.record_id):v for v in self.db.scalars(select(RecordLabels).where(RecordLabels.workspace_id==self.workspace_id,RecordLabels.record_id.in_(identifiers)))}
        if chat_ids:
            for role,where in [('last',Message.role.in_(['user','assistant'])),('customer',Message.role=='user'),('replied',and_(Message.role=='assistant',func.coalesce(Message.message_data['delivery_status'].as_string(),'').notin_(['failed','failed_to_send','failed_to_deliver'])))]:
                query=select(Message.conversation_id,Message.created_at,Message.sequence,func.substr(Message.content,1,180).label('content')).where(Message.conversation_id.in_(chat_ids),where).distinct(Message.conversation_id).order_by(Message.conversation_id,Message.sequence.desc())
                for value in objects(self.db,query):self.chat_stats.setdefault(value.conversation_id,{})[role]=value
            self.handoffs=set(self.db.scalars(select(AIInboundSession.conversation_id).where(AIInboundSession.conversation_id.in_(chat_ids))))|set(self.db.scalars(select(AIOutgoingSession.conversation_id).where(AIOutgoingSession.conversation_id.in_(chat_ids))))
        if ticket_ids:
            query=select(TicketInboundEmail.ticket_id,TicketInboundEmail.created_at,func.substr(TicketInboundEmail.content,1,180).label('content')).where(TicketInboundEmail.ticket_id.in_(ticket_ids)).distinct(TicketInboundEmail.ticket_id).order_by(TicketInboundEmail.ticket_id,TicketInboundEmail.created_at.desc(),TicketInboundEmail.id.desc())
            for value in objects(self.db,query):self.ticket_stats.setdefault(value.ticket_id,{})['incoming']=value
            query=select(TicketEmail.ticket_id,TicketEmail.created_at,func.substr(TicketEmail.body_text,1,180).label('content')).where(TicketEmail.ticket_id.in_(ticket_ids),TicketEmail.recipient_type=='customer').distinct(TicketEmail.ticket_id).order_by(TicketEmail.ticket_id,TicketEmail.created_at.desc(),TicketEmail.id.desc())
            for value in objects(self.db,query):self.ticket_stats.setdefault(value.ticket_id,{})['sent']=value
            query=select(TicketEmail.ticket_id,TicketEmail.sent_at,TicketEmail.created_at).where(TicketEmail.ticket_id.in_(ticket_ids),TicketEmail.recipient_type=='customer',TicketEmail.status=='sent').distinct(TicketEmail.ticket_id).order_by(TicketEmail.ticket_id,TicketEmail.sent_at.desc(),TicketEmail.id.desc())
            for value in objects(self.db,query):self.ticket_stats.setdefault(value.ticket_id,{})['delivered']=value

    def labels(self,kind,row):
        label=self.label_rows.get((kind,row.id))
        direct=set(label.tag_ids) if label else {t['id'] for t in row.tags if t.get('id')} if kind=='tickets' else set()
        groups=set(label.group_ids)&self.groups if label else set()
        effective={key for key in self.tags if key in direct or self.tag_groups[key]&groups}
        return effective,groups

    def priority(self,row):
        policy=self.policies.get(row.space_id,{})
        if not row.person_id or not policy.get('priority_enabled'):return False
        tags,groups=self.labels('people',SimpleNamespace(id=row.person_id))
        effective_groups=set(groups)
        for tag in tags:effective_groups.update(self.tag_groups[tag])
        return bool(tags&set(policy.get('tag_ids',[])) or effective_groups&set(policy.get('group_ids',[])))

    def channels(self,row):
        from app.people.service import CHANNELS
        state=row.workflow_state;value=str(state.get('channel','webchat')).lower();provider=str(state.get('channel_provider','')).lower()
        values={('whatsapp' if provider=='whatsapp' or value in {'whatsapp','whatsapp_business'} else 'webchat' if value in {'webchat','tester'} else value)}&set(CHANNELS)
        if row.id in self.handoffs:values.add('ai_agent_handoff')
        main='tester' if state.get('channel')=='tester' else 'facebook' if state.get('channel')=='facebook' else next((v for v in ['whatsapp','webchat','messaging','bitrix24','africas_talking'] if v in values),'webchat')
        return main,sorted(values)

    def item(self,kind,row):
        from app.inbox.workspace import chat_status
        chat=kind=='chats';state=row.workflow_state if chat else {}
        assigned=row.live_assigned_user_id if chat else row.assignee_id if row.assignee_type=='user' else None
        status=chat_status(row) if chat else 'pending' if row.status=='in_progress' else row.status
        if chat:
            evidence=self.chat_stats.get(row.id,{});last=evidence.get('last');customer=evidence.get('customer');replied=evidence.get('replied')
            latest=last.created_at if last else row.created_at;preview=last.content if last else ''
            waiting=customer.created_at if customer and (not replied or customer.sequence>replied.sequence) else None
            channel,channels=self.channels(row)
        else:
            evidence=self.ticket_stats.get(row.id,{});incoming=evidence.get('incoming');sent=evidence.get('sent');delivered=evidence.get('delivered')
            latest=max([row.created_at,*([incoming.created_at] if incoming else []),*([sent.created_at] if sent else [])])
            preview=incoming.content if incoming and (not sent or incoming.created_at>sent.created_at) else sent.content if sent else row.description
            waiting=incoming.created_at if incoming and (not delivered or incoming.created_at>(delivered.sent_at or delivered.created_at)) else None
            channel='email';channels=['email']
        return {'key':kind+':'+str(row.id),'kind':kind,'id':str(row.id),'name':'','person_id':None,
            'subject':row.subject if not chat else '','description':'','agent_name':row.agent_name,'status':status,
            'runtime_status':row.live_status if chat else row.status,'teamspace_id':str(row.space_id),
            'assigned_user_id':str(assigned) if assigned else None,'revision':row.live_revision if chat else row.revision,
            'created_at':row.created_at,'updated_at':row.live_updated_at if chat else row.updated_at,'latest_at':latest,'preview':preview[:180],'wait_since':waiting,
            'handoff_at':row.live_context.get('handoff_at') if chat else None,'priority':self.priority(row) if chat else False,
            'channel':channel,'channels':channels,'channel_name':state.get('webchat_name') or state.get('whatsapp_name',''),
            'channel_icon_url':'/api/workspaces/'+str(self.workspace_id)+'/support/chats/'+str(row.id)+'/channel-icon' if chat and (state.get('webchat_icon') or state.get('whatsapp_icon')) else None,
            'language':state.get('language','en') if chat else 'en','mine':assigned==self.user_id,
            'read_only':chat and status in {'ai','external','closed','resolved'}}

    def matches(self,kind,row,item,filters):
        if filters.kind!='all' and filters.kind!=kind or filters.statuses and item['status'] not in filters.statuses or filters.priority and not item['priority']:return False
        if filters.agent_id and str(filters.agent_id)!=item['assigned_user_id']:return False
        teams=row.live_context.get('routing',{}).get('team_ids',[]) if kind=='chats' else list(dict.fromkeys((row.assigned_team_ids or [])+([str(row.assignee_id)] if row.assignee_type=='team' and row.assignee_id else [])))
        if filters.team_id and str(filters.team_id) not in teams:return False
        if filters.assigned=='me' and not item['mine'] or filters.assigned=='unassigned' and item['assigned_user_id'] or filters.assigned=='teams' and not set(teams)&{str(t.id) for t in self.scope.teams}:return False
        if filters.language and filters.language!=item['language'] or filters.channel and filters.channel not in {item['channel'],*item['channels']}:return False
        if filters.tag_id or filters.group_id:
            tags,groups=self.labels(kind,row)
            if filters.tag_id and str(filters.tag_id) not in tags or filters.group_id and str(filters.group_id) not in groups:return False
        return True

    def decorate(self,page):
        from app.people.service import visibility
        people={row.person_id for _,row,_ in page if row.person_id}
        if people:
            query=select(SupportPerson.id,SupportPerson.name,func.coalesce(SupportPerson.teamspace_id,self.scope.default).label('space_id')).where(SupportPerson.id.in_(people),visibility(self.db,self.workspace_id,self.user_id))
            self.people={v.id:v for v in objects(self.db,query)}
        tickets=[row.id for kind,row,_ in page if kind=='tickets']
        if tickets:
            from app.tickets.service import access_filter
            self.manage_tickets=set(self.db.scalars(select(Ticket.id).where(Ticket.id.in_(tickets),access_filter(self.db,self.workspace_id,self.user_id,self.scope.legacy,'manage'))))
        descriptions={}
        for kind,model,column in [('chats',Chat,Chat.live_description),('tickets',Ticket,Ticket.description)]:
            ids=[row.id for k,row,_ in page if k==kind]
            if ids:descriptions.update(((kind,k),v) for k,v in self.db.execute(select(model.id,column).where(model.id.in_(ids),model.workspace_id==self.workspace_id)))
        result=[]
        for kind,row,item in page:
            person=self.people.get(row.person_id)
            item['person_id']=str(person.id) if person else None
            item['name']=(person.name if person and self.scope.allowed('view_people_name',person.space_id) else '') or (row.full_name if kind=='tickets' else '') or ('Unknown person' if kind=='tickets' else row.agent_name)
            manage=self.scope.chat_allowed(row,'manage') if kind=='chats' else row.id in self.manage_tickets
            item.update(description=descriptions.get((kind,row.id),''),can_manage=manage,can_transfer=bool(manage and self.scope.allowed('transfer_livechats' if kind=='chats' else 'transfer_tickets',row.space_id)))
            result.append(item)
        return result
