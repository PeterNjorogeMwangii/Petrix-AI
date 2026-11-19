"""Permission-scoped CRM profiles, customer history and private staff notes."""
from copy import deepcopy
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import select,or_,and_,false,func,cast,delete
from sqlalchemy.dialects.postgresql import JSONB
from app.core.security import utcnow
from app.models import (SupportPerson,PersonNote,SupportTicket,TicketEmail,TicketInboundEmail,Agent,AgentConversation,
    AIInboundSession,AIOutgoingSession,SupportTeam,WorkspaceRole,Teamspace,Workspace,User,RecordLabels,SupportJob)
from app.teamspace import records as r
from app.workspace_settings import permissions as p

CHANNELS={'ticket':'Ticket','webchat':'Webchat','whatsapp':'WhatsApp','ai_agent_handoff':'AI agent handoff',
    'messaging':'Messaging','bitrix24':'Bitrix24','africas_talking':'SMS'}
FIELDS={'name':'name','email':'email','phone':'phone','location':'location','country':'location','external_id':'unique_id'}


def visibility(db,workspace_id,user_id,manage=False):
    key='manage_people_all' if manage else 'view_people_all'
    person=SupportPerson;space=func.coalesce(person.teamspace_id,p.default_space(db,workspace_id))
    accessible=p.accessible_spaces(db,workspace_id,user_id);rules=[]
    if key in p.workspace_grants(db,workspace_id,user_id):rules.append(space.in_([uuid.UUID(i) for i in accessible]))
    for team in p.teams_for(db,workspace_id,user_id):
        role=db.get(WorkspaceRole,team.role_id) if team.role_id else None
        if not role or key not in role.permissions:continue
        spaces=(set(team.teamspace_ids) if team.teamspace_ids is not None else {str(p.default_space(db,workspace_id))})&accessible
        if not spaces:continue
        ids=[uuid.UUID(i) for i in spaces]
        chats=select(AgentConversation.id).outerjoin(Agent,Agent.id==AgentConversation.agent_id).where(AgentConversation.workspace_id==workspace_id,
            AgentConversation.person_id==person.id,func.coalesce(Agent.teamspace_id,p.default_space(db,workspace_id)).in_(ids),
            cast(AgentConversation.live_context['routing']['team_ids'],JSONB).contains([str(team.id)])).correlate(person).exists()
        tickets=select(SupportTicket.id).where(SupportTicket.workspace_id==workspace_id,SupportTicket.person_id==person.id,
            func.coalesce(SupportTicket.teamspace_id,p.default_space(db,workspace_id)).in_(ids),or_(
                (SupportTicket.assignee_type=='team')&(SupportTicket.assignee_id==team.id),
                cast(SupportTicket.assigned_team_ids,JSONB).contains([str(team.id)]),cast(SupportTicket.audience_team_ids,JSONB).contains([str(team.id)]))).correlate(person).exists()
        rules.append(space.in_(ids)&or_(person.team_id==team.id,chats,tickets))
    return (person.workspace_id==workspace_id)&(or_(*rules) if rules else false())


def require_list(db,workspace_id,user_id):
    if not any(p.allowed(db,workspace_id,'view_people_all',user_id,teamspace_id=i) for i in p.accessible_spaces(db,workspace_id,user_id)):
        raise HTTPException(403,'Your role cannot view People.')


def get(db,workspace_id,identifier,user_id,manage=False,lock=False):
    query=select(SupportPerson).where(SupportPerson.id==identifier,visibility(db,workspace_id,user_id),visibility(db,workspace_id,user_id,True) if manage else True)
    if lock:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'Person not found or your role does not permit this operation.')
    return row


def visible_chats(db,row,user_id):
    values=[]
    for chat in db.scalars(select(AgentConversation).where(AgentConversation.workspace_id==row.workspace_id,AgentConversation.person_id==row.id)):
        try:r.record(db,row.workspace_id,'chats',chat.id,user_id)
        except HTTPException:continue
        values.append(chat)
    return values


def visible_tickets(db,row,user_id):
    from app.tickets.service import access_filter
    from app.tickets.workspace import legacy
    return list(db.scalars(select(SupportTicket).where(SupportTicket.person_id==row.id,access_filter(db,row.workspace_id,user_id,legacy(db,row.workspace_id,user_id)))))


def chat_channels(db,chat):
    value=str((chat.workflow_state or {}).get('channel','webchat')).lower()
    provider=str((chat.workflow_state or {}).get('channel_provider','')).lower()
    result={('whatsapp' if provider=='whatsapp' or value in {'whatsapp','whatsapp_business'} else 'webchat' if value in {'webchat','tester'} else value)}&set(CHANNELS)
    if db.scalar(select(AIInboundSession.id).where(AIInboundSession.conversation_id==chat.id)) or db.get(AIOutgoingSession,chat.id):result.add('ai_agent_handoff')
    return result


