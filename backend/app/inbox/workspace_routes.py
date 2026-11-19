import json
import uuid
from datetime import datetime
from typing import List, Literal, Optional
from urllib.parse import quote
from fastapi import APIRouter, Depends, Request, Query, HTTPException, File, UploadFile, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.teamspace.routes import actor
from app.teamspace import records as r, configuration as c
from app.models import AgentMessage, InboxView, InboxAttachment, AgentConversation, LivechatNote, TicketNote, TicketEmail, TicketInboundEmail
from app.inbox import workspace as w, workspace_schemas as s, copilot

router = APIRouter(prefix='/api/workspaces/{workspace_id}/inbox', tags=['Unified Inbox'])
Kind = Literal['chats', 'tickets']


@router.get('/workspace-options')
def options(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session = actor(request, db, workspace_id)
    return w.options(db, workspace_id, session.user_id, request.app.state.settings)


@router.get('/conversations')
def listing(workspace_id: uuid.UUID, request: Request, section: Literal['mine','conversations','livechats','tickets','ai','test']='mine',
    views: List[str]=Query(default=[]), filters: str=Query('{}',max_length=8000), teamspace_id: Optional[uuid.UUID]=None,
    q: str=Query('',max_length=256), search_at: Optional[datetime]=None, sort: Literal['','created','latest','wait','handoff']='',
    direction: Literal['asc','desc']='desc', offset: int=Query(0,ge=0), limit: int=Query(40,ge=1,le=100), db: Session=Depends(get_db)):
    session = actor(request, db, workspace_id)
    if len(views)>10: raise HTTPException(422,'Stack at most ten views.')
    try: extra=s.ViewFilters.model_validate_json(filters)
    except ValueError: raise HTTPException(422,'Select valid Inbox filters.') from None
    return w.listing(db,workspace_id,session.user_id,section,views,extra,teamspace_id,q,search_at,sort,direction,offset,limit)


@router.post('/views',status_code=201)
def create_view(workspace_id:uuid.UUID,body:s.ViewBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);return w.save_view(db,workspace_id,session.user_id,body)


@router.post('/views/{identifier}')
def edit_view(workspace_id:uuid.UUID,identifier:uuid.UUID,body:s.ViewBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);return w.save_view(db,workspace_id,session.user_id,body,identifier)


@router.post('/views/{identifier}/delete')
def delete_view(workspace_id:uuid.UUID,identifier:uuid.UUID,body:s.ViewRevision,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    row=next((v for v in w.views(db,workspace_id,session.user_id) if v.id==identifier),None)
    if not row:raise HTTPException(404,'View not found.')
    from app.models import Teamspace
    db.scalar(select(Teamspace).where(Teamspace.id==row.teamspace_id).with_for_update())
    row=db.scalar(select(InboxView).where(InboxView.id==identifier).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'View not found.')
    if row.owner_id!=session.user_id and not (row.scope=='teamspace' and w.shared_manage(db,workspace_id,session.user_id,row.teamspace_id)):raise HTTPException(403,'You cannot delete this view.')
    if row.revision!=body.expected_revision:raise HTTPException(409,'This view changed. Reload before deleting.')
    db.delete(row);db.commit();return {'deleted':True}


@router.post('/preferences')
def save_preferences(workspace_id:uuid.UUID,body:s.Preferences,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);return w.save_preferences(db,workspace_id,session.user_id,body)


def ticket_messages(db,row,before=None):
    from app.tickets.notifications import email_payload
    queries=[select(TicketEmail).where(TicketEmail.ticket_id==row.id,TicketEmail.recipient_type=='customer'),
        select(TicketInboundEmail).where(TicketInboundEmail.ticket_id==row.id)]
    marker=None
    if before:
        marker=db.scalar(queries[0].where(TicketEmail.id==before)) or db.scalar(queries[1].where(TicketInboundEmail.id==before))
        if not marker:raise HTTPException(404,'Message cursor not found.')
    groups=[]
    for model,query in zip((TicketEmail,TicketInboundEmail),queries):
        if marker:query=query.where((model.created_at<marker.created_at)|((model.created_at==marker.created_at)&(model.id<marker.id)))
        groups.append(db.scalars(query.order_by(model.created_at.desc(),model.id.desc()).limit(101)).all())
    sent,incoming=groups
    messages=[{'id':str(e.id),'role':'assistant','content':e.body_text,'created_at':e.created_at,'source':'live_agent' if e.kind=='reply' else 'email','client_message_id':str(e.client_request_id) if e.client_request_id else None,'avatar_url':e.persona.get('avatar_url'),'author_name':e.persona.get('name',e.sender_name),'attachments':e.persona.get('attachments',[]),'delivery_status':email_payload(e)['email_status'],'delivery_error':e.error} for e in sent]
    messages += [{'id':str(e.id),'role':'user','content':e.content,'created_at':e.created_at,'author_name':row.full_name or e.sender} for e in incoming]
    messages.sort(key=lambda m:(m['created_at'],m['id']))
    return {'messages':messages[-100:],'has_more':len(messages)>100}


def detail_payload(db,row,kind,user_id,settings,before=None):
    from app.inbox.service import messages
    result=w.summary(db,kind,row,user_id)
    if kind=='chats':
        result.update(messages(db,row,before));notes=db.scalars(select(LivechatNote).where(LivechatNote.conversation_id==row.id).order_by(LivechatNote.created_at).limit(300)).all()
        result['cc']=[]
    else:
        result.update(ticket_messages(db,row,before))
        latest=db.scalar(select(TicketEmail).where(TicketEmail.ticket_id==row.id,TicketEmail.recipient_type=='customer').order_by(TicketEmail.created_at.desc(),TicketEmail.id.desc()).limit(1))
        notes=db.scalars(select(TicketNote).where(TicketNote.ticket_id==row.id,TicketNote.kind=='note').order_by(TicketNote.created_at).limit(300)).all()
        result['cc']=latest.cc_recipients if latest else [];result['sender_domain_id']=str(row.sender_domain_id) if row.sender_domain_id else None
    result['notes']=[{'id':str(n.id),'content':n.content,'author_name':n.author_name,'created_at':n.created_at,'revision':n.revision,'color':n.color,'private':True} for n in notes]
    ops=c.operations(db,row.workspace_id,r.record_space(db,kind,row));result['can_copilot']=copilot.permitted(db,row,kind,user_id)
    result['mediate_allowed']=ops.copilot.get('current_conversation',True);result['provider_ready']=bool(settings.support_ai_base_url and settings.support_ai_model)
    result['custom_iframe']=ops.profile_iframe if __import__('app.workspace_settings.permissions',fromlist=['allowed']).allowed(db,row.workspace_id,'view_profile_iframe',user_id,teamspace_id=ops.teamspace_id) else {'enabled':False}
    return result


@router.get('/conversations/{kind}/{identifier}')
def detail(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,request:Request,before:Optional[uuid.UUID]=None,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id);row=r.record(db,workspace_id,kind,identifier,session.user_id)
    return detail_payload(db,row,kind,session.user_id,request.app.state.settings,before)


@router.post('/conversations/{kind}/{identifier}/notes')
def note(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,body:__import__('app.tickets.workspace_schemas',fromlist=['NoteBody']).NoteBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id,'manage',True)
    if kind=='chats':__import__('app.livechats.service',fromlist=['save_note']).save_note(db,row,session,body)
    else:__import__('app.tickets.workspace',fromlist=['save_note']).save_note(db,row,session.user_id,body)
    return detail_payload(db,row,kind,session.user_id,request.app.state.settings)


@router.post('/conversations/{kind}/{identifier}/assistant',status_code=202)
def assistant(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,body:s.AssistantRequest,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True);row=r.record(db,workspace_id,kind,identifier,session.user_id)
    return copilot.request(db,row,kind,session.user_id,body,request.app.state.settings)


@router.post('/{kind}/{identifier}/attachments',status_code=201)
async def upload(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,request:Request,files:List[UploadFile]=File(...),db:Session=Depends(get_db)):
    from starlette.concurrency import run_in_threadpool
    def authorize():
        session=actor(request,db,workspace_id,True)
        r.record(db,workspace_id,kind,identifier,session.user_id,'manage')
        db.rollback()
    await run_in_threadpool(authorize)
    if not 1<=len(files)<=5:raise HTTPException(422,'Upload between one and five attachments.')
    from app.workflows.attachments import read_uploads
    payloads=await read_uploads(files)
    def persist():
        session=actor(request,db,workspace_id,True)
        r.record(db,workspace_id,kind,identifier,session.user_id,'manage')
        from sqlalchemy import func
        from app.models import Membership
        db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==session.user_id).with_for_update())
        count=db.scalar(select(func.count()).select_from(InboxAttachment).where(InboxAttachment.workspace_id==workspace_id,InboxAttachment.actor_id==session.user_id,InboxAttachment.used.is_(False))) or 0
        if count+len(payloads)>30:raise HTTPException(409,'Remove unused attachments before uploading more.')
        assets=[InboxAttachment(workspace_id=workspace_id,kind=kind,record_id=identifier,actor_id=session.user_id,**v) for v in payloads]
        db.add_all(assets);db.commit();return {'attachments':[w.attachment_payload(a) for a in assets]}
    return await run_in_threadpool(persist)


