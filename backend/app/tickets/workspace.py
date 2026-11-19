"""Human ticket creation, repository queries and private staff annotations."""
from copy import deepcopy
from email.utils import parseaddr
import hashlib
import json
import uuid
from fastapi import HTTPException
from sqlalchemy import select, cast, String, or_, delete, func
from sqlalchemy.dialects.postgresql import insert, JSONB
from app import agent_service
from app.core.security import utcnow
from app.models import (SupportTicket, TicketEmail, TicketInboundEmail, TicketNote, TicketCounter,
    TicketEmailDomain, SupportPerson, SupportTeam, Teamspace, User, Workspace, RecordLabels,
    ConversationPersona, SupportJob, UsageEvent, ConversationField, AgentConversation)
from app.teamspace import records as r
from app.workspace_settings import permissions as p
from app.tickets import service as s, settings


def default_sender(config):
    name, address = parseaddr(config.smtp_from)
    if not address or '@' not in address or any(ord(c) < 33 or ord(c) == 127 for c in address) or any(c in address for c in '<>,;'):
        raise HTTPException(503, 'The default support sender is not configured correctly.')
    return name or 'Petrix AI', address


def sender(db, workspace_id, identifier, config):
    if identifier:
        domain = db.get(TicketEmailDomain, identifier)
        if not domain or domain.workspace_id != workspace_id or not settings.valid_sender(domain):
            raise HTTPException(422, 'Choose an enabled, verified sender in this workspace.')
        return domain.from_name, domain.local_part + '@' + domain.domain, domain
    name, address = default_sender(config)
    return name, address, None


def legacy(db, workspace_id, user_id):
    member = p.membership(db, workspace_id, user_id)
    return bool(member and agent_service.can_manage(db, workspace_id, member.role))


def permitted(db, row, user_id, action='view'):
    return bool(db.scalar(select(SupportTicket.id).where(SupportTicket.id == row.id,
        s.access_filter(db, row.workspace_id, user_id, legacy(db, row.workspace_id, user_id), action))))


def require_list(db, workspace_id, user_id, action='view'):
    if not p.has_custom(db, workspace_id, user_id): return
    scopes, _ = p.record_scopes(db, workspace_id, user_id, 'tickets', action)
    if not scopes and not any(scopes for _, scopes, _ in p.team_record_grants(db, workspace_id, user_id, 'tickets', action)):
        raise HTTPException(403, 'Your role cannot ' + action + ' tickets.')


def team_ids(row):
    return list(dict.fromkeys((row.assigned_team_ids or []) + ([str(row.assignee_id)] if row.assignee_type == 'team' and row.assignee_id else [])))


def assignment(db, workspace_id, space_id, user_id, ids):
    teams = list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == workspace_id,
        SupportTeam.id.in_(ids), SupportTeam.archived.is_(False)))) if ids else []
    if len(teams) != len(ids): raise HTTPException(422, 'Choose active teams in this workspace.')
    if any(t.teamspace_ids is not None and str(space_id) not in t.teamspace_ids for t in teams):
        raise HTTPException(422, 'A selected team does not have access to this teamspace.')
    if user_id:
        try: name, _ = s.assignee(db, workspace_id, 'user', user_id)
        except s.ScriptError as error: raise HTTPException(422, str(error)) from None
        if str(space_id) not in p.accessible_spaces(db, workspace_id, user_id): raise HTTPException(422, 'This agent cannot access the ticket teamspace.')
        return 'user', user_id, name
    if len(teams) == 1: return 'team', teams[0].id, teams[0].name
    return 'none', None, 'Unassigned'


def visible_person_ticket(db, workspace_id, user_id, person):
    return db.scalar(select(SupportTicket).where(SupportTicket.person_id == person.id,
        s.access_filter(db, workspace_id, user_id, legacy(db, workspace_id, user_id))).order_by(SupportTicket.created_at.desc()).limit(1))


def person_access(db, workspace_id, user_id, person):
    space = r.record_space(db, 'people', person)
    if r.person_visible(db, workspace_id, person, user_id) and all(p.allowed(db, workspace_id, 'view_people_' + field, user_id, teamspace_id=space) for field in ['name','email']): return True
    return visible_person_ticket(db, workspace_id, user_id, person) is not None


