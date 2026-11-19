import uuid
from datetime import date
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.teamspace.routes import actor
from app.teamspace.schemas import PersonBody
from app.models import PersonNote
from app.people import service as s
from app.people.schemas import NoteBody, NoteRevision, DeleteBody

router=APIRouter(prefix='/api/workspaces/{workspace_id}/people',tags=['People CRM'])

@router.get('/options')
def options(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    return s.options(db,workspace_id,session.user_id)

@router.get('')
def listing(workspace_id:uuid.UUID,request:Request,q:str=Query('',max_length=256),
    tag:Optional[uuid.UUID]=None,group:Optional[uuid.UUID]=None,custom_key:str=Query('',max_length=100),custom_value:str=Query('',max_length=4000),
    channel:Literal['','ticket','webchat','whatsapp','ai_agent_handoff','messaging','bitrix24','africas_talking']='',country:str=Query('',max_length=2),
    period:Literal['','today','week','month','custom']='',created_from:Optional[date]=None,created_to:Optional[date]=None,
    timezone:str=Query('Africa/Nairobi',max_length=100),offset:int=Query(0,ge=0),limit:int=Query(40,ge=1,le=100),db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    filters={k:v for k,v in locals().items() if k in {'q','tag','group','custom_key','custom_value','channel','country','period','created_from','created_to','timezone'}}
    return s.listing(db,workspace_id,session.user_id,filters,offset,limit)

@router.post('',status_code=201)
def create(workspace_id:uuid.UUID,body:PersonBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    return s.register(db,workspace_id,session.user_id,body)

@router.post('/delete')
def remove(workspace_id:uuid.UUID,body:DeleteBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    return s.remove(db,workspace_id,session.user_id,body)

@router.get('/{identifier}')
def detail(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    return s.detail(db,s.get(db,workspace_id,identifier,session.user_id),session.user_id)

@router.post('/{identifier}')
def update(workspace_id:uuid.UUID,identifier:uuid.UUID,body:PersonBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    return s.update(db,workspace_id,identifier,session.user_id,body)

@router.get('/{identifier}/conversations')
def conversations(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    return s.history(db,s.get(db,workspace_id,identifier,session.user_id),session.user_id,offset,limit)

@router.get('/{identifier}/conversations/{kind}/{conversation_id}')
def transcript(workspace_id:uuid.UUID,identifier:uuid.UUID,kind:Literal['chats','tickets'],conversation_id:uuid.UUID,request:Request,before:Optional[uuid.UUID]=None,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id)
    return s.transcript(db,s.get(db,workspace_id,identifier,session.user_id),session.user_id,kind,conversation_id,before)

@router.post('/{identifier}/notes',status_code=201)
def create_note(workspace_id:uuid.UUID,identifier:uuid.UUID,body:NoteBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    return s.save_note(db,s.get(db,workspace_id,identifier,session.user_id,True,True),session.user_id,body)

@router.post('/{identifier}/notes/{note_id}')
def update_note(workspace_id:uuid.UUID,identifier:uuid.UUID,note_id:uuid.UUID,body:NoteBody,request:Request,db:Session=Depends(get_db)):
    session=actor(request,db,workspace_id,True)
    return s.save_note(db,s.get(db,workspace_id,identifier,session.user_id,True,True),session.user_id,body,note_id)

@router.post('/{identifier}/notes/{note_id}/delete')
def delete_note(workspace_id:uuid.UUID,identifier:uuid.UUID,note_id:uuid.UUID,body:NoteRevision,request:Request,db:Session=Depends(get_db)):
    from fastapi import HTTPException
    from app.core.security import utcnow
    session=actor(request,db,workspace_id,True);row=s.get(db,workspace_id,identifier,session.user_id,True,True)
    note=db.scalar(select(PersonNote).where(PersonNote.id==note_id,PersonNote.person_id==identifier).with_for_update().execution_options(populate_existing=True))
    if not note:raise HTTPException(404,'Internal note not found.')
    if note.revision!=body.expected_revision:raise HTTPException(409,'This note changed. Reload before deleting.')
    db.delete(note);row.revision+=1;row.updated_at=utcnow();db.commit();return s.detail(db,row,session.user_id)