@router.post('/{kind}/{identifier}/attachments/{asset_id}/delete')
def remove_attachment(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,asset_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    asset=db.get(InboxAttachment,asset_id)
    if not asset or asset.workspace_id!=workspace_id or asset.kind!=kind or asset.record_id!=identifier or asset.actor_id!=session.user_id or asset.used:raise HTTPException(404,'Unused attachment not found.')
    db.delete(asset);db.commit();return {'deleted':True}


@router.get('/{kind}/{identifier}/attachments/{asset_id}')
def download(workspace_id:uuid.UUID,kind:Kind,identifier:uuid.UUID,asset_id:uuid.UUID,request:Request,inline:bool=False,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    asset=db.get(InboxAttachment,asset_id)
    from app.workspace_settings import permissions as p
    if not asset and kind=='chats':
        from app.models import AgentAttachment
        file=db.get(AgentAttachment,asset_id)
        chat=db.get(AgentConversation,identifier)
        if not file or file.conversation_id!=identifier or not chat or chat.workspace_id!=workspace_id:raise HTTPException(404,'Attachment not found.')
        customer=chat.user_id==session.user_id and p.allowed(db,workspace_id,'view_ai_agents',session.user_id,teamspace_id=r.record_space(db,'chats',chat))
        if not customer:r.record(db,workspace_id,kind,identifier,session.user_id)
        asset=file
    else:
        if not asset or asset.workspace_id!=workspace_id or asset.kind!=kind or asset.record_id!=identifier:raise HTTPException(404,'Attachment not found.')
        chat=db.get(AgentConversation,identifier) if kind=='chats' else None
        customer=bool(chat and chat.user_id==session.user_id and asset.used and not asset.private and p.allowed(db,workspace_id,'view_ai_agents',session.user_id,teamspace_id=r.record_space(db,'chats',chat)))
        if not customer:
            r.record(db,workspace_id,kind,identifier,session.user_id)
            if not asset.used and asset.actor_id!=session.user_id:raise HTTPException(404,'Attachment not found.')
    safe_inline=inline and (asset.content_type.startswith(('image/','audio/','video/')) and asset.content_type!='image/svg+xml' or asset.content_type=='application/pdf')
    return Response(asset.data,media_type=asset.content_type if safe_inline else 'application/octet-stream',headers={'Content-Disposition':('inline' if safe_inline else 'attachment')+"; filename*=UTF-8''"+quote(asset.file_name,safe=''),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