def channels(db,row,user_id):
    values=set()
    for chat in visible_chats(db,row,user_id):values.update(chat_channels(db,chat))
    if visible_tickets(db,row,user_id):values.add('ticket')
    return [key for key in CHANNELS if key in values]


def label_details(db,row,user_id):
    space=r.record_space(db,'people',row)
    if not p.allowed(db,row.workspace_id,'view_people_tags',user_id,teamspace_id=space):return None
    value=r.label_payload(db,row.workspace_id,'people',row);ids=set(g['id'] for g in value['groups'])
    from app.models import TicketTag,TicketTagGroup
    for tag in db.scalars(select(TicketTag).where(TicketTag.workspace_id==row.workspace_id,TicketTag.id.in_([uuid.UUID(t['id']) for t in value['effective_tags']]))):ids.update(r.tag_group_ids(db,tag))
    groups=[{'id':str(g.id),'name':g.name} for g in db.scalars(select(TicketTagGroup).where(TicketTagGroup.workspace_id==row.workspace_id,TicketTagGroup.id.in_([uuid.UUID(i) for i in ids]),TicketTagGroup.archived.is_(False)).order_by(TicketTagGroup.name))]
    return {**value,'effective_groups':groups}


def summary(db,row,user_id,with_channels=True):
    value=r.person_payload(db,row,user_id);space=r.record_space(db,'people',row)
    if value.get('country'):value['country']=value['country'].upper()
    label=label_details(db,row,user_id)
    value.update({'teamspace_id':str(space),'tags':label['effective_tags'] if label else None,'groups':label['effective_groups'] if label else None,
        'channels':[{'key':key,'name':CHANNELS[key]} for key in channels(db,row,user_id)] if with_channels else [],
        'can_delete':bool(value['can_manage'] and (not r.is_banned(row) or p.allowed(db,row.workspace_id,'manage_bans',user_id,teamspace_id=space))),
        'can_tags':bool(value['can_manage'] and p.allowed(db,row.workspace_id,'manage_people_tags',user_id,teamspace_id=space)),
        'can_export':p.allowed(db,row.workspace_id,'export_people',user_id,teamspace_id=space),
        'field_permissions':{key:bool(value['can_manage'] and p.allowed(db,row.workspace_id,'manage_people_'+permission,user_id,teamspace_id=space)) for key,permission in FIELDS.items()}})
    return value


def options(db,workspace_id,user_id):
    import pycountry
    from app.tickets.settings import active,payload
    from app.custom_fields.service import payload as field_payload
    require_list(db,workspace_id,user_id);spaces=p.accessible_spaces(db,workspace_id,user_id)
    allowed=lambda key:any(p.allowed(db,workspace_id,key,user_id,teamspace_id=i) for i in spaces)
    fields=r.field_definitions(db,workspace_id,'person') if allowed('view_people_custom_fields') else []
    manage_spaces=[t for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace_id).order_by(Teamspace.is_default.desc(),Teamspace.name)) if str(t.id) in spaces and p.allowed(db,workspace_id,'manage_people_all',user_id,teamspace_id=t.id)]
    return {'tags':[payload(t) for t in active(db,workspace_id,'tags')] if allowed('view_people_tags') else [],
        'groups':[payload(g) for g in active(db,workspace_id,'groups')] if allowed('view_people_tags') else [],
        'fields':[field_payload(f) for f in fields],'countries':sorted([{'code':c.alpha_2,'name':c.name} for c in pycountry.countries],key=lambda c:c['name']) if allowed('view_people_location') else [],
        'channels':[{'key':key,'name':name} for key,name in CHANNELS.items()],
        'can_create':bool(manage_spaces),'teamspaces':[{'id':str(t.id),'name':t.name,'fields':{key:p.allowed(db,workspace_id,'manage_people_'+permission,user_id,teamspace_id=t.id) for key,permission in FIELDS.items()}} for t in manage_spaces]}


