from copy import deepcopy
from dataclasses import replace
import json
import uuid
from fastapi import HTTPException
from email_validator import validate_email,EmailNotValidError
from sqlalchemy import select,or_
from sqlalchemy.dialects.postgresql import insert
from app import agent_service
from app.core.security import utcnow
from app.models import SupportTicket,TicketCounter,TicketNote,TicketEmail,TicketTag,TicketTagGroup,TicketEmailDomain,TicketEmailTemplate,ConversationField,SupportTeam,User,Membership,AgentConversation
from app.custom_fields import service as fields
from app.custom_fields.schemas import FieldCreate
from app.tickets import settings
from app.tickets.html import sanitize
from app.workflows.chat_script import ScriptError
from app.workflows.templates import render,TOKEN


def email(value):
    if any(ord(c)<32 or ord(c)==127 for c in value):raise ScriptError('Email addresses cannot contain control characters.')
    try:return validate_email(value,check_deliverability=False,allow_smtputf8=False).ascii_email
    except EmailNotValidError:raise ScriptError('The rendered customer email address is invalid.') from None


def assignee(db,workspace_id,kind,identifier):
    if kind=='none':
        if identifier is not None:raise ScriptError('Choose Unassigned without an assignee ID.')
        return 'Unassigned',[]
    if not identifier:raise ScriptError('Choose an assignee.')
    if kind=='team':
        row=db.scalar(select(SupportTeam).where(SupportTeam.id==uuid.UUID(str(identifier)),SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))
        if not row:raise ScriptError('The selected support team is no longer available.')
        ids=[uuid.UUID(value) for value in row.member_ids]
        users=db.scalars(select(User).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id,User.id.in_(ids)).order_by(User.id)).all() if ids else []
        return row.name,[user.email for user in users]
    row=db.scalar(select(User).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id,User.id==uuid.UUID(str(identifier))))
    if not row:raise ScriptError('Choose a current workspace member as the assignee.')
    return row.full_name or row.email,[row.email]


def checked_field(db,workspace_id,identifier,value):
    row=db.scalar(select(ConversationField).where(ConversationField.id==uuid.UUID(str(identifier)),ConversationField.workspace_id==workspace_id,ConversationField.archived.is_(False)))
    if not row:raise ScriptError('Choose an active conversation custom field.')
    try:FieldCreate.model_validate({'scope':'conversation','type':row.type,'name':row.name,'description':row.description,'options':row.options,'default_value':value})
    except ValueError:raise ScriptError('The value for custom field “{}” does not match its type or configured options.'.format(row.name)) from None
    return row


def override_value(value,context):
    if not isinstance(value,str):return value
    match=TOKEN.fullmatch(value)
    if match:
        from app.workflows.templates import resolve
        return deepcopy(resolve(match.group(1) or match.group(2),context))
    return render(value,context,4000,allow_empty=True)


def validate_references(db,workspace_id,nodes):
    from app.workflows.numbering import walk
    for node in walk(nodes):
        if node['kind']!='action' or node['type']!='create_ticket':continue
        config=node['config']
        try:
            assignee(db,workspace_id,config['assignee_type'],config['assignee_id'])
            for kind,key in [('tags','tag_ids'),('groups','tag_group_ids')]:
                ids={str(row.id) for row in settings.active(db,workspace_id,kind)}
                if not set(config[key])<=ids:raise ScriptError('Choose active tags and groups from this workspace.')
            if config['email_domain_id']:
                row=db.scalar(select(TicketEmailDomain).where(TicketEmailDomain.id==uuid.UUID(config['email_domain_id']),TicketEmailDomain.workspace_id==workspace_id))
                if not settings.valid_sender(row):raise ScriptError('Choose an enabled email domain with current DNS verification.')
            for key in ['customer_template_id','assignee_template_id']:
                if config[key]:
                    row=db.scalar(select(TicketEmailTemplate).where(TicketEmailTemplate.id==uuid.UUID(config[key]),TicketEmailTemplate.workspace_id==workspace_id,TicketEmailTemplate.archived.is_(False)))
                    if not row:raise ScriptError('Choose an active email template from this workspace.')
            for item in config['custom_fields']:
                if isinstance(item['value'],str) and TOKEN.search(item['value']):
                    row=db.scalar(select(ConversationField.id).where(ConversationField.id==uuid.UUID(item['field_id']),ConversationField.workspace_id==workspace_id,ConversationField.archived.is_(False)))
                    if not row:raise ScriptError('Choose an active conversation custom field.')
                else:checked_field(db,workspace_id,item['field_id'],item['value'])
        except ScriptError as error:raise HTTPException(422,"Step '{}': {}".format(node['name'],error)) from None


def summary(ticket):
    return {'id':str(ticket.id),'reference':ticket.reference,'subject':ticket.subject,'status':ticket.status,'assignee_name':ticket.assignee_name}


