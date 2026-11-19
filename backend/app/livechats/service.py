"""Permission-scoped livechat records. Runtime ownership remains in Inbox."""
import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select, or_, and_, false, func, case, cast, delete
from sqlalchemy.dialects.postgresql import JSONB, insert
from app.core.security import utcnow
from app.models import (Agent, AgentConversation, AgentMessage, ConversationPersona, LivechatNote,
    RecordLabels, SupportJob, SupportPerson, SupportTeam, TicketTag, TicketTagGroup, User, UsageEvent)
from app.workspace_settings import permissions as p
from app.teamspace import records as r
from app.inbox import service as inbox


def allowed(db, row, user_id, action='view'):
    teams = (row.live_context or {}).get('routing', {}).get('team_ids', [])
    space = r.record_space(db, 'chats', row)
    assigned = row.live_assigned_user_id
    if row.live_status == 'pending' and inbox.can_view(db, row, user_id): assigned = user_id
    return any(p.record_allowed(db, row.workspace_id, user_id, 'livechats', action,
        assigned, team, space) for team in teams or [None])


def require_list(db, workspace_id, user_id):
    scopes, _ = p.record_scopes(db, workspace_id, user_id, 'livechats', 'view')
    if not scopes and not any(scopes for _, scopes, _ in p.team_record_grants(db, workspace_id, user_id, 'livechats', 'view')):
        raise HTTPException(403, 'Your role cannot view Livechats.')


def visibility(db, workspace_id, user_id):
    spaces = p.accessible_spaces(db, workspace_id, user_id)
    space = func.coalesce(Agent.teamspace_id, p.default_space(db, workspace_id))
    teams_json = cast(AgentConversation.live_context['routing']['team_ids'], JSONB)
    team_match = lambda ids: or_(*[teams_json.contains([str(i)]) for i in ids]) if ids else false()
    scopes, teams = p.record_scopes(db, workspace_id, user_id, 'livechats', 'view')
    pool = and_(AgentConversation.live_status == 'pending', cast(AgentConversation.live_context['eligible_ids'], JSONB).contains([str(user_id)]))
    rules = []
    if 'all' in scopes: rules.append(space.in_([uuid.UUID(i) for i in spaces]))
    if 'own' in scopes: rules.append(and_(space.in_([uuid.UUID(i) for i in spaces]), or_(AgentConversation.live_assigned_user_id == user_id, pool)))
    if 'teams' in scopes: rules.append(and_(space.in_([uuid.UUID(i) for i in spaces]), or_(team_match(teams), pool)))
    for team, team_scopes, team_spaces in p.team_record_grants(db, workspace_id, user_id, 'livechats', 'view'):
        target = space.in_([uuid.UUID(i) for i in team_spaces])
        if team_scopes & {'all', 'teams'}: rules.append(and_(target, team_match([team])))
        if 'own' in team_scopes: rules.append(and_(target, or_(AgentConversation.live_assigned_user_id == user_id, pool)))
    return or_(*rules) if rules else false()


def get(db, workspace_id, identifier, user_id, action='view', lock=False):
    query = select(AgentConversation).where(AgentConversation.workspace_id == workspace_id,
        AgentConversation.id == identifier, AgentConversation.live_status != 'none')
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if not row or not allowed(db, row, user_id) or not allowed(db, row, user_id, action):
        raise HTTPException(404, 'Livechat not found or your role does not permit this operation.')
    return row


def status(row):
    if row.live_status == 'closed': return 'resolved' if row.live_display_status == 'resolved' else 'closed'
    if row.live_display_status in {'pending', 'overdue'}: return row.live_display_status
    return 'pending' if row.live_status == 'pending' else 'open'


def status_column():
    c = AgentConversation
    return case((c.live_status == 'closed', case((c.live_display_status == 'resolved', 'resolved'), else_='closed')),
        (c.live_display_status.in_(['pending', 'overdue']), c.live_display_status),
        (c.live_status == 'pending', 'pending'), else_='open')


def languages(row):
    routing = (row.live_context or {}).get('routing', {})
    primary = row.live_context.get('primary_language') or routing.get('language') or row.workflow_state.get('language', 'en')
    values = list(dict.fromkeys([primary, *row.live_context.get('languages', []), row.workflow_state.get('language', primary)]))
    return primary, values


def track_language(db, row):
    if row.live_status == 'none': return
    primary, values = languages(row)
    if row.live_context.get('languages') != values or row.live_context.get('primary_language') != primary:
        row.live_context = {**row.live_context, 'primary_language': primary, 'languages': values}
        db.commit()