def listing(db,workspace_id,user_id,filters,offset,limit):
    require_list(db,workspace_id,user_id)
    query=select(SupportPerson).where(visibility(db,workspace_id,user_id))
    from app.livechats.service import date_bounds
    start,end=date_bounds(filters['period'],filters['created_from'],filters['created_to'],filters['timezone'])
    if start:query=query.where(SupportPerson.created_at>=start)
    if end:query=query.where(SupportPerson.created_at<end)
    if filters['country']:
        import pycountry
        code=filters['country'].upper()
        if not pycountry.countries.get(alpha_2=code):raise HTTPException(422,'Choose a valid country code.')
        query=query.where(func.upper(SupportPerson.country)==code)
    definition=next((f for f in r.field_definitions(db,workspace_id,'person') if f.key==filters['custom_key']),None)
    if filters['custom_key'] and not definition:raise HTTPException(422,'Choose an active People custom field.')
    values=[];total=0
    for row in db.scalars(query.order_by(SupportPerson.created_at.desc(),SupportPerson.id.desc()).execution_options(yield_per=100)):
        value=r.person_payload(db,row,user_id)
        if filters['country'] and 'country' not in value:continue
        if filters['q'] and filters['q'].casefold() not in ' '.join(str(value.get(k,'')) for k in ['id','name','email','phone','external_id']).casefold():continue
        if definition:
            if 'custom_fields' not in value:continue
            actual=(row.custom_fields.get(definition.key) or {}).get('value',definition.default_value)
            if (str(actual).lower() if isinstance(actual,bool) else str(actual) if actual is not None else '')!=filters['custom_value']:continue
        if filters['tag'] or filters['group']:
            label=label_details(db,row,user_id)
            if label is None:continue
            if filters['tag'] and str(filters['tag']) not in {t['id'] for t in label['effective_tags']}:continue
            if filters['group'] and str(filters['group']) not in {g['id'] for g in label['effective_groups']}:continue
        if filters['channel'] and filters['channel'] not in channels(db,row,user_id):continue
        if offset<=total<offset+limit:values.append(summary(db,row,user_id))
        total+=1
    return {'people':values,'total':total,'offset':offset,'limit':limit,'has_more':offset+limit<total}


def register(db,workspace_id,user_id,body):
    space=body.teamspace_id or p.default_space(db,workspace_id)
    p.require(db,workspace_id,'manage_people_all',user_id,teamspace_id=space)
    if not any(str(getattr(body,k) or '').strip() for k in ['name','email','phone','external_id']):raise HTTPException(422,'Enter a name, email address or phone number.')
    for key in body.model_fields_set&set(FIELDS):p.require(db,workspace_id,'manage_people_'+FIELDS[key],user_id,teamspace_id=space)
    db.scalar(select(Workspace).where(Workspace.id==workspace_id).with_for_update(key_share=True))
    digest=hashlib.sha256(json.dumps({'actor':str(user_id),**body.model_dump(mode='json',exclude={'expected_revision'})},sort_keys=True).encode()).hexdigest()
    if body.client_request_id:
        prior=db.scalar(select(SupportPerson).where(SupportPerson.workspace_id==workspace_id,SupportPerson.client_request_id==body.client_request_id))
        if prior:
            if prior.create_payload_hash!=digest:raise HTTPException(409,'This registration request ID was already used with different details.')
            return summary(db,get(db,workspace_id,prior.id,user_id),user_id)
    if body.external_id and db.scalar(select(SupportPerson.id).where(SupportPerson.workspace_id==workspace_id,SupportPerson.external_id==body.external_id)):raise HTTPException(409,'This external ID is already used.')
    identifier=uuid.uuid4();values=body.model_dump(exclude={'expected_revision','teamspace_id','client_request_id'})
    row=SupportPerson(id=identifier,workspace_id=workspace_id,teamspace_id=space,identity_key='manual:'+str(identifier),client_request_id=body.client_request_id,create_payload_hash=digest,**values)
    db.add(row);db.commit();return summary(db,row,user_id)


def update(db,workspace_id,identifier,user_id,body):
    from app.teamspace.configuration import checked_revision
    if body.model_fields_set&{'client_request_id','teamspace_id'}:raise HTTPException(422,'Registration ID and teamspace cannot be changed here.')
    row=get(db,workspace_id,identifier,user_id,True,True);checked_revision(row,body.expected_revision)
    if body.external_id and db.scalar(select(SupportPerson.id).where(SupportPerson.workspace_id==workspace_id,SupportPerson.external_id==body.external_id,SupportPerson.id!=identifier)):raise HTTPException(409,'This external ID is already used.')
    for key,value in body.model_dump(exclude={'expected_revision'},exclude_unset=True).items():
        p.require(db,workspace_id,'manage_people_'+FIELDS[key],user_id,teamspace_id=r.record_space(db,'people',row));setattr(row,key,value)
    row.revision+=1;row.updated_at=utcnow();db.commit();return summary(db,row,user_id)


def note_payload(note):
    return {key:getattr(note,key) for key in ['id','author_id','author_name','content','color','revision','created_at','updated_at']}