def people(db, workspace_id, user_id, q):
    query = select(SupportPerson).where(SupportPerson.workspace_id == workspace_id)
    if q: query = query.where(or_(SupportPerson.name.icontains(q, autoescape=True), SupportPerson.email.icontains(q, autoescape=True)))
    result = []
    for person in db.scalars(query.order_by(SupportPerson.name, SupportPerson.id)):
        if person_access(db, workspace_id, user_id, person):
            public = visible_person_ticket(db, workspace_id, user_id, person)
            can_read = lambda field: p.allowed(db,workspace_id,'view_people_'+field,user_id,teamspace_id=r.record_space(db,'people',person))
            result.append({'id': str(person.id), 'name': (person.name if can_read('name') else public.full_name if public else '') or 'Unknown', 'email': person.email if can_read('email') else public.email if public else ''})
        if len(result) == 40: break
    return result


def create(db, workspace_id, actor, body, config):
    require_list(db, workspace_id, actor.user_id, 'manage')
    # Workspace lock covers the request ledger, person registration and ticket counter.
    db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update(key_share=True))
    digest = hashlib.sha256(json.dumps(body.model_dump(mode='json'), sort_keys=True, allow_nan=False).encode()).hexdigest()
    prior = db.scalar(select(SupportTicket).where(SupportTicket.workspace_id == workspace_id, SupportTicket.client_request_id == body.client_request_id))
    if prior:
        if prior.creator_id != actor.user_id or prior.create_payload_hash != digest: raise HTTPException(409, 'This creation request ID was already used with different contents.')
        return s.owned(db, workspace_id, actor.user_id, legacy(db, workspace_id, actor.user_id), prior.id)
    conversation = r.record(db, workspace_id, 'chats', body.conversation_id, actor.user_id) if body.conversation_id else None
    space = r.record_space(db, 'chats', conversation) if conversation else body.teamspace_id or p.default_space(db, workspace_id)
    if body.teamspace_id and body.teamspace_id != space: raise HTTPException(422, 'Use the linked conversation teamspace.')
    if str(space) not in p.accessible_spaces(db, workspace_id, actor.user_id): raise HTTPException(403, 'This teamspace is not available to your account.')
    sender(db, workspace_id, body.sender_domain_id, config)
    kind, assigned, assigned_name = assignment(db, workspace_id, space, body.user_id, body.team_ids)
    person = db.get(SupportPerson, body.person_id) if body.person_id else None
    linked_person = db.get(SupportPerson, conversation.person_id) if conversation and conversation.person_id else None
    if body.person_id and (not person or person.workspace_id != workspace_id or not (linked_person and linked_person.id == person.id) and not person_access(db, workspace_id, actor.user_id, person)):
        raise HTTPException(404, 'Person not found.')
    person = person or (linked_person if linked_person and linked_person.email else None)
    public = visible_person_ticket(db, workspace_id, actor.user_id, person) if person else None
    can_read = lambda field: bool(person and (linked_person and linked_person.id==person.id or p.allowed(db,workspace_id,'view_people_'+field,actor.user_id,teamspace_id=r.record_space(db,'people',person))))
    contact_name = (person.name if can_read('name') else public.full_name if public else '') if person else body.name.strip()
    contact_email = (person.email if can_read('email') else public.email if public else '') if person else body.email.strip()
    try: address = s.email(contact_email)
    except s.ScriptError as error: raise HTTPException(422, str(error)) from None
    if body.email and person and body.email.strip().lower() != address.lower(): raise HTTPException(422, 'Use the selected person’s email address.')
    if r.is_banned(person): raise HTTPException(403, 'This person is currently banned.')
    if not person:
        key = 'ticket-email:' + str(space) + ':' + hashlib.sha256(address.lower().encode()).hexdigest()
        person = db.scalar(select(SupportPerson).where(SupportPerson.workspace_id == workspace_id, SupportPerson.identity_key == key))
        if not person:
            person = SupportPerson(workspace_id=workspace_id, teamspace_id=space, identity_key=key, name=body.name.strip(), email=address)
            db.add(person); db.flush()
        if r.is_banned(person): raise HTTPException(403, 'This person is currently banned.')
    copied = deepcopy(conversation.custom_fields or {}) if conversation else {}
    definitions = {f.key: f for f in r.field_definitions(db, workspace_id, 'conversation')}
    for key, value in body.custom_fields.items():
        field = definitions.get(key)
        if not field: raise HTTPException(422, 'Choose an active conversation custom field.')
        try: s.checked_field(db, workspace_id, field.id, value)
        except s.ScriptError as error: raise HTTPException(422, str(error)) from None
        from app.custom_fields.service import option_snapshot
        copied[key] = option_snapshot(field, value, 'manual_ticket')
    if len(json.dumps(copied, allow_nan=False).encode()) > 131072: raise HTTPException(422, 'Ticket custom fields exceed 128 KiB.')
    db.execute(insert(TicketCounter).values(workspace_id=workspace_id, next_number=1).on_conflict_do_nothing())
    counter = db.scalar(select(TicketCounter).where(TicketCounter.workspace_id == workspace_id).with_for_update())
    number = counter.next_number; counter.next_number += 1
    ticket = SupportTicket(workspace_id=workspace_id, teamspace_id=space, origin='manual', client_request_id=body.client_request_id,
        create_payload_hash=digest, creator_id=actor.user_id, action_node_id=uuid.uuid4(), execution_id=body.client_request_id,
        number=number, reference='PTX-{:06d}'.format(number), agent_name='', subject=body.subject.strip(), description=body.description,
        person_id=person.id, full_name=contact_name, email=address, phone=person.phone if can_read('phone') else public.phone if public else '', conversation_id=conversation.id if conversation else None,
        sender_domain_id=body.sender_domain_id, assignee_type=kind, assignee_id=assigned, assignee_name=assigned_name,
        assigned_team_ids=list(map(str, body.team_ids)), custom_fields=copied)
    db.add(ticket); db.flush()
    if not permitted(db, ticket, actor.user_id, 'manage') or not permitted(db, ticket, actor.user_id):
        raise HTTPException(403, 'Assign the ticket within your view and manage permissions.')
    user = db.get(User, actor.user_id)
    db.add(TicketNote(ticket_id=ticket.id, author_id=actor.user_id, author_name=user.full_name or user.email, kind='event', content='Ticket created. No email has been sent.'))
    db.commit()
    return ticket