def summary(db, row, user_id):
    person = db.get(SupportPerson, row.person_id) if row.person_id else None
    assigned = db.get(User, row.live_assigned_user_id) if row.live_assigned_user_id else None
    primary, used = languages(row)
    label = r.label_payload(db, row.workspace_id, 'chats', row)
    team_ids = row.live_context.get('routing', {}).get('team_ids', [])
    teams = list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == row.workspace_id,
        SupportTeam.id.in_([uuid.UUID(i) for i in team_ids])))) if team_ids else []
    from app.profile.service import resolve
    persona = resolve(db, row.workspace_id, 'chats', row.id, assigned.id) if assigned else None
    return {'id': str(row.id), 'revision': row.live_revision, 'agent_id': str(row.agent_id) if row.agent_id else None,
        'agent_name': row.agent_name, 'person': {'id': str(person.id), 'name': person.name or 'Unknown', 'email': person.email, 'phone': person.phone} if person else None,
        'assigned_user_id': str(assigned.id) if assigned else None, 'assigned_name': (assigned.full_name or assigned.email.split('@')[0]) if assigned else '',
        'alias_name': persona.name if persona else '',
        'teams': [{'id': str(t.id), 'name': t.name} for t in teams], 'tags': label['effective_tags'], 'groups': label['groups'],
        'status': status(row), 'runtime_status': row.live_status, 'channel': row.workflow_state.get('channel', 'webchat'),
        'description': row.live_description, 'created_at': row.created_at, 'updated_at': row.live_updated_at,
        'rating': row.workflow_state.get('csat_rating'), 'primary_language': primary, 'languages': used,
        'priority': bool(row.live_context.get('priority')), 'priority_type': row.live_context.get('priority_type', 'skip_queue'),
        'can_manage': allowed(db, row, user_id, 'manage'), 'can_export': allowed(db, row, user_id, 'export'),
        'can_transfer': allowed(db, row, user_id, 'manage') and p.allowed(db, row.workspace_id, 'transfer_livechats', user_id, teamspace_id=r.record_space(db, 'chats', row)),
        'can_open_inbox': allowed(db,row,user_id,'view'), 'can_complete': row.live_status == 'closed' or row.live_status == 'active' and row.live_assigned_user_id == user_id}


def options(db, workspace_id, user_id):
    from app.agent_schemas import LANGUAGES
    require_list(db, workspace_id, user_id)
    spaces = p.accessible_spaces(db, workspace_id, user_id)
    in_space = lambda item: str(getattr(item,'teamspace_id',None) or p.default_space(db, workspace_id)) in spaces
    fields = [f for f in r.field_definitions(db, workspace_id, 'conversation') if f.type == 'single_select_grouped']
    return {'agents': [{'id': a['id'], 'name': a['name']} for a in inbox.roster(db, workspace_id)],
        'teams': [{'id': str(t.id), 'name': t.name} for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == workspace_id, SupportTeam.archived.is_(False)))],
        'tags': [{'id': str(t.id), 'name': t.name, 'color': t.color} for t in db.scalars(select(TicketTag).where(TicketTag.workspace_id == workspace_id, TicketTag.archived.is_(False))) if in_space(t)],
        'groups': [{'id': str(t.id), 'name': t.name} for t in db.scalars(select(TicketTagGroup).where(TicketTagGroup.workspace_id == workspace_id, TicketTagGroup.archived.is_(False))) if in_space(t)],
        'fields': [{'key': f.key, 'name': f.name, 'options': f.options} for f in fields],
        'languages': [{'code': code, 'name': name} for code, name in LANGUAGES]}


def date_bounds(preset, start, end, timezone_name):
    try: zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError): raise HTTPException(422, 'Choose a valid IANA timezone.') from None
    today = utcnow().astimezone(zone).date()
    if preset == 'today': start = end = today
    elif preset == 'week': start, end = today - timedelta(days=today.weekday()), today
    elif preset == 'month': start, end = today.replace(day=1), today
    if start and end and end < start: raise HTTPException(422, 'The end date must follow the start date.')
    try:
        return (datetime.combine(start, time.min, zone).astimezone(timezone.utc) if start else None,
            datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc) if end else None)
    except (OverflowError, ValueError):
        raise HTTPException(422, 'Choose a date range supported by this timezone.') from None



