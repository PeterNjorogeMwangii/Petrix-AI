import uuid
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import TicketNote
from app.routes.tickets import actor, detail_payload
from app.tickets import workspace as w
from app.tickets import workspace_schemas as m
from app.teamspace import records as r
from app.workspace_settings import permissions as p

router = APIRouter(prefix='/api/workspaces/{workspace_id}/tickets', tags=['Ticket workspace'])


@router.get('/workspace-options')
def options(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id)
    return w.options(db, workspace_id, session.user_id, request.app.state.settings)


@router.get('/people')
def people(workspace_id: uuid.UUID, request: Request, q: str = Query('', max_length=256), db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id)
    w.require_list(db, workspace_id, session.user_id)
    return {'people': w.people(db, workspace_id, session.user_id, q)}


@router.post('', status_code=201)
def create(workspace_id: uuid.UUID, body: m.CreateBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    ticket = w.create(db, workspace_id, session, body, request.app.state.settings)
    return detail_payload(db, ticket, request.app.state.settings)


@router.post('/delete')
def remove(workspace_id: uuid.UUID, body: m.DeleteBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    return w.remove(db, workspace_id, session, body)


@router.post('/{identifier}/details')
def details(workspace_id: uuid.UUID, identifier: uuid.UUID, body: m.DetailsBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    ticket = r.record(db, workspace_id, 'tickets', identifier, session.user_id, 'manage', True)
    w.revision(ticket, body.expected_revision)
    if body.status in {'resolved', 'closed'}: r.check_resolution(db, workspace_id, 'tickets', ticket)
    w.sender(db, workspace_id, body.sender_domain_id, request.app.state.settings)
    status = 'in_progress' if body.status == 'pending' else body.status
    if (ticket.status, ticket.description, ticket.sender_domain_id) != (status, body.description, body.sender_domain_id):
        ticket.status = status; ticket.description = body.description; ticket.sender_domain_id = body.sender_domain_id
        w.touch(ticket); w.event(db, ticket, session.user_id, 'Ticket details updated. Status: ' + body.status + '.')
    db.commit()
    if ticket.status in {'resolved', 'closed'}:
        from app.teamspace.jobs import analysis_request
        from fastapi import HTTPException
        try: analysis_request(db, workspace_id, 'tickets', ticket, None, None, request.app.state.settings, 'auto')
        except HTTPException: db.rollback()
    return detail_payload(db, ticket, request.app.state.settings)


@router.post('/{identifier}/transfer')
def transfer(workspace_id: uuid.UUID, identifier: uuid.UUID, body: m.TransferBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    from app.teamspace.configuration import require_plan
    require_plan(db,workspace_id,request.app.state.settings,1)
    ticket = r.record(db, workspace_id, 'tickets', identifier, session.user_id, 'manage', True)
    w.revision(ticket, body.expected_revision)
    p.require(db, workspace_id, 'transfer_tickets', session.user_id, teamspace_id=r.record_space(db, 'tickets', ticket))
    kind, assigned, name = w.assignment(db, workspace_id, r.record_space(db, 'tickets', ticket), body.user_id, body.team_ids)
    ticket.assignee_type = kind; ticket.assignee_id = assigned; ticket.assignee_name = name
    ticket.assigned_team_ids = list(map(str, body.team_ids)); ticket.audience_team_ids = []
    w.touch(ticket); w.event(db, ticket, session.user_id, 'Assignment updated: ' + name + '.'); db.commit()
    # A transfer can intentionally remove the initiating user's access.
    if not w.permitted(db, ticket, session.user_id): return {'transferred': True}
    return detail_payload(db, ticket, request.app.state.settings)


@router.post('/{identifier}/private-notes', status_code=201)
def note(workspace_id: uuid.UUID, identifier: uuid.UUID, body: m.NoteBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    ticket = r.record(db, workspace_id, 'tickets', identifier, session.user_id, 'manage', True)
    w.save_note(db, ticket, session.user_id, body)
    return detail_payload(db, ticket, request.app.state.settings)


@router.post('/{identifier}/private-notes/{note_id}')
def edit_note(workspace_id: uuid.UUID, identifier: uuid.UUID, note_id: uuid.UUID, body: m.NoteBody, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    ticket = r.record(db, workspace_id, 'tickets', identifier, session.user_id, 'manage', True)
    w.save_note(db, ticket, session.user_id, body, note_id)
    return detail_payload(db, ticket, request.app.state.settings)


@router.post('/{identifier}/private-notes/{note_id}/delete')
def delete_note(workspace_id: uuid.UUID, identifier: uuid.UUID, note_id: uuid.UUID, body: m.NoteRevision, request: Request, db: Session = Depends(get_db)):
    session, _, _, _ = actor(request, db, workspace_id, True)
    ticket = r.record(db, workspace_id, 'tickets', identifier, session.user_id, 'manage', True)
    from fastapi import HTTPException
    note = db.scalar(select(TicketNote).where(TicketNote.id == note_id, TicketNote.ticket_id == ticket.id, TicketNote.kind == 'note').with_for_update().execution_options(populate_existing=True))
    if not note: raise HTTPException(404, 'Internal note not found.')
    w.revision(note, body.expected_revision); db.delete(note); w.touch(ticket); db.commit()
    return detail_payload(db, ticket, request.app.state.settings)