def creator(db, row):
    first = db.scalar(select(TicketEmail).where(TicketEmail.ticket_id == row.id, TicketEmail.recipient_type == 'customer', TicketEmail.status == 'sent').order_by(TicketEmail.sent_at, TicketEmail.id).limit(1))
    received = db.scalar(select(TicketInboundEmail).where(TicketInboundEmail.ticket_id == row.id).order_by(TicketInboundEmail.created_at, TicketInboundEmail.id).limit(1))
    if received and (not first or received.created_at < first.sent_at): return {'key': 'person:' + str(row.person_id), 'name': row.full_name or row.email, 'type': 'person'}
    if first:
        user = db.get(User, first.actor_id) if first.actor_id else None
        return {'key': 'user:' + str(user.id) if user else 'workflow', 'name': (user.full_name or user.email) if user else row.agent_name or 'AI workflow', 'type': 'agent' if user else 'workflow'}
    return {'key': '', 'name': 'No email sent', 'type': 'none'}


def email_state(email):
    if not email: return 'pending'
    from datetime import timedelta
    if email.status=='sending' and email.updated_at<utcnow()-timedelta(minutes=2):return 'failed_to_send'
    if email.delivery_status == 'delivered': return 'delivered'
    if email.delivery_status == 'bounced': return 'failed_to_deliver'
    return {'queued': 'pending', 'sending': 'sending', 'sent': 'sent', 'failed': 'failed_to_send', 'uncertain': 'failed_to_send'}[email.status]


def summary(db, row, user_id, config):
    value = s.payload(row)
    label = r.label_payload(db, row.workspace_id, 'tickets', row)
    teams = list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == row.workspace_id, SupportTeam.id.in_([uuid.UUID(i) for i in team_ids(row)]))))
    latest = db.scalar(select(TicketEmail).where(TicketEmail.ticket_id == row.id, TicketEmail.recipient_type == 'customer').order_by(TicketEmail.created_at.desc(), TicketEmail.id.desc()).limit(1))
    domain = db.get(TicketEmailDomain, row.sender_domain_id) if row.sender_domain_id else None
    address = domain.local_part + '@' + domain.domain if domain else default_sender(config)[1]
    value.update({'display_status': 'pending' if row.status == 'in_progress' else row.status, 'short_id': row.reference,
        'person': {'id': str(row.person_id), 'name': row.full_name or 'Unknown', 'email': row.email, 'phone': row.phone},
        'assigned_user_id': str(row.assignee_id) if row.assignee_type == 'user' else None,
        'teams': [{'id': str(t.id), 'name': t.name} for t in teams], 'tags': row.tags or label['effective_tags'], 'active_tags': label['effective_tags'], 'groups': label['groups'],
        'created_by': creator(db, row), 'email_status': email_state(latest), 'sender_email': address,
        'can_manage': permitted(db, row, user_id, 'manage'), 'can_export': permitted(db, row, user_id, 'export'),
        'can_transfer': permitted(db, row, user_id, 'manage') and p.allowed(db, row.workspace_id, 'transfer_tickets', user_id, teamspace_id=r.record_space(db, 'tickets', row))})
    return value