def listing(db, workspace_id, user_id, filters, offset, limit):
    require_list(db, workspace_id, user_id)
    c = AgentConversation
    query = select(c).outerjoin(Agent, Agent.id == c.agent_id).outerjoin(SupportPerson, SupportPerson.id == c.person_id).where(
        c.workspace_id == workspace_id, c.live_status != 'none', visibility(db, workspace_id, user_id))
    if filters['q']:
        escaped = filters['q'].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = '%' + escaped + '%'
        query = query.where(or_(*[column.ilike(pattern, escape='\\') for column in [cast(c.id, __import__('sqlalchemy').String), SupportPerson.name, SupportPerson.email, SupportPerson.phone]]))
    if filters['agent']: query = query.where(c.live_assigned_user_id == filters['agent'])
    if filters['team']: query = query.where(cast(c.live_context['routing']['team_ids'], JSONB).contains([str(filters['team'])]))
    if filters['status']: query = query.where(status_column() == filters['status'])
    if filters['rating'] == 'none': query = query.where(c.workflow_state['csat_rating'].as_integer().is_(None))
    elif filters['rating']: query = query.where(c.workflow_state['csat_rating'].as_integer() == int(filters['rating']))
    start, end = date_bounds(filters['period'], filters['created_from'], filters['created_to'], filters['timezone'])
    if start: query = query.where(c.created_at >= start)
    if end: query = query.where(c.created_at < end)
    definition = next((f for f in r.field_definitions(db, workspace_id, 'conversation') if f.key == filters['custom_key'] and f.type == 'single_select_grouped'), None)
    if filters['custom_key'] and not definition: raise HTTPException(422, 'Select an active grouped single-select custom field.')
    values = []; total = 0
    query = query.order_by(c.created_at.desc(), c.id.desc()).execution_options(yield_per=100)
    for row in db.scalars(query):
        if not allowed(db, row, user_id): continue
        primary, used = languages(row)
        if filters['primary_language'] and filters['primary_language'] != primary: continue
        if filters['language'] and filters['language'] not in used: continue
        if definition and str((row.custom_fields.get(definition.key) or {}).get('value', definition.default_value) or '') != filters['custom_value']: continue
        if filters['tag'] or filters['group']:
            labels = r.label_payload(db, workspace_id, 'chats', row)
            if filters['tag'] and str(filters['tag']) not in {t['id'] for t in labels['effective_tags']}: continue
            group_ids = set(labels['group_ids'])
            for tag in db.scalars(select(TicketTag).where(TicketTag.workspace_id == workspace_id, TicketTag.id.in_([uuid.UUID(t['id']) for t in labels['effective_tags']]))): group_ids.update(r.tag_group_ids(db, tag))
            if filters['group'] and str(filters['group']) not in group_ids: continue
        if offset <= total < offset + limit: values.append(summary(db, row, user_id))
        total += 1
    return {'livechats': values, 'total': total, 'offset': offset, 'limit': limit}


def revision(row, expected):
    if row.live_revision != expected: raise HTTPException(409, 'This livechat changed. Reload before saving.')


def transfer(db, row, actor, body):
    revision(row, body.expected_revision)
    p.require(db, row.workspace_id, 'transfer_livechats', actor.user_id, teamspace_id=r.record_space(db, 'chats', row))
    if row.live_status == 'closed': raise HTTPException(409, 'Completed livechats cannot be transferred. Start a new human handoff.')
    from app.teamspace.assignment import policy, pick
    # Same occupancy lock as automatic assignment and Inbox claim.
    inbox.assignment(db, row.workspace_id, lock=True)
    teams = list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == row.workspace_id,
        SupportTeam.id.in_(body.team_ids), SupportTeam.archived.is_(False)))) if body.team_ids else []
    if len(teams) != len(body.team_ids): raise HTTPException(422, 'Select active teams in this workspace.')
    routing = {**row.live_context.get('routing', {}), 'assignment_mode': 'specific', 'team_ids': [str(t.id) for t in teams], 'user_ids': [str(body.user_id)] if body.user_id else []}
    routing.setdefault('language', row.workflow_state.get('language', 'en')); routing.setdefault('assign_by_language', False)
    people = inbox.eligible(db, row.workspace_id, routing, space_id=r.record_space(db, 'chats', row))
    ops = policy(db, row.workspace_id, r.record_space(db, 'chats', row), True)
    if ops.assignment.get('algorithm', 1) < 2:
        legacy = inbox.assignment(db, row.workspace_id)
        ops.assignment = {**ops.assignment, 'mode': legacy.mode, 'limit': legacy.max_active_chats}
    if body.user_id:
        target = next((a for a in people if a['id'] == str(body.user_id) and (not teams or set(routing['team_ids']) & set(a['team_ids']))), None)
        if not target or not target['available']: raise HTTPException(422, 'Select an available eligible agent. The agent must belong to a support team and have permission in this teamspace.')
        ignore = row.live_context.get('priority') and row.live_context.get('priority_type') == 'ignore_limit'
        if not ignore and target['active_chats'] >= ops.assignment.get('limit', inbox.assignment(db, row.workspace_id).max_active_chats) and row.live_assigned_user_id != body.user_id:
            raise HTTPException(409, 'This agent has reached the assignment limit.')
        chosen = body.user_id
    else:
        if not people: raise HTTPException(422, 'These teams have no eligible agents for this conversation.')
        chosen_id = pick(db, row.workspace_id, ops, people, bool(row.live_context.get('priority')), row.live_context.get('priority_type'))
        chosen = uuid.UUID(chosen_id) if chosen_id else None
    row.live_context = {**row.live_context, 'routing': routing, 'assigned_id': str(chosen) if chosen else None, 'eligible_ids': [a['id'] for a in people]}
    row.live_assigned_user_id = chosen; row.live_status = 'active' if chosen else 'pending'
    row.live_display_status = ''; row.live_revision += 1; row.live_updated_at = utcnow()
    if chosen: inbox.ensure_profile(db, row.workspace_id, chosen).last_assigned_at = utcnow()
    inbox.audit(db, row, actor.user_id, 'transferred'); db.commit()


