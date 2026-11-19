import hmac,json,secrets,uuid
from typing import Optional
from fastapi import APIRouter,Depends,Request,HTTPException
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.agents import access
from app.agent_service import record_change
from app.core.security import new_token,token_hash,utcnow
from app.models import WhatsAppChannel as Channel,WhatsAppInbound as Inbound,WhatsAppOutbox as Outbox,AgentMessage
from app.whatsapp import schemas as b,service as s,credentials as crypto
from app.whatsapp.client import GraphError

router=APIRouter(prefix='/api/workspaces/{workspace_id}/agents/{agent_id}/whatsapp',tags=['Publish WhatsApp'])


@router.get('')
def index(workspace_id:uuid.UUID,agent_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    _,_,_,agent=access(request,db,workspace_id,agent_id=agent_id)
    return {'items':[s.payload(db,row,request.app.state.settings) for row in db.scalars(select(Channel).where(Channel.agent_id==agent_id,Channel.workspace_id==workspace_id).order_by(Channel.created_at))],
        'eligible':s.eligible(db,workspace_id),'languages':agent.languages,'minimum_plan':'Lite'}


@router.post('',status_code=201)
def create(workspace_id:uuid.UUID,agent_id:uuid.UUID,body:b.Create,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    return s.payload(db,s.create(db,agent,actor.user_id,body,request.app.state.settings),request.app.state.settings)


@router.get('/{identifier}')
def detail(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,agent_id=agent_id)
    return s.payload(db,s.own(db,workspace_id,agent_id,identifier),request.app.state.settings)


@router.post('/{identifier}')
def save(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Save,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier,True);s.revision(row,body.expected_revision)
    config=s.checked(db,agent,body.config)
    if body.enabled:s.can_enable(db,row,request.app.state.settings)
    row.name=body.name;row.config=config;row.runtime=s.freeze(db,agent);row.actor_id=actor.user_id;row.enabled=body.enabled;row.revision+=1;row.updated_at=utcnow()
    record_change(db,agent,actor.user_id,'whatsapp_saved','WhatsApp channel '+row.name+' saved'+(' and enabled.' if row.enabled else '.'))
    db.commit();return s.payload(db,row,request.app.state.settings)


@router.post('/{identifier}/configuration')
def configuration(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Connection,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier,True);s.revision(row,body.expected_revision);settings=request.app.state.settings
    previous=crypto.secrets(row,settings)
    values={'app_secret':body.app_secret.get_secret_value() or previous.get('app_secret',''),'access_token':body.access_token.get_secret_value() or previous.get('access_token','')}
    if not all(values.values()):raise HTTPException(422,'Enter the Meta App secret and User access token.')
    changed=values!=previous or row.app_id!=body.app_id or row.phone_number_id!=body.phone_number_id
    conflict=db.scalar(select(Channel.id).where(Channel.phone_number_id==body.phone_number_id,Channel.id!=row.id))
    if conflict:raise HTTPException(409,'This WhatsApp Phone number ID is already assigned to a channel.')
    if changed:
        app_changed=row.app_id!=body.app_id
        row.app_id=body.app_id;row.phone_number_id=body.phone_number_id;row.credentials=crypto.encrypt(json.dumps(values),settings)
        row.connection_revision+=1;row.verified_revision=None;row.verified_at=None;row.enabled=False;row.last_error='';row.display_phone='';row.verified_name=''
        if app_changed:row.webhook_verified_at=None
    row.revision+=1;row.updated_at=utcnow()
    record_change(db,agent,actor.user_id,'whatsapp_configuration_saved','WhatsApp connection settings saved; credentials are private.')
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'This WhatsApp Phone number ID is already assigned to a channel.') from None
    return s.payload(db,row,settings)


@router.post('/{identifier}/verify')
def verify(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Revision,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier);s.revision(row,body.expected_revision);settings=request.app.state.settings
    value=crypto.secrets(row,settings)
    if not row.app_id or not row.phone_number_id or not value:raise HTTPException(422,'Save all four Meta connection fields first.')
    expected=row.revision;app_id=row.app_id;phone=row.phone_number_id;db.commit()
    try:
        result=request.app.state.whatsapp_client.verify(app_id,value['app_secret'],value['access_token'],phone);failure=''
    except GraphError as error:result={};failure=str(error)
    row=s.own(db,workspace_id,agent_id,identifier,True);s.revision(row,expected)
    if failure:
        row.verified_revision=None;row.verified_at=None;row.enabled=False;row.last_error=failure[:300]
    else:
        row.verified_revision=row.connection_revision;row.verified_at=utcnow();row.last_error='';row.display_phone=result['display_phone'];row.verified_name=result['verified_name']
    row.revision+=1;row.updated_at=utcnow();db.commit();return s.payload(db,row,settings)


@router.post('/{identifier}/setup-secret')
def reveal(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Revision,request:Request,db:Session=Depends(get_db)):
    access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier);s.revision(row,body.expected_revision)
    return {'verify_token':crypto.decrypt(row.setup_secret,request.app.state.settings)}