def options(db, workspace_id, user_id, config):
    from app.inbox.service import roster
    from app.custom_fields.service import payload
    require_list(db, workspace_id, user_id)
    spaces = p.accessible_spaces(db, workspace_id, user_id)
    fields = r.field_definitions(db, workspace_id, 'conversation')
    name, address = default_sender(config)
    visible = list(db.scalars(select(SupportTicket).where(s.access_filter(db, workspace_id, user_id, legacy(db, workspace_id, user_id)))))
    creators = {v['key']: v for row in visible for v in [creator(db, row)] if v['key']}
    try: require_list(db, workspace_id, user_id, 'manage'); can_create = True
    except HTTPException: can_create = False
    return {'users': roster(db, workspace_id), 'teams': [{'id': str(t.id), 'name': t.name} for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == workspace_id, SupportTeam.archived.is_(False)))],
        'teamspaces': [{'id': str(t.id), 'name': t.name} for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id == workspace_id, Teamspace.id.in_([uuid.UUID(i) for i in spaces])).order_by(Teamspace.is_default.desc(),Teamspace.name))],
        'tags': [settings.payload(t) for t in settings.active(db, workspace_id, 'tags')], 'groups': [settings.payload(g) for g in settings.active(db, workspace_id, 'groups')],
        'custom_fields': [payload(f) for f in fields], 'creators': list(creators.values()), 'can_create': can_create,
        'default_sender': {'name': name, 'email': address},
        'email_domains': [{'id': str(d.id), 'from_email': d.local_part + '@' + d.domain, 'verified': settings.valid_sender(d)} for d in settings.active(db, workspace_id, 'email-domains') if settings.valid_sender(d)]}


def listing(db, workspace_id, user_id, filters, offset, limit, config):
    require_list(db, workspace_id, user_id)
    query = select(SupportTicket).where(s.access_filter(db, workspace_id, user_id, legacy(db, workspace_id, user_id)))
    if filters['q']:
        query = query.where(or_(*[c.icontains(filters['q'], autoescape=True) for c in [cast(SupportTicket.id, String), SupportTicket.reference, SupportTicket.subject, SupportTicket.full_name, SupportTicket.email, SupportTicket.phone]]))
    if filters['status']: query = query.where(SupportTicket.status == ('in_progress' if filters['status'] == 'pending' else filters['status']))
    if filters['agent']: query = query.where(SupportTicket.assignee_type == 'user', SupportTicket.assignee_id == filters['agent'])
    if filters['team']: query = query.where(or_(cast(SupportTicket.assigned_team_ids, JSONB).contains([str(filters['team'])]), (SupportTicket.assignee_type == 'team') & (SupportTicket.assignee_id == filters['team'])))
    from app.livechats.service import date_bounds
    start, end = date_bounds(filters['period'], filters['created_from'], filters['created_to'], filters['timezone'])
    if start: query = query.where(SupportTicket.created_at >= start)
    if end: query = query.where(SupportTicket.created_at < end)
    if filters['custom_key'] and not any(f.key == filters['custom_key'] for f in r.field_definitions(db, workspace_id, 'conversation')):
        raise HTTPException(422, 'Choose an active conversation custom field.')
    rows = []; total = 0
    for row in db.scalars(query.order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc()).execution_options(yield_per=100)):
        if not r.matches_filters(db, workspace_id, 'tickets', row, filters['tag'], filters['group'], filters['custom_key'] or None, filters['custom_value']): continue
        if filters['created_by'] and creator(db, row)['key'] != filters['created_by']: continue
        if filters['email_status']:
            latest = db.scalar(select(TicketEmail).where(TicketEmail.ticket_id == row.id, TicketEmail.recipient_type == 'customer').order_by(TicketEmail.created_at.desc(), TicketEmail.id.desc()).limit(1))
            if email_state(latest) != filters['email_status']: continue
        if offset <= total < offset + limit: rows.append(summary(db, row, user_id, config))
        total += 1
    return {'tickets': rows, 'total': total, 'offset': offset, 'limit': limit, 'has_more': offset + limit < total, 'can_manage': legacy(db, workspace_id, user_id)}