class TicketService:
    def __init__(self,db,workspace_id,actor_id,conversation,execution_id):
        self.db=db;self.workspace_id=workspace_id;self.actor_id=actor_id;self.conversation=conversation;self.execution_id=execution_id;self.calls=0

    def create(self,node,context):
        db=self.db;config=node['config'];self.calls+=1
        if self.calls>5:raise ScriptError('An event can create at most five tickets.')
        prior=db.scalar(select(SupportTicket).where(SupportTicket.conversation_id==self.conversation.id,SupportTicket.action_node_id==uuid.UUID(node['id']),SupportTicket.execution_id==self.execution_id))
        if prior:return summary(prior)
        single={key:render(config[key],context,maximum,allow_empty=(key=='phone')) for key,maximum in [('subject',200),('full_name',200),('email',320),('phone',50)]}
        for value in single.values():
            if any(ord(c)<32 or ord(c)==127 for c in value):raise ScriptError('Rendered single-line ticket fields cannot contain control characters.')
        single['email']=email(single['email']);description=render(config['description'],context,16000,allow_empty=True)
        name,recipients=assignee(db,self.workspace_id,config['assignee_type'],config['assignee_id'])
        if len(recipients)>50:raise ScriptError('Initial notifications support up to 50 assignee recipients. Choose a smaller support team.')
        copied=deepcopy(context.state.get('custom_fields',self.conversation.custom_fields or {}))
        for item in config['custom_fields']:
            value=override_value(item['value'],context);definition=checked_field(db,self.workspace_id,item['field_id'],value)
            copied[definition.key]=fields.option_snapshot(definition,value,'create_ticket')
        if len(json.dumps(copied,ensure_ascii=False,allow_nan=False).encode())>131072:raise ScriptError('Ticket custom fields exceed 128 KiB.')
        tags=settings.active(db,self.workspace_id,'tags');groups={str(g.id):g for g in settings.active(db,self.workspace_id,'groups')}
        chosen=set(config['tag_ids']);wanted_groups=set(config['tag_group_ids'])
        if not chosen<={str(t.id) for t in tags} or not wanted_groups<=set(groups):raise ScriptError('A configured ticket tag or group is no longer available.')
        tags=[t for t in tags if str(t.id) in chosen or bool(set(__import__('app.teamspace.records',fromlist=['tag_group_ids']).tag_group_ids(db,t))&wanted_groups)]
        if len(tags)>100:raise ScriptError('Apply at most 100 tags per ticket, including expanded groups.')
        snapshots=[{'id':str(t.id),'name':t.name,'color':t.color,'group':groups[str(t.group_id)].name if t.group_id and str(t.group_id) in groups else None} for t in tags]
        db.execute(insert(TicketCounter).values(workspace_id=self.workspace_id,next_number=1).on_conflict_do_nothing(index_elements=[TicketCounter.workspace_id]))
        counter=db.scalar(select(TicketCounter).where(TicketCounter.workspace_id==self.workspace_id).with_for_update())
        number=counter.next_number;counter.next_number+=1
        ticket=SupportTicket(id=uuid.uuid4(),workspace_id=self.workspace_id,conversation_id=self.conversation.id,creator_id=self.actor_id,action_node_id=uuid.UUID(node['id']),execution_id=self.execution_id,
            number=number,reference='PTX-{:06d}'.format(number),agent_name=context.agent.name,description=description,assignee_type=config['assignee_type'],assignee_id=uuid.UUID(config['assignee_id']) if config['assignee_id'] else None,
            assignee_name=name,custom_fields=copied,tags=snapshots,**single)
        ticket.person_id=self.conversation.person_id
        db.add(ticket);db.flush()
        from app.models import RecordLabels
        db.add(RecordLabels(workspace_id=self.workspace_id,kind="tickets",record_id=ticket.id,tag_ids=config["tag_ids"],group_ids=config["tag_group_ids"]))
        db.add(TicketNote(ticket_id=ticket.id,author_id=self.actor_id,author_name=context.agent.name,kind='event',content='Ticket created by the AI workflow.'))
        if config['assignee_template_id'] and not recipients:
            db.add(TicketNote(ticket_id=ticket.id,author_id=self.actor_id,author_name=context.agent.name,kind='event',content='The initial assignee email was skipped because the selected team has no current members.'))
        self.queue_emails(ticket,config,context,recipients)
        return summary(ticket)

    def queue_emails(self,ticket,config,context,recipients):
        if not(config['customer_template_id'] or config['assignee_template_id']):return
        domain=self.db.scalar(select(TicketEmailDomain).where(TicketEmailDomain.id==uuid.UUID(config['email_domain_id']),TicketEmailDomain.workspace_id==self.workspace_id))
        if not domain:raise ScriptError('The configured email sender is no longer available.')
        template_context=replace(context,state={**context.state,'variables':{**context.variables,'ticket':{**summary(ticket),'description':ticket.description,'full_name':ticket.full_name,'email':ticket.email,'phone':ticket.phone,'custom_fields':ticket.custom_fields,'tags':ticket.tags}}})
        for kind,key,emails in [('customer','customer_template_id',[ticket.email]),('assignee','assignee_template_id',recipients)]:
            if not config[key]:continue
            template=self.db.scalar(select(TicketEmailTemplate).where(TicketEmailTemplate.id==uuid.UUID(config[key]),TicketEmailTemplate.workspace_id==self.workspace_id,TicketEmailTemplate.archived.is_(False)))
            if not template:raise ScriptError('The selected notification template is unavailable.')
            html,plain=sanitize(render(template.content,template_context,131072,escape_values=True))
            if not plain:raise ScriptError('The rendered email template is empty.')
            for recipient in dict.fromkeys(emails):
                identifier=uuid.uuid4()
                self.db.add(TicketEmail(id=identifier,ticket_id=ticket.id,domain_id=domain.id,recipient_type=kind,recipient=email(recipient),sender=domain.local_part+'@'+domain.domain,sender_name=domain.from_name,
                    subject='[{}] {}'.format(ticket.reference,ticket.subject),body_html=html,body_text=plain,message_id='<ticket-{}@{}>'.format(identifier.hex,domain.domain)))