@router.post('/{identifier}/reissue')
def reissue(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Delete,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier,True);s.revision(row,body.expected_revision)
    if not body.confirmed:raise HTTPException(422,'Confirm reissuing the webhook verify token.')
    secret=new_token();row.setup_secret=crypto.encrypt(secret,request.app.state.settings);row.verify_token_hash=token_hash(secret)
    row.webhook_verified_at=None;row.enabled=False;row.revision+=1;row.updated_at=utcnow()
    record_change(db,agent,actor.user_id,'whatsapp_webhook_reissued','WhatsApp webhook verify token reissued; verify it in Meta before enabling.')
    db.commit();return {**s.payload(db,row,request.app.state.settings),'verify_token':secret}


@router.post('/{identifier}/delete')
def delete(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,body:b.Delete,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id)
    row=s.own(db,workspace_id,agent_id,identifier,True);s.revision(row,body.expected_revision)
    if not body.confirmed:raise HTTPException(422,'Confirm deleting this WhatsApp channel.')
    record_change(db,agent,actor.user_id,'whatsapp_deleted','WhatsApp channel deleted; customer conversation records retained.')
    pending=list(db.scalars(select(Outbox).where(Outbox.channel_id==identifier,Outbox.status.in_(['queued','sending','uncertain','blocked','failed'])).with_for_update()))
    for message_id in {item.message_id for item in pending}:
        message=db.get(AgentMessage,message_id)
        if not message:continue
        uncertain=any(item.message_id==message_id and item.status in {'sending','uncertain'} for item in pending)
        message.message_data={**message.message_data,'delivery_status':'uncertain' if uncertain else 'cancelled',
            'delivery_error':'Delivery was unconfirmed when this channel was deleted.' if uncertain else 'Unsent delivery cancelled because the channel was deleted.'}
    db.delete(row);db.commit();return {'deleted':True}


@router.get('/{identifier}/activity')
def activity(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,request:Request,offset:int=0,db:Session=Depends(get_db)):
    access(request,db,workspace_id,agent_id=agent_id);s.own(db,workspace_id,agent_id,identifier)
    if not 0<=offset<=100000:raise HTTPException(422,'Choose a valid activity page.')
    inbound=list(db.scalars(select(Inbound).where(Inbound.channel_id==identifier).order_by(Inbound.position.desc()).offset(offset).limit(21)))
    outbound=list(db.scalars(select(Outbox).where(Outbox.channel_id==identifier).order_by(Outbox.created_at.desc(),Outbox.message_sequence.desc(),Outbox.part.desc()).offset(offset).limit(21)))
    return {'inbound':[{**s.status_payload(v),'kind':v.kind} for v in inbound[:20]],'outbound':[s.status_payload(v) for v in outbound[:20]],'has_more':len(inbound)>20 or len(outbound)>20}


@router.post('/{identifier}/activity/{kind}/{job_id}')
def review(workspace_id:uuid.UUID,agent_id:uuid.UUID,identifier:uuid.UUID,kind:str,job_id:uuid.UUID,body:b.Review,request:Request,db:Session=Depends(get_db)):
    actor,_,_,agent=access(request,db,workspace_id,mutation=True,agent_id=agent_id);s.own(db,workspace_id,agent_id,identifier)
    model={'inbound':Inbound,'outbound':Outbox}.get(kind)
    if not model or not body.confirmed:raise HTTPException(422,'Confirm this activity action.')
    row=db.scalar(select(model).where(model.id==job_id,model.channel_id==identifier).with_for_update())
    if not row:raise HTTPException(404,'Activity record not found.')
    if kind=='inbound':
        if body.action!='dismiss' or row.status not in {'uncertain','failed'}:raise HTTPException(409,'Only failed or uncertain incoming work can be dismissed. Workflows are not automatically replayed.')
        row.status='dismissed';row.completed_at=utcnow()
    else:
        related=list(db.scalars(select(Outbox).where(Outbox.message_id==row.message_id).order_by(Outbox.part).with_for_update()))
        if body.action=='cancel' and row.status in {'queued','failed','blocked','uncertain'}:
            for item in related:
                if item.status in {'queued','failed','blocked','uncertain'}:item.status='cancelled';item.error='';item.updated_at=utcnow()
        elif body.action=='retry' and row.status in {'failed','blocked'}:
            from app.whatsapp.delivery import validate_delivery
            if any(item.status=='uncertain' for item in related):raise HTTPException(409,'Review the unconfirmed parts of this message before retrying.')
            channel,_=validate_delivery(db,row,request.app.state.settings,allow_revision=True)
            for item in related:
                if item.status in {'failed','blocked','queued'}:item.status='queued';item.error='';item.connection_revision=channel.connection_revision;item.updated_at=utcnow()
        else:raise HTTPException(409,'An unconfirmed send is not replayed. Wait for a Meta status update or cancel it before sending a new reply.')
        from app.whatsapp.delivery import update_message
        db.flush();update_message(db,row.message_id)
    record_change(db,agent,actor.user_id,'whatsapp_activity_reviewed','WhatsApp '+kind+' activity '+body.action+' confirmed.')
    db.commit();return s.status_payload(row)