def revision(row, expected):
    if row.revision != expected: raise HTTPException(409, 'This ticket changed. Reload before saving.')


def touch(row):
    row.revision += 1; row.updated_at = utcnow()


def event(db, row, user_id, content):
    user = db.get(User, user_id)
    db.add(TicketNote(ticket_id=row.id, author_id=user_id, author_name=user.full_name or user.email, kind='event', content=content))


def save_note(db, row, user_id, body, identifier=None):
    if identifier:
        note = db.scalar(select(TicketNote).where(TicketNote.ticket_id == row.id, TicketNote.id == identifier, TicketNote.kind == 'note').with_for_update().execution_options(populate_existing=True))
        if not note: raise HTTPException(404, 'Internal note not found.')
        revision(note, body.expected_revision)
        note.content = body.content; note.color = body.color; note.updated_at = utcnow(); note.revision += 1
    else:
        count = db.scalar(select(func.count()).select_from(TicketNote).where(TicketNote.ticket_id == row.id, TicketNote.kind == 'note'))
        if count >= 100: raise HTTPException(409, 'A ticket supports up to 100 internal notes.')
        user = db.get(User, user_id)
        db.add(TicketNote(ticket_id=row.id, author_id=user_id, author_name=user.full_name or user.email, content=body.content, color=body.color))
    touch(row); db.commit()


def remove(db, workspace_id, actor, body):
    rows = [s.owned(db, workspace_id, actor.user_id, legacy(db, workspace_id, actor.user_id), i, True, 'manage') for i in sorted(body.ids)]
    for row in rows:
        revision(row, body.revisions[row.id])
        if not permitted(db, row, actor.user_id): raise HTTPException(404, 'Ticket not found.')
    emails = list(db.scalars(select(TicketEmail).where(TicketEmail.ticket_id.in_(body.ids)).with_for_update()))
    if any(e.status == 'sending' for e in emails): raise HTTPException(409, 'An email is being sent. Wait for delivery to finish before deleting this ticket.')
    by_id = {row.id: row for row in rows}
    for email in emails:
        if email.status != 'sent' or not email.sent_at: continue
        row = by_id[email.ticket_id]
        db.execute(insert(UsageEvent).values(id=uuid.uuid4(), workspace_id=workspace_id, event_key='deleted-ticket-email:' + str(email.id),
            category='manual_emails' if email.kind == 'reply' else 'ai_emails', source='email', user_id=email.actor_id if email.kind == 'reply' else row.creator_id,
            teamspace_id=r.record_space(db, 'tickets', row), team_id=row.assignee_id if row.assignee_type == 'team' else None, units=1, created_at=email.sent_at).on_conflict_do_nothing())
    from app.models import InboxAttachment
    db.execute(delete(InboxAttachment).where(InboxAttachment.workspace_id==workspace_id,InboxAttachment.kind=='tickets',InboxAttachment.record_id.in_(body.ids)))
    targets = set(map(str, body.ids))
    for job in db.scalars(select(SupportJob).where(SupportJob.workspace_id == workspace_id, SupportJob.target_kind == 'tickets').with_for_update()):
        if str(job.target_id) in targets or targets & set(job.payload.get('ids', [])):
            job.status = 'failed'; job.error = 'A source ticket was deleted.'; job.file_data = None; job.output = {}; job.finished_at = utcnow(); job.lease_id = None
    db.execute(delete(RecordLabels).where(RecordLabels.workspace_id == workspace_id, RecordLabels.kind == 'tickets', RecordLabels.record_id.in_(body.ids)))
    db.execute(delete(ConversationPersona).where(ConversationPersona.workspace_id == workspace_id, ConversationPersona.kind == 'tickets', ConversationPersona.record_id.in_(body.ids)))
    for row in rows: db.delete(row)
    db.commit(); return {'deleted': len(rows)}
