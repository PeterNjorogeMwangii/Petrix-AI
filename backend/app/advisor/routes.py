import uuid
from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter,Depends,Request,Query,HTTPException,UploadFile,File,Response
from sqlalchemy import select,func,delete
from sqlalchemy.orm import Session
from app.database import get_db
from app.teamspace.routes import actor
from app.core.security import utcnow
from app.models import AdvisorConversation as Conversation,AdvisorAttachment as Attachment,Membership
from app.advisor import service as s,schemas as b,knowledge as k
from app.advisor.attachments import prepare,MAX_FILE

router=APIRouter(prefix='/api/workspaces/{workspace_id}/advisor',tags=['Advisor'])

def access(request,db,workspace,mutation=False):return actor(request,db,workspace,mutation).user_id

@router.get('/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    return {**s.options(db,workspace_id,access(request,db,workspace_id),request.app.state.advisor_ai),'worker_enabled':request.app.state.settings.advisor_worker_enabled}

@router.get('/docs')
def docs(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id)
    if not s.spaces(db,workspace_id,user):raise HTTPException(403,'No accessible teamspace.')
    return {'version':k.manual()['version'],'documents':[{'slug':slug,'title':value['title']} for slug,value in k.manual()['documents'].items()]}

@router.get('/docs/{slug}')
def doc(workspace_id:uuid.UUID,slug:str,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id)
    if not s.spaces(db,workspace_id,user):raise HTTPException(403,'No accessible teamspace.')
    value=k.manual()['documents'].get(slug)
    if not value:raise HTTPException(404,'Documentation page not found.')
    return {**value,'slug':slug,'version':k.manual()['version']}

@router.get('/openapi')
def openapi(workspace_id:uuid.UUID,request:Request,download:bool=False,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id)
    if not s.spaces(db,workspace_id,user):raise HTTPException(403,'No accessible teamspace.')
    value=request.app.openapi()
    if download:
        import json
        return Response(json.dumps(value,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="petrix-ai-openapi.json"','X-Content-Type-Options':'nosniff'})
    return value

@router.get('/conversations')
def history(workspace_id:uuid.UUID,request:Request,space_id:Optional[uuid.UUID]=None,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100),db:Session=Depends(get_db)):
    return s.history(db,workspace_id,access(request,db,workspace_id),space_id,offset,limit)

@router.post('/conversations',status_code=201)
def create(workspace_id:uuid.UUID,body:b.Create,request:Request,db:Session=Depends(get_db)):
    return s.create(db,workspace_id,access(request,db,workspace_id,True),body)

@router.get('/conversations/{identifier}')
def detail(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,before:Optional[uuid.UUID]=None,limit:int=Query(40,ge=1,le=100),db:Session=Depends(get_db)):
    return s.detail(db,s.own(db,workspace_id,access(request,db,workspace_id),identifier),before,limit)

@router.post('/conversations/{identifier}/messages',status_code=202)
def send(workspace_id:uuid.UUID,identifier:uuid.UUID,body:b.Send,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,True);return s.submit(db,s.own(db,workspace_id,user,identifier),user,body)

@router.post('/conversations/{identifier}/retry/{message_id}',status_code=202)
def retry(workspace_id:uuid.UUID,identifier:uuid.UUID,message_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,True);return s.retry(db,s.own(db,workspace_id,user,identifier),user,message_id)

@router.post('/conversations/{identifier}/delete')
def remove(workspace_id:uuid.UUID,identifier:uuid.UUID,body:b.Delete,request:Request,db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,True);row=s.own(db,workspace_id,user,identifier,True)
    if body.expected_revision!=row.revision:raise HTTPException(409,'This Advisor conversation changed. Reload before deleting.')
    if not body.confirmed:raise HTTPException(409,'Confirm deletion of this private conversation.')
    db.delete(row);db.commit();return {'deleted':True}

@router.post('/attachments',status_code=201)
def upload(workspace_id:uuid.UUID,request:Request,file:UploadFile=File(...),db:Session=Depends(get_db)):
    user=access(request,db,workspace_id,True)
    if not s.spaces(db,workspace_id,user):raise HTTPException(403,'No accessible teamspace.')
    data=file.file.read(MAX_FILE+1);value=prepare(file.filename,data)
    member=db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==user).with_for_update())
    if not member or not s.spaces(db,workspace_id,user):raise HTTPException(403,'Advisor access changed before the upload finished.')
    from datetime import timedelta
    db.execute(delete(Attachment).where(Attachment.user_id==user,Attachment.workspace_id==workspace_id,Attachment.message_id.is_(None),Attachment.created_at<utcnow()-timedelta(days=1)))
    count=db.scalar(select(func.count()).select_from(Attachment).where(Attachment.workspace_id==workspace_id,Attachment.user_id==user,Attachment.message_id.is_(None))) or 0
    if count>=20:raise HTTPException(422,'Remove unused Advisor attachments before uploading more.')
    row=Attachment(workspace_id=workspace_id,user_id=user,**value);db.add(row);db.commit();return s.attachment_payload(row,workspace_id)

def attachment(db,workspace,user,identifier,locked=False):
    query=select(Attachment).where(Attachment.id==identifier,Attachment.workspace_id==workspace,Attachment.user_id==user)
    if locked:query=query.with_for_update().execution_options(populate_existing=True)
    row=db.scalar(query)
    if not row:raise HTTPException(404,'Advisor attachment not found.')
    if row.message_id:
        from app.models import AdvisorMessage
        message=db.get(AdvisorMessage,row.message_id)
        if not message:raise HTTPException(404,'Advisor attachment not found.')
        s.own(db,workspace,user,message.conversation_id)
    elif not s.spaces(db,workspace,user):raise HTTPException(403,'No accessible teamspace.')
    return row

@router.get('/attachments/{identifier}')
def download(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    row=attachment(db,workspace_id,access(request,db,workspace_id),identifier)
    return Response(row.data,media_type=row.mime_type,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row.filename),'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})

@router.get('/attachments/{identifier}/preview')
def preview(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    row=attachment(db,workspace_id,access(request,db,workspace_id),identifier)
    if not row.preview:raise HTTPException(404,'Image preview not available.')
    return Response(row.preview,media_type='image/jpeg',headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'"})

@router.post('/attachments/{identifier}/delete')
def delete_attachment(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    row=attachment(db,workspace_id,access(request,db,workspace_id,True),identifier,True)
    if row.message_id:raise HTTPException(409,'Sent attachments remain part of their conversation. Delete the conversation to remove them.')
    db.delete(row);db.commit();return {'deleted':True}
