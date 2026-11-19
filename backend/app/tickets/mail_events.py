"""Authenticated adapter contract for incoming mail and provider delivery receipts.

The adapter must verify its mail provider before signing this normalized payload.
SMTP acceptance alone is deliberately never treated as a delivery receipt.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, field_validator, model_validator, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.agent_schemas import StrictModel
from app.core.security import utcnow
from app.database import get_db
from app.models import (Workspace, Teamspace, SupportTicket, TicketCounter, TicketEmail,
    TicketEmailDomain, TicketInboundEmail, TicketMailEvent, SupportPerson, UsageEvent, TicketNote)
from app.tickets import service as s, settings, workspace as w
from app.workspace_settings import permissions as p

router = APIRouter(tags=['Ticket mail adapter'])


class MailEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=200)
    type: Literal['incoming', 'delivered', 'bounced']
    occurred_at: datetime
    message_id: str = Field(min_length=1, max_length=320)
    ticket_id: Optional[uuid.UUID] = None
    in_reply_to: str = Field(default='', max_length=320)
    from_email: str = Field(default='', max_length=320)
    to_email: str = Field(default='', max_length=320)
    subject: str = Field(default='', max_length=200)
    name: str = Field(default='', max_length=200)
    content: str = Field(default='', max_length=48000)
    teamspace_id: Optional[uuid.UUID] = None

    @field_validator('event_id', 'message_id', 'in_reply_to', 'from_email', 'to_email', 'subject', 'name')
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError('Mail headers must be single-line values.')
        return value

    @model_validator(mode='after')
    def complete(self):
        if self.occurred_at.tzinfo is None or self.occurred_at > utcnow() + timedelta(minutes=5): raise ValueError('Use a valid event time with timezone.')
        if self.type == 'incoming' and (not self.from_email or not self.to_email or not self.content.strip()): raise ValueError('Incoming mail requires sender, recipient and plain text content.')
        return self


def outbound_for(db, workspace_id, message_id, ticket_id=None):
    rows=list(db.scalars(select(TicketEmail).join(SupportTicket,SupportTicket.id==TicketEmail.ticket_id).where(SupportTicket.workspace_id==workspace_id,TicketEmail.message_id==message_id)))
    if not rows:raise HTTPException(404,'Outbound email not found.')
    if ticket_id:
        rows=[row for row in rows if row.ticket_id==ticket_id]
        if not rows:raise HTTPException(422,'The message does not match this ticket.')
    if len(rows)!=1:raise HTTPException(409,'This legacy Message-ID is ambiguous. Include the matching ticket ID.')
    return rows[0]


def apply(db, workspace_id, body, config):
    ws = db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update(key_share=True))
    if not ws: raise HTTPException(404, 'Workspace not found.')
    digest = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()
    prior = db.get(TicketMailEvent, (workspace_id, body.event_id))
    if prior:
        if prior.payload_hash != digest: raise HTTPException(409, 'This mail event ID was already used with different contents.')
        return {'accepted': True, 'duplicate': True, 'ticket_id': str(prior.ticket_id) if prior.ticket_id else None}
    message_key = hashlib.sha256(body.message_id.encode()).hexdigest() if body.type == 'incoming' else None
    previous_ledger = db.scalar(select(TicketMailEvent).where(TicketMailEvent.workspace_id == workspace_id, TicketMailEvent.message_key == message_key)) if message_key else None
    if previous_ledger and previous_ledger.ticket_id is None:
        return {'accepted': True, 'duplicate': True, 'ticket_id': None}
    outbound = None; ticket = None
    if body.type != 'incoming':
        outbound = outbound_for(db,workspace_id,body.message_id,body.ticket_id)
        if not outbound: raise HTTPException(404, 'Outbound email not found.')
        if body.ticket_id and body.ticket_id != outbound.ticket_id: raise HTTPException(422, 'The receipt does not match this ticket.')
        ticket = db.scalar(select(SupportTicket).where(SupportTicket.id == outbound.ticket_id).with_for_update().execution_options(populate_existing=True))
        outbound = db.scalar(select(TicketEmail).where(TicketEmail.id == outbound.id).with_for_update().execution_options(populate_existing=True))
        if outbound.status not in {'sent','sending','uncertain'}: raise HTTPException(409, 'This email has not been sent.')
        if not outbound.delivery_at or body.occurred_at > outbound.delivery_at:
            outbound.delivery_status = 'delivered' if body.type == 'delivered' else 'bounced'; outbound.delivery_at = body.occurred_at
            if outbound.status in {'sending','uncertain'}:
                outbound.status='sent';outbound.sent_at=outbound.sent_at or body.occurred_at;outbound.error=''
            outbound.revision += 1; outbound.updated_at = utcnow(); w.touch(ticket)
    else:
        try: address = s.email(body.from_email)
        except s.ScriptError as error: raise HTTPException(422, str(error)) from None
        allowed = {w.default_sender(config)[1].lower()} | {d.local_part.lower() + '@' + d.domain.lower() for d in settings.active(db, workspace_id, 'email-domains') if settings.valid_sender(d)}
        if body.to_email.lower() not in allowed: raise HTTPException(422, 'Incoming mail must target an enabled support sender.')
        previous = db.scalar(select(TicketInboundEmail).join(SupportTicket, SupportTicket.id == TicketInboundEmail.ticket_id).where(SupportTicket.workspace_id == workspace_id, TicketInboundEmail.message_id == body.message_id))
        if previous:
            if previous.sender != address or previous.recipient != body.to_email or previous.content != body.content or body.ticket_id and body.ticket_id != previous.ticket_id:
                raise HTTPException(409, 'This incoming Message-ID was already used.')
            ticket = db.get(SupportTicket, previous.ticket_id)
        else:
            if body.in_reply_to:
                linked = outbound_for(db,workspace_id,body.in_reply_to,body.ticket_id)
                if not linked: raise HTTPException(404, 'The referenced outbound email was not found.')
                if body.ticket_id and body.ticket_id != linked.ticket_id: raise HTTPException(422, 'The thread does not match this ticket.')
                ticket = db.get(SupportTicket, linked.ticket_id)
            elif body.ticket_id:
                ticket = db.scalar(select(SupportTicket).where(SupportTicket.workspace_id == workspace_id, SupportTicket.id == body.ticket_id))
                if not ticket: raise HTTPException(404, 'Ticket not found.')
            if ticket:
                ticket = db.scalar(select(SupportTicket).where(SupportTicket.id == ticket.id).with_for_update().execution_options(populate_existing=True))
                participants = {ticket.email.lower()}
                for email in db.scalars(select(TicketEmail).where(TicketEmail.ticket_id == ticket.id, TicketEmail.status == 'sent', TicketEmail.recipient_type == 'customer')):
                    participants.update(v.lower() for v in email.cc_recipients)
                if address.lower() not in participants: raise HTTPException(422, 'The sender is not a participant in this ticket.')
            else:
                space = body.teamspace_id or p.default_space(db, workspace_id)
                if not db.scalar(select(Teamspace.id).where(Teamspace.id == space, Teamspace.workspace_id == workspace_id)): raise HTTPException(422, 'Choose a teamspace in this workspace.')
                key = 'ticket-email:' + str(space) + ':' + hashlib.sha256(address.lower().encode()).hexdigest()
                person = db.scalar(select(SupportPerson).where(SupportPerson.workspace_id == workspace_id, SupportPerson.identity_key == key))
                if not person:
                    person = SupportPerson(workspace_id=workspace_id, teamspace_id=space, identity_key=key, name=body.name, email=address); db.add(person); db.flush()
                db.execute(insert(TicketCounter).values(workspace_id=workspace_id, next_number=1).on_conflict_do_nothing())
                counter = db.scalar(select(TicketCounter).where(TicketCounter.workspace_id == workspace_id).with_for_update()); number = counter.next_number; counter.next_number += 1
                domain = next((d for d in settings.active(db, workspace_id, 'email-domains') if d.local_part.lower() + '@' + d.domain.lower() == body.to_email.lower() and settings.valid_sender(d)), None)
                ticket = SupportTicket(workspace_id=workspace_id, teamspace_id=space, origin='email', action_node_id=uuid.uuid4(), execution_id=uuid.uuid4(), number=number,
                    reference='PTX-{:06d}'.format(number), agent_name='', subject=body.subject.strip() or '(No subject)', full_name=person.name, email=address, phone=person.phone, person_id=person.id, sender_domain_id=domain.id if domain else None)
                db.add(ticket); db.flush()
                db.add(TicketNote(ticket_id=ticket.id, author_name='Mail adapter', kind='event', content='Ticket opened by an incoming email.'))
            db.add(TicketInboundEmail(ticket_id=ticket.id, message_id=body.message_id, sender=address, recipient=body.to_email, subject=body.subject, content=body.content, created_at=body.occurred_at))
            db.add(UsageEvent(workspace_id=workspace_id, event_key='ticket-inbound:' + str(workspace_id) + ':' + hashlib.sha256(body.message_id.encode()).hexdigest(), category='manual_emails', source='email_inbound', teamspace_id=ticket.teamspace_id, units=1, created_at=body.occurred_at))
            if ticket.status in {'closed', 'resolved'}: ticket.status = 'open'
            w.touch(ticket)
    db.add(TicketMailEvent(workspace_id=workspace_id, event_id=body.event_id, payload_hash=digest, message_key=message_key if not previous_ledger else None, ticket_id=ticket.id, email_id=outbound.id if outbound else None, occurred_at=body.occurred_at))
    db.commit(); return {'accepted': True, 'duplicate': False, 'ticket_id': str(ticket.id)}


@router.post('/api/ticket-mail/{workspace_id}')
async def receive(workspace_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    config = request.app.state.settings
    secret = config.ticket_mail_webhook_secret.get_secret_value()
    if not secret: raise HTTPException(503, 'The ticket mail adapter is not enabled.')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 262144: raise HTTPException(413, 'Mail events are limited to 256 KiB.')
    timestamp = request.headers.get('x-petrix-mail-timestamp', '')
    try:
        if abs(utcnow().timestamp() - int(timestamp)) > 300: raise ValueError()
    except ValueError: raise HTTPException(401, 'A fresh signed mail event is required.') from None
    expected = hmac.new(secret.encode(), timestamp.encode() + b'.' + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get('x-petrix-mail-signature', '')): raise HTTPException(401, 'Invalid mail event signature.')
    try: body = MailEvent.model_validate_json(raw)
    except ValidationError: raise HTTPException(422, 'Check the normalized mail event fields.') from None
    return apply(db, workspace_id, body, config)