def note_payload(note):
    return {key: str(getattr(note, key)) if key in {'id', 'author_id'} and getattr(note, key) else getattr(note, key)
        for key in ['id', 'author_id', 'author_name', 'content', 'color', 'revision', 'created_at', 'updated_at']}


def save_note(db, row, actor, body, identifier=None):
    if identifier:
        note = db.scalar(select(LivechatNote).where(LivechatNote.id == identifier, LivechatNote.conversation_id == row.id).with_for_update().execution_options(populate_existing=True))
        if not note: raise HTTPException(404, 'Note not found.')
        if note.revision != body.expected_revision: raise HTTPException(409, 'This note changed. Reload before editing.')
        note.content = body.content; note.color = body.color; note.revision += 1; note.updated_at = utcnow()
    else:
        if db.scalar(select(func.count()).select_from(LivechatNote).where(LivechatNote.conversation_id == row.id)) >= 100:
            raise HTTPException(409, 'A livechat supports up to 100 internal notes.')
        user = db.get(User, actor.user_id)
        note = LivechatNote(conversation_id=row.id, author_id=actor.user_id, author_name=user.full_name or user.email.split('@')[0], content=body.content, color=body.color)
        db.add(note)
    inbox.audit(db, row, actor.user_id, 'note_saved'); db.commit(); return note_payload(note)


def preserve_usage(db, workspace_id, ids):
    from app.workspace_settings.usage import message_source_query
    remaining = message_source_query(workspace_id, exclude_ids=ids).subquery()
    selected = message_source_query(workspace_id, ids=ids).subquery()
    for item in db.execute(select(selected).where(selected.c.identity.not_in(select(remaining.c.identity)))):
        data = item._mapping
        db.execute(insert(UsageEvent).values(id=uuid.uuid4(), workspace_id=workspace_id, event_key='deleted-message:' + data['identity'],
            category=data['category'], source=data['source'], agent_id=data['agent_id'], user_id=data['user_id'], teamspace_id=data['teamspace_id'],
            team_id=uuid.UUID(data['team_id']) if data['team_id'] else None, units=data['units'], created_at=data['at']).on_conflict_do_nothing())


def remove(db, workspace_id, actor, body):
    # Validate the complete selection before any destructive action.
    rows = [get(db, workspace_id, i, actor.user_id, 'manage', True) for i in sorted(body.ids)]
    for row in rows: revision(row, body.revisions[row.id])
    preserve_usage(db, workspace_id, body.ids)
    from app.models import InboxAttachment
    db.execute(delete(InboxAttachment).where(InboxAttachment.workspace_id==workspace_id,InboxAttachment.kind=='chats',InboxAttachment.record_id.in_(body.ids)))
    jobs = list(db.scalars(select(SupportJob).where(SupportJob.workspace_id == workspace_id, SupportJob.target_kind == 'chats').with_for_update()))
    targets = set(map(str, body.ids))
    for job in jobs:
        if str(job.target_id) in targets or targets & set(job.payload.get('ids', [])):
            job.status = 'failed'; job.error = 'A source livechat was deleted.'; job.file_data = None; job.output = {}; job.finished_at = utcnow(); job.lease_id = None
    db.execute(delete(RecordLabels).where(RecordLabels.workspace_id == workspace_id, RecordLabels.kind == 'chats', RecordLabels.record_id.in_(body.ids)))
    db.execute(delete(ConversationPersona).where(ConversationPersona.workspace_id == workspace_id, ConversationPersona.kind == 'chats', ConversationPersona.record_id.in_(body.ids)))
    for row in rows:
        inbox.audit(db, row, actor.user_id, 'deleted'); db.delete(row)
    db.commit()
    return {'deleted': len(rows)}