def detail(db,row,user_id):
    notes=list(db.scalars(select(PersonNote).where(PersonNote.person_id==row.id).order_by(PersonNote.created_at.desc(),PersonNote.id.desc())))
    return {'person':summary(db,row,user_id),'notes':[note_payload(n) for n in notes]}


def save_note(db,row,user_id,body,identifier=None):
    if identifier:
        note=db.scalar(select(PersonNote).where(PersonNote.person_id==row.id,PersonNote.id==identifier).with_for_update().execution_options(populate_existing=True))
        if not note:raise HTTPException(404,'Internal note not found.')
        if note.revision!=body.expected_revision:raise HTTPException(409,'This note changed. Reload before editing.')
        note.content=body.content;note.color=body.color;note.revision+=1;note.updated_at=utcnow()
    else:
        if db.scalar(select(func.count()).select_from(PersonNote).where(PersonNote.person_id==row.id))>=100:raise HTTPException(409,'A person supports up to 100 internal notes.')
        user=db.get(User,user_id);db.add(PersonNote(person_id=row.id,author_id=user_id,author_name=user.full_name or user.email,content=body.content,color=body.color))
    row.revision+=1;row.updated_at=utcnow();db.commit();return detail(db,row,user_id)


def history(db,row,user_id,offset,limit):
    items=[]
    for chat in visible_chats(db,row,user_id):
        keys=chat_channels(db,chat)
        items.append({'id':str(chat.id),'kind':'chats','name':chat.agent_name or 'Conversation','created_at':chat.created_at,
            'status':chat.live_status if chat.live_status!='none' else 'closed' if (chat.workflow_state or {}).get('closed') else 'open',
            'channels':[{'key':key,'name':CHANNELS[key]} for key in CHANNELS if key in keys]})
    for ticket in visible_tickets(db,row,user_id):
        items.append({'id':str(ticket.id),'kind':'tickets','name':ticket.reference+' · '+ticket.subject,'created_at':ticket.created_at,
            'status':'pending' if ticket.status=='in_progress' else ticket.status,'channels':[{'key':'ticket','name':'Ticket'}]})
    items.sort(key=lambda item:(item['created_at'],item['id']),reverse=True)
    return {'items':items[offset:offset+limit],'total':len(items),'offset':offset,'limit':limit}


def transcript(db,person,user_id,kind,identifier,before=None):
    row=r.record(db,person.workspace_id,kind,identifier,user_id)
    if row.person_id!=person.id:raise HTTPException(404,'Conversation not found for this person.')
    if kind=='chats':
        from app.inbox.service import messages
        return messages(db,row,before)
    # Customer email history excludes unsent drafts, assignee alerts and private notes.
    outgoing=list(db.scalars(select(TicketEmail).where(TicketEmail.ticket_id==row.id,TicketEmail.status=='sent',TicketEmail.recipient_type=='customer')))
    incoming=list(db.scalars(select(TicketInboundEmail).where(TicketInboundEmail.ticket_id==row.id)))
    values=[{'id':str(e.id),'role':'assistant','content':e.body_text,'created_at':e.sent_at or e.created_at,'author_name':(e.persona or {}).get('name') or e.sender_name,'source':'email','cc':e.cc_recipients,'attachments':[]} for e in outgoing]
    values += [{'id':str(e.id),'role':'user','content':e.content,'created_at':e.created_at,'author_name':row.full_name or e.sender,'source':'email','attachments':[]} for e in incoming]
    values.sort(key=lambda item:(item['created_at'],item['id']))
    if before:
        index=next((i for i,v in enumerate(values) if v['id']==str(before)),None)
        if index is None:raise HTTPException(404,'The earlier message was not found in this conversation.')
        values=values[:index]
    return {'messages':values[-50:],'has_more':len(values)>50}


def remove(db,workspace_id,user_id,body):
    from app.teamspace.configuration import checked_revision
    rows=[get(db,workspace_id,i,user_id,True,True) for i in sorted(body.ids)]
    for row in rows:
        checked_revision(row,body.revisions[row.id])
        if r.is_banned(row):p.require(db,workspace_id,'manage_bans',user_id,teamspace_id=r.record_space(db,'people',row))
    targets=set(map(str,body.ids))
    for job in db.scalars(select(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.target_kind=='people').with_for_update()):
        if str(job.target_id) in targets or targets&set(job.payload.get('ids',[])):
            job.status='failed';job.error='A source person was deleted.';job.file_data=None;job.output={};job.finished_at=utcnow();job.lease_id=None
    db.execute(delete(RecordLabels).where(RecordLabels.workspace_id==workspace_id,RecordLabels.kind=='people',RecordLabels.record_id.in_(body.ids)))
    for row in rows:db.delete(row)
    db.commit();return {'deleted':len(rows)}
