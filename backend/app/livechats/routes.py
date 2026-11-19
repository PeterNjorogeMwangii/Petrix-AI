import uuid
from datetime import date
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.routes.inbox import access
from app.inbox import service as inbox
from app.livechats import service as s
from app.livechats.schemas import DetailsBody, TransferBody, NoteBody, NoteRevision, DeleteBody
from app.models import LivechatNote

router = APIRouter(prefix='/api/workspaces/{workspace_id}/livechats', tags=['Livechats'])

@router.get('/options')
def options(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id)
    return s.options(db, workspace_id, actor.user_id)

@router.get('')
def listing(workspace_id: uuid.UUID, request: Request, q: str = Query('', max_length=256),
    agent: Optional[uuid.UUID] = None, team: Optional[uuid.UUID] = None,
    status: Literal['', 'open', 'pending', 'overdue', 'resolved', 'closed'] = '',
    rating: Literal['', 'none', '1', '2', '3', '4', '5'] = '',
    tag: Optional[uuid.UUID] = None, group: Optional[uuid.UUID] = None,
    custom_key: str = Query('', max_length=100), custom_value: str = Query('', max_length=4000),
    primary_language: str = Query('', max_length=16), language: str = Query('', max_length=16),
    period: Literal['', 'today', 'week', 'month', 'custom'] = '',
    created_from: Optional[date] = None, created_to: Optional[date] = None,
    timezone: str = Query('Africa/Nairobi', max_length=100), offset: int = Query(0, ge=0),
    limit: int = Query(40, ge=1, le=100), db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id)
    filters = {key: value for key, value in locals().items() if key in {'q','agent','team','status','rating','tag','group','custom_key','custom_value','primary_language','language','period','created_from','created_to','timezone'}}
    return s.listing(db, workspace_id, actor.user_id, filters, offset, limit)

@router.post('/delete')
def remove(workspace_id: uuid.UUID, body: DeleteBody, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    inbox.assignment(db, workspace_id, lock=True)
    return s.remove(db, workspace_id, actor, body)

@router.get('/{identifier}')
def detail(workspace_id: uuid.UUID, identifier: uuid.UUID, request: Request, before: Optional[uuid.UUID] = None, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id)
    row = s.get(db, workspace_id, identifier, actor.user_id)
    notes = db.scalars(select(LivechatNote).where(LivechatNote.conversation_id == row.id).order_by(LivechatNote.created_at.desc(), LivechatNote.id.desc())).all()
    return {**s.summary(db, row, actor.user_id), **inbox.messages(db, row, before), 'notes': [s.note_payload(n) for n in notes]}

@router.post('/{identifier}/details')
def update_details(workspace_id: uuid.UUID, identifier: uuid.UUID, body: DetailsBody, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    row = s.get(db, workspace_id, identifier, actor.user_id, 'manage', True)
    s.revision(row, body.expected_revision)
    if row.live_status == 'closed' and body.status not in {'resolved', 'closed'}:
        raise HTTPException(409, 'A completed livechat cannot be reopened here. Start a new human handoff.')
    if row.live_status == 'pending' and body.status in {'open', 'resolved', 'closed'}:
        raise HTTPException(409, 'Assign or claim this livechat in Inbox before opening or completing it.')
    row.live_description = body.description
    row.live_display_status = body.status
    if body.status in {'resolved', 'closed'} and row.live_status != 'closed':
        if not row.agent_id: raise HTTPException(409, 'The AI agent for this livechat is unavailable.')
        from app.routes.workflows import process_event
        from app.workflows.schemas import TesterEvent
        db.flush()
        process_event(workspace_id, row.agent_id, TesterEvent(event='chat_closed', conversation_id=row.id), request, db, inbox_actor=actor, inbox_revision=body.expected_revision)
    else:
        row.live_revision += 1; row.live_updated_at = __import__('app.core.security', fromlist=['utcnow']).utcnow()
        inbox.audit(db, row, actor.user_id, 'details_saved'); db.commit()
    return s.summary(db, row, actor.user_id)

@router.post('/{identifier}/transfer')
def transfer(workspace_id: uuid.UUID, identifier: uuid.UUID, body: TransferBody, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    from app.teamspace.configuration import require_plan
    require_plan(db,workspace_id,request.app.state.settings,1)
    inbox.assignment(db, workspace_id, lock=True)
    row = s.get(db, workspace_id, identifier, actor.user_id, 'manage', True)
    s.transfer(db, row, actor, body)
    return {'transferred': True}

@router.post('/{identifier}/notes', status_code=201)
def create_note(workspace_id: uuid.UUID, identifier: uuid.UUID, body: NoteBody, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    row = s.get(db, workspace_id, identifier, actor.user_id, 'manage', True)
    return s.save_note(db, row, actor, body)

@router.post('/{identifier}/notes/{note_id}')
def update_note(workspace_id: uuid.UUID, identifier: uuid.UUID, note_id: uuid.UUID, body: NoteBody, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    row = s.get(db, workspace_id, identifier, actor.user_id, 'manage', True)
    return s.save_note(db, row, actor, body, note_id)

@router.post('/{identifier}/notes/{note_id}/delete')
def delete_note(workspace_id: uuid.UUID, identifier: uuid.UUID, note_id: uuid.UUID, body: NoteRevision, request: Request, db: Session = Depends(get_db)):
    actor, _, _ = access(request, db, workspace_id, mutation=True)
    row = s.get(db, workspace_id, identifier, actor.user_id, 'manage', True)
    note = db.scalar(select(LivechatNote).where(LivechatNote.id == note_id, LivechatNote.conversation_id == row.id).with_for_update().execution_options(populate_existing=True))
    if not note: raise HTTPException(404, 'Note not found.')
    if note.revision != body.expected_revision: raise HTTPException(409, 'This note changed. Reload first.')
    db.delete(note); inbox.audit(db, row, actor.user_id, 'note_deleted'); db.commit()
    return {'deleted': True}
