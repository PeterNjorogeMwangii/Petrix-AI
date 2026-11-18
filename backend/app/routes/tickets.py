from datetime import timedelta
import uuid
from typing import Literal,Optional
from fastapi import APIRouter,Depends,Request,HTTPException,Query,Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app import agent_service
from app.database import get_db
from app.core.security import utcnow
from app.models import SupportTicket,TicketEmail,TicketNote,User,RateLimit
from app.routes.agents import access
from app.tickets import service,settings,notifications
from app.tickets.schemas import GroupBody,GroupUpdate,TagBody,TagUpdate,DomainBody,DomainUpdate,TemplateBody,TemplateUpdate,Revision,TicketUpdate,NoteBody,EmailRetry

router=APIRouter(prefix='/api/workspaces/{workspace_id}',tags=['Tickets and email settings'])
Kind=Literal['groups','tags','email-domains','email-templates']


def actor(request,db,workspace_id,mutation=False,manage=False):
    if manage:return access(request,db,workspace_id,mutation=mutation)
    from app.auth_service import current_session,require_csrf
    session=current_session(request,db)
    if mutation:require_csrf(request,session)
    workspace,role=agent_service.workspace_for(db,workspace_id,session.user_id)
    return session,workspace,role,None


@router.get('/tickets/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    actor(request,db,workspace_id)
    from app.inbox.service import roster
    from app.teams.service import definitions,payload
    from app.custom_fields.service import definitions as field_definitions,payload as field_payload
    return {'users':roster(db,workspace_id),'teams':[payload(t) for t in definitions(db,workspace_id)],
        'tags':[settings.payload(t) for t in settings.active(db,workspace_id,'tags')],'groups':[settings.payload(t) for t in settings.active(db,workspace_id,'groups')],
        'email_domains':[{'id':str(d.id),'domain':d.domain,'from_email':d.local_part+'@'+d.domain,'verified':settings.valid_sender(d)} for d in settings.active(db,workspace_id,'email-domains')],
        'email_templates':[{'id':str(t.id),'name':t.name,'description':t.description} for t in settings.active(db,workspace_id,'email-templates')],
        'custom_fields':[field_payload(f) for f in field_definitions(db,workspace_id)]}


@router.get('/ticket-settings/{kind}')
def list_settings(workspace_id:uuid.UUID,kind:Kind,request:Request,db:Session=Depends(get_db)):
    _,_,role,_=access(request,db,workspace_id)
    from app.workspace_settings.permissions import allowed
    manage=allowed(db,workspace_id,'manage_tags' if kind in {'tags','groups'} else 'manage_email',teamspace_id=__import__('app.workspace_settings.permissions',fromlist=['default_space']).default_space(db,workspace_id))
    return {'can_manage':manage,'items':[settings.payload(row,request.app.state.settings) for row in settings.active(db,workspace_id,kind)],
        'groups':[settings.payload(row) for row in settings.active(db,workspace_id,'groups')] if kind=='tags' else []}


def setting_body(kind,body,update=False):
    models={'groups':GroupUpdate if update else GroupBody,'tags':TagUpdate if update else TagBody,'email-domains':DomainUpdate if update else DomainBody,'email-templates':TemplateUpdate if update else TemplateBody}
    from pydantic import ValidationError
    try:return models[kind].model_validate(body)
    except ValidationError as error:
        issue=error.errors(include_input=False,include_context=False,include_url=False)[0]
        raise HTTPException(422,'{}: {}'.format('.'.join(map(str,issue['loc'])),issue['msg'])) from None


@router.post('/ticket-settings/{kind}',status_code=201)
def create_setting(workspace_id:uuid.UUID,kind:Kind,body:dict,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    return settings.save(db,workspace_id,session.user_id,kind,setting_body(kind,body),request.app.state.settings)


@router.post('/ticket-settings/{kind}/{identifier}')
def update_setting(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,body:dict,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    return settings.save(db,workspace_id,session.user_id,kind,setting_body(kind,body,True),request.app.state.settings,identifier)


@router.post('/ticket-settings/{kind}/{identifier}/delete',status_code=204)
def remove_setting(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,body:Revision,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    settings.remove(db,workspace_id,session.user_id,kind,identifier,body.expected_revision);return Response(status_code=204)


@router.post('/ticket-settings/email-domains/{identifier}/verify')
def verify_domain(workspace_id:uuid.UUID,identifier:uuid.UUID,body:Revision,request:Request,db:Session=Depends(get_db)):
    session,_,_,_=access(request,db,workspace_id,mutation=True)
    from sqlalchemy.dialects.postgresql import insert
    from app.core.security import token_hash
    now=utcnow();key=token_hash('ticket-dns:{}:{}'.format(workspace_id,int(now.timestamp())//60))
    count=db.execute(insert(RateLimit).values(key=key,count=1,expires_at=now+timedelta(minutes=2)).on_conflict_do_update(index_elements=[RateLimit.key],set_={'count':RateLimit.count+1}).returning(RateLimit.count)).scalar_one()
    if count>10:raise HTTPException(429,'Wait a minute before checking domain DNS again.')
    return settings.verify(db,workspace_id,session.user_id,identifier,body.expected_revision,request.app.state.settings,request.app.state.ticket_dns)


@router.get('/tickets')
def list_tickets(workspace_id:uuid.UUID,request:Request,status:Optional[Literal['open','in_progress','resolved','closed','overdue']]=None,q:str=Query('',max_length=200),offset:int=Query(0,ge=0,le=10000),tag:Optional[uuid.UUID]=None,group:Optional[uuid.UUID]=None,custom_key:Optional[str]=Query(None,max_length=100),custom_value:Optional[str]=Query(None,max_length=4000),db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id);manage=agent_service.can_manage(db,workspace_id,role)
    query=select(SupportTicket).where(service.access_filter(db,workspace_id,session.user_id,manage))
    if status:query=query.where(SupportTicket.status==status)
    if q:query=query.where(SupportTicket.subject.icontains(q,autoescape=True)|SupportTicket.reference.icontains(q,autoescape=True)|SupportTicket.email.icontains(q,autoescape=True)|SupportTicket.full_name.icontains(q,autoescape=True))
    query=query.order_by(SupportTicket.created_at.desc(),SupportTicket.id.desc())
    if tag or group or custom_key:
        from app.teamspace.records import matches_filters
        rows=[];skipped=0
        for row in db.scalars(query):
            if not matches_filters(db,workspace_id,'tickets',row,tag,group,custom_key,custom_value):continue
            if skipped<offset:skipped+=1;continue
            rows.append(row)
            if len(rows)==51:break
    else:rows=db.scalars(query.offset(offset).limit(51)).all()
    return {'tickets':[service.payload(t,brief=True) for t in rows[:50]],'has_more':len(rows)>50,'can_manage':manage}


def detail_payload(db,ticket):
    from app.workspace_settings import permissions as p
    from app.models import AgentConversation, Agent
    member=p.membership(db,ticket.workspace_id)
    user_id=member.user_id if member else None
    legacy_manage=agent_service.can_manage(db,ticket.workspace_id,member.role) if member else False
    can_manage=bool(user_id and db.scalar(select(SupportTicket.id).where(SupportTicket.id==ticket.id,service.access_filter(db,ticket.workspace_id,user_id,legacy_manage,action='manage'))))
    conversation=db.get(AgentConversation,ticket.conversation_id) if ticket.conversation_id else None
    agent=db.get(Agent,conversation.agent_id) if conversation and conversation.agent_id else None
    can_transfer=bool(can_manage and p.allowed(db,ticket.workspace_id,'transfer_tickets',teamspace_id=agent.teamspace_id if agent else None))

    notes=list(reversed(db.scalars(select(TicketNote).where(TicketNote.ticket_id==ticket.id).order_by(TicketNote.created_at.desc(),TicketNote.id.desc()).limit(200)).all()))
    emails=db.scalars(select(TicketEmail).where(TicketEmail.ticket_id==ticket.id).order_by(TicketEmail.created_at,TicketEmail.id)).all()
    return {'ticket':service.payload(ticket),'can_manage':can_manage,'can_transfer':can_transfer,'notes':[{'id':str(n.id),'author_name':n.author_name,'kind':n.kind,'content':n.content,'created_at':n.created_at} for n in notes],
        'emails':[notifications.email_payload(e) for e in emails]}


@router.get('/tickets/{identifier}')
def read_ticket(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id)
    ticket=service.owned(db,workspace_id,session.user_id,agent_service.can_manage(db,workspace_id,role),identifier)
    return detail_payload(db,ticket)


@router.post('/tickets/{identifier}')
def update_ticket(workspace_id:uuid.UUID,identifier:uuid.UUID,body:TicketUpdate,request:Request,db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id,True)
    ticket=service.owned(db,workspace_id,session.user_id,agent_service.can_manage(db,workspace_id,role),identifier,True,action='manage')
    if body.status in {'resolved','closed'}:
        from app.teamspace.records import check_resolution
        check_resolution(db,workspace_id,'tickets',ticket)
    if ticket.revision!=body.expected_revision:raise HTTPException(409,'This ticket changed. Reload before saving.')
    if (body.assignee_type,body.assignee_id)!=(ticket.assignee_type,ticket.assignee_id):
        from app.workspace_settings.permissions import require
        from app.models import AgentConversation,Agent
        conversation=db.get(AgentConversation,ticket.conversation_id) if ticket.conversation_id else None
        agent=db.get(Agent,conversation.agent_id) if conversation and conversation.agent_id else None
        require(db,workspace_id,'transfer_tickets',teamspace_id=agent.teamspace_id if agent else None)
    try:name,_=service.assignee(db,workspace_id,body.assignee_type,body.assignee_id)
    except service.ScriptError as error:raise HTTPException(422,str(error)) from None
    if (ticket.status,ticket.assignee_type,ticket.assignee_id)!=(body.status,body.assignee_type,body.assignee_id):
        user=db.get(User,session.user_id)
        db.add(TicketNote(ticket_id=ticket.id,author_id=user.id,author_name=user.full_name or user.email,kind='event',content='Status: {} → {}. Assignee: {} → {}.'.format(ticket.status.replace('_',' '),body.status.replace('_',' '),ticket.assignee_name,name)))
        ticket.status=body.status;ticket.assignee_type=body.assignee_type;ticket.assignee_id=body.assignee_id;ticket.assignee_name=name;ticket.revision+=1;ticket.updated_at=utcnow()
    db.commit()
    if ticket.status in {'resolved','closed'}:
        from app.teamspace.jobs import analysis_request
        try:analysis_request(db,workspace_id,'tickets',ticket,None,None,request.app.state.settings,'auto')
        except HTTPException:db.rollback()
    return detail_payload(db,ticket)


@router.post('/tickets/{identifier}/notes',status_code=201)
def add_note(workspace_id:uuid.UUID,identifier:uuid.UUID,body:NoteBody,request:Request,db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id,True)
    ticket=service.owned(db,workspace_id,session.user_id,agent_service.can_manage(db,workspace_id,role),identifier,True,action='manage')
    user=db.get(User,session.user_id);ticket.revision+=1;ticket.updated_at=utcnow()
    db.add(TicketNote(ticket_id=ticket.id,author_id=user.id,author_name=user.full_name or user.email,content=body.content));db.commit();return detail_payload(db,ticket)


@router.post('/tickets/{identifier}/send-queued')
def send_queued(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id,True)
    ticket=service.owned(db,workspace_id,session.user_id,agent_service.can_manage(db,workspace_id,role),identifier,action='manage')
    db.commit();notifications.dispatch(db,request.app.state.ticket_mailer,ticket_id=ticket.id);return detail_payload(db,ticket)


@router.post('/tickets/{identifier}/emails/{email_id}/retry')
def retry_email(workspace_id:uuid.UUID,identifier:uuid.UUID,email_id:uuid.UUID,body:EmailRetry,request:Request,db:Session=Depends(get_db)):
    session,_,role,_=actor(request,db,workspace_id,True)
    ticket=service.owned(db,workspace_id,session.user_id,agent_service.can_manage(db,workspace_id,role),identifier,action='manage')
    row=db.scalar(select(TicketEmail).where(TicketEmail.id==email_id,TicketEmail.ticket_id==ticket.id).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'Ticket email not found.')
    if row.revision!=body.expected_revision:raise HTTPException(409,'Email delivery changed. Reload first.')
    status=notifications.email_payload(row)['status']
    if status not in {'failed','uncertain'}:raise HTTPException(409,'Only failed or unconfirmed deliveries can be retried.')
    if status=='uncertain' and not body.confirm_not_sent:raise HTTPException(409,'Confirm that the email was not sent before retrying an unconfirmed delivery.')
    if row.attempts>=5:raise HTTPException(409,'This email reached its five-attempt limit. Review the mail server configuration.')
    row.status='queued';row.error='';row.revision+=1;row.updated_at=utcnow();db.commit()
    notifications.dispatch(db,request.app.state.ticket_mailer,ticket_id=ticket.id);return detail_payload(db,ticket)