def access_filter(db,workspace_id,user_id,manage,action='view'):
    from app.workspace_settings import permissions as grants
    if grants.has_custom(db,workspace_id,user_id):
        scopes,teams=grants.record_scopes(db,workspace_id,user_id,'tickets',action)
        from sqlalchemy import false
        scope=false()
        if 'all' in scopes:scope=SupportTicket.workspace_id==workspace_id
        else:
            if 'own' in scopes:scope=scope|((SupportTicket.assignee_type=='user')&(SupportTicket.assignee_id==user_id))
            if 'teams' in scopes:scope=scope|((SupportTicket.assignee_type=='team')&SupportTicket.assignee_id.in_([uuid.UUID(t) for t in teams]))|__import__('sqlalchemy').cast(SupportTicket.audience_team_ids,__import__('sqlalchemy.dialects.postgresql',fromlist=['JSONB']).JSONB).op('?|')(__import__('sqlalchemy.dialects.postgresql',fromlist=['array']).array(list(teams),type_=__import__('sqlalchemy').String))
        spaces=grants.accessible_spaces(db,workspace_id,user_id)
        from app.models import Agent
        conversations=select(AgentConversation.id).join(Agent,Agent.id==AgentConversation.agent_id).where(Agent.teamspace_id.in_([uuid.UUID(t) for t in spaces]))
        for team_id,team_scopes,team_spaces in grants.team_record_grants(db,workspace_id,user_id,'tickets',action):
            team_conversations=select(AgentConversation.id).join(Agent,Agent.id==AgentConversation.agent_id).where(Agent.teamspace_id.in_([uuid.UUID(t) for t in team_spaces]))
            team_scope=false()
            if team_scopes&{'all','teams'}:team_scope=team_scope|(((SupportTicket.assignee_type=='team')&(SupportTicket.assignee_id==uuid.UUID(team_id)))|__import__('sqlalchemy').cast(SupportTicket.audience_team_ids,__import__('sqlalchemy.dialects.postgresql',fromlist=['JSONB']).JSONB).op('?')(team_id))
            if 'own' in team_scopes:team_scope=team_scope|((SupportTicket.assignee_type=='user')&(SupportTicket.assignee_id==user_id))
            scope=scope|(team_scope&SupportTicket.conversation_id.in_(team_conversations))
        member=grants.membership(db,workspace_id,user_id)
        orphan=SupportTicket.conversation_id.is_(None) if member and member.teamspace_ids is None else false()
        return (SupportTicket.workspace_id==workspace_id)&scope&((SupportTicket.conversation_id.in_(conversations))|orphan)

    if manage:return SupportTicket.workspace_id==workspace_id
    teams=[team.id for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False))) if str(user_id) in team.member_ids]
    return (SupportTicket.workspace_id==workspace_id)&or_(SupportTicket.creator_id==user_id,(SupportTicket.assignee_type=='user')&(SupportTicket.assignee_id==user_id),(SupportTicket.assignee_type=='team')&(SupportTicket.assignee_id.in_(teams)),__import__('sqlalchemy').cast(SupportTicket.audience_team_ids,__import__('sqlalchemy.dialects.postgresql',fromlist=['JSONB']).JSONB).op('?|')(__import__('sqlalchemy.dialects.postgresql',fromlist=['array']).array([str(t) for t in teams],type_=__import__('sqlalchemy').String)))


def owned(db,workspace_id,user_id,manage,identifier,lock=False,action='view'):
    query=select(SupportTicket).where(SupportTicket.id==identifier,access_filter(db,workspace_id,user_id,manage,action))
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'Ticket not found.')
    return row


def payload(ticket,brief=False):
    excluded={'creator_id','execution_id','action_node_id','number'}
    if brief:excluded.update({'description','custom_fields','tags'})
    return {column.name:getattr(ticket,column.name) for column in ticket.__table__.columns if column.name not in excluded}
