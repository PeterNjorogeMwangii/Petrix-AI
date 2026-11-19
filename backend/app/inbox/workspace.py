"""Unified, permission-scoped Inbox lists and durable personal/shared views."""
from datetime import datetime, timezone
import uuid
from fastapi import HTTPException
from sqlalchemy import select, or_, and_, func
from app.core.security import utcnow
from app.models import (Agent, AgentConversation, AgentMessage, SupportTicket, TicketEmail,
    TicketInboundEmail, SupportPerson, InboxView, InboxPreference, InboxAttachment, Teamspace,
    User, LivechatNote, TicketNote, WorkspaceBilling)
from app.workspace_settings import permissions as p
from app.teamspace import records as r, configuration as c
from app.inbox.workspace_schemas import ViewFilters

DEFAULT_VIEWS = {'mine': 'My inbox', 'conversations': 'Conversations', 'livechats': 'Active livechats',
    'tickets': 'Active tickets', 'ai': 'Active AI chats', 'test': 'Test conversations'}


def view_payload(row, user_id):
    return {'id': str(row.id), 'key': 'view:' + str(row.id), 'name': row.name, 'scope': row.scope,
        'teamspace_id': str(row.teamspace_id), 'filters': row.filters, 'revision': row.revision,
        'can_manage': row.owner_id == user_id}


def views(db, workspace_id, user_id):
    spaces = [uuid.UUID(i) for i in p.accessible_spaces(db, workspace_id, user_id)]
    return list(db.scalars(select(InboxView).where(InboxView.workspace_id == workspace_id,
        InboxView.teamspace_id.in_(spaces), or_(InboxView.owner_id == user_id, InboxView.scope == 'teamspace'))
        .order_by(InboxView.created_at, InboxView.id)))


def shared_manage(db, workspace_id, user_id, space_id):
    return any(p.allowed(db, workspace_id, key, user_id, teamspace_id=space_id)
        for key in ['manage_livechats_all', 'manage_tickets_all', 'manage_ai_agents'])


def preferences(db, workspace_id, user_id):
    row = db.get(InboxPreference, (workspace_id, user_id))
    available = set(DEFAULT_VIEWS) | {'view:' + str(v.id) for v in views(db, workspace_id, user_id)}
    return {'revision': row.revision if row else 0, 'pinned': [v for v in row.pinned if v in available] if row else [],
        'sort': row.sort if row else '', 'direction': row.direction if row else 'desc'}


def options(db, workspace_id, user_id, settings):
    from app.inbox.service import roster
    from app.agent_schemas import LANGUAGES
    spaces = list(db.scalars(select(Teamspace).where(Teamspace.workspace_id == workspace_id,
        Teamspace.id.in_([uuid.UUID(i) for i in p.accessible_spaces(db, workspace_id, user_id)]))))
    limit = c.limit(db, workspace_id, 'inbox_panels')
    rank = c.plan_rank(db, workspace_id, settings)
    maximum = min(5, max(1, int(limit))) if isinstance(limit, (int, float)) and not isinstance(limit, bool) else (1 if rank == 0 else 2 if rank == 1 else 3 if rank == 2 else 5)
    from app.tickets.settings import active
    return {'defaults': [{'key': k, 'name': v} for k, v in DEFAULT_VIEWS.items()],
        'views': [dict(view_payload(v, user_id), can_manage=v.owner_id == user_id or v.scope == 'teamspace' and shared_manage(db, workspace_id, user_id, v.teamspace_id)) for v in views(db, workspace_id, user_id)],
        'preferences': preferences(db, workspace_id, user_id), 'max_panels': maximum,
        'teamspaces': [{'id': str(t.id), 'name': t.name, 'is_default': t.is_default,
            'can_share_views': shared_manage(db, workspace_id, user_id, t.id)} for t in spaces],
        'agents': [{k: v for k, v in a.items() if k != 'last_assigned_at'} for a in roster(db, workspace_id)],
        'tags': [{'id': str(t.id), 'name': t.name} for t in active(db, workspace_id, 'tags')],
        'groups': [{'id': str(t.id), 'name': t.name} for t in active(db, workspace_id, 'groups')],
        'teams': [{'id': str(t.id), 'name': t.name} for t in db.scalars(select(__import__('app.models', fromlist=['SupportTeam']).SupportTeam).where(__import__('app.models', fromlist=['SupportTeam']).SupportTeam.workspace_id == workspace_id, __import__('app.models', fromlist=['SupportTeam']).SupportTeam.archived.is_(False)))],
        'languages': [{'code': code, 'name': name} for code, name in LANGUAGES]}


def save_view(db, workspace_id, user_id, body, identifier=None):
    if str(body.teamspace_id) not in p.accessible_spaces(db, workspace_id, user_id): raise HTTPException(404, 'Teamspace not found.')
    db.scalar(select(Teamspace).where(Teamspace.id == body.teamspace_id).with_for_update())
    row = next((v for v in views(db, workspace_id, user_id) if v.id == identifier), None) if identifier else None
    if identifier and not row: raise HTTPException(404, 'View not found.')
    if row:
        if row.owner_id != user_id and not (row.scope == 'teamspace' and shared_manage(db, workspace_id, user_id, row.teamspace_id)): raise HTTPException(403, 'You cannot edit this view.')
        if row.revision != body.expected_revision: raise HTTPException(409, 'This view changed. Reload before editing.')
        if row.teamspace_id != body.teamspace_id or row.scope != body.scope: raise HTTPException(422, 'A saved view keeps its original visibility and teamspace. Create a separate view to change them.')
    elif (db.scalar(select(func.count()).select_from(InboxView).where(InboxView.workspace_id == workspace_id, InboxView.owner_id == user_id)) or 0) >= 50:
        raise HTTPException(409, 'You can create up to 50 views per workspace.')
    if body.filters.agent_id and not p.membership(db, workspace_id, body.filters.agent_id): raise HTTPException(422, 'Select a live agent in this workspace.')
    if body.filters.team_id:
        from app.models import SupportTeam
        team=db.get(SupportTeam,body.filters.team_id)
        if not team or team.workspace_id!=workspace_id or team.archived:raise HTTPException(422,'Select an active team in this workspace.')
    if body.scope == 'teamspace' and not shared_manage(db, workspace_id, user_id, body.teamspace_id): raise HTTPException(403, 'Managing shared views requires teamspace administration or manage-all conversation permission.')
    c.validate_labels(db, workspace_id, [body.filters.tag_id] if body.filters.tag_id else [], [body.filters.group_id] if body.filters.group_id else [])
    if not row:
        row = InboxView(workspace_id=workspace_id, teamspace_id=body.teamspace_id, owner_id=user_id, scope=body.scope, name=body.name, filters={}); db.add(row)
    else: row.revision += 1
    row.name = body.name; row.filters = body.filters.model_dump(mode='json'); row.updated_at = utcnow(); db.commit()
    return view_payload(row, user_id)


def save_preferences(db, workspace_id, user_id, body):
    from app.models import Membership
    db.scalar(select(Membership).where(Membership.workspace_id == workspace_id, Membership.user_id == user_id).with_for_update())
    row = db.get(InboxPreference, (workspace_id, user_id), populate_existing=True)
    if (row.revision if row else 0) != body.expected_revision: raise HTTPException(409, 'Inbox preferences changed. Reload before saving.')
    valid = set(DEFAULT_VIEWS) | {'view:' + str(v.id) for v in views(db, workspace_id, user_id)}
    if not set(body.pinned) <= valid: raise HTTPException(422, 'Pin views available to your account.')
    if not row: row = InboxPreference(workspace_id=workspace_id, user_id=user_id, revision=1); db.add(row)
    else: row.revision += 1
    row.pinned = body.pinned; row.sort = body.sort; row.direction = body.direction; db.commit()
    return preferences(db, workspace_id, user_id)


def chat_status(row):
    from app.livechats.service import status
    external = (row.workflow_state or {}).get('external_handoff', {})
    if external.get('status') in {'sending', 'transferred', 'uncertain'}: return 'external'
    if row.live_status == 'none': return 'closed' if row.workflow_state.get('closed') else 'ai'
    return status(row)


def is_priority(db, row):
    from app.teamspace.assignment import policy
    ops = policy(db, row.workspace_id, r.record_space(db, 'chats', row))
    config = ops.assignment
    if not config.get('priority_enabled'): return False
    person = db.get(SupportPerson, row.person_id) if row.person_id else None
    if not person: return False
    labels = r.label_payload(db, row.workspace_id, 'people', person)
    tags = {t['id'] for t in labels['effective_tags']}; groups = set(labels['group_ids'])
    from app.models import TicketTag
    for tag in db.scalars(select(TicketTag).where(TicketTag.workspace_id == row.workspace_id, TicketTag.id.in_([uuid.UUID(i) for i in tags]))): groups.update(r.tag_group_ids(db, tag))
    return bool(tags & set(config.get('tag_ids', [])) or groups & set(config.get('group_ids', [])))


def last_messages(db, kind, row):
    if kind == 'chats':
        all_messages = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == row.id).order_by(AgentMessage.sequence.desc()).limit(100)).all()
        last = next((m for m in all_messages if m.role in {'user', 'assistant'}), None)
        customer = next((m for m in all_messages if m.role == 'user'), None)
        replied = next((m for m in all_messages if m.role == 'assistant' and (m.message_data or {}).get('delivery_status') not in {'failed','failed_to_send','failed_to_deliver'}), None)
        return last.created_at if last else row.created_at, last.content if last else '', customer.created_at if customer and (not replied or customer.created_at > replied.created_at) else None
    incoming = db.scalar(select(TicketInboundEmail).where(TicketInboundEmail.ticket_id == row.id).order_by(TicketInboundEmail.created_at.desc()).limit(1))
    sent = db.scalar(select(TicketEmail).where(TicketEmail.ticket_id == row.id, TicketEmail.recipient_type == 'customer').order_by(TicketEmail.created_at.desc()).limit(1))
    latest = max([row.created_at, *([incoming.created_at] if incoming else []), *([sent.created_at] if sent else [])])
    text = incoming.content if incoming and (not sent or incoming.created_at > sent.created_at) else sent.body_text if sent else row.description
    delivered=db.scalar(select(TicketEmail).where(TicketEmail.ticket_id==row.id,TicketEmail.recipient_type=='customer',TicketEmail.status=='sent').order_by(TicketEmail.sent_at.desc()).limit(1))
    return latest, text, incoming.created_at if incoming and (not delivered or incoming.created_at > delivered.sent_at) else None


def summary(db, kind, row, user_id):
    from app.people.service import chat_channels
    person = db.get(SupportPerson, row.person_id) if row.person_id else None
    visible_person = r.person_payload(db, person, user_id) if person and r.person_visible(db, row.workspace_id, person, user_id) else None
    status = chat_status(row) if kind == 'chats' else 'pending' if row.status == 'in_progress' else row.status
    latest, preview, waiting = last_messages(db, kind, row)
    assigned = row.live_assigned_user_id if kind == 'chats' else row.assignee_id if row.assignee_type == 'user' else None
    name = (visible_person or {}).get('name') or (row.full_name if kind == 'tickets' else '') or ('Unknown person' if kind == 'tickets' else row.agent_name)
    manage = True
    try: r.record(db, row.workspace_id, kind, row.id, user_id, 'manage')
    except HTTPException: manage = False
    space = r.record_space(db, kind, row)
    return {'key': kind + ':' + str(row.id), 'kind': kind, 'id': str(row.id), 'name': name,
        'subject': row.subject if kind == 'tickets' else '', 'description': row.description if kind == 'tickets' else row.live_description, 'agent_name': row.agent_name,
        'status': status, 'runtime_status': row.live_status if kind == 'chats' else row.status,
        'teamspace_id': str(space), 'person_id': (visible_person or {}).get('id'),
        'assigned_user_id': str(assigned) if assigned else None,
        'revision': row.live_revision if kind == 'chats' else row.revision,
        'created_at': row.created_at, 'updated_at': row.live_updated_at if kind == 'chats' else row.updated_at,
        'latest_at': latest, 'preview': preview[:180], 'wait_since': waiting,
        'handoff_at': row.live_context.get('handoff_at') if kind == 'chats' else None,
        'priority': is_priority(db, row) if kind == 'chats' else False,
        'channel': ('tester' if row.workflow_state.get('channel')=='tester' else 'facebook' if row.workflow_state.get('channel')=='facebook' else next((v for v in ['whatsapp','webchat','messaging','bitrix24','africas_talking'] if v in chat_channels(db,row)), 'webchat')) if kind == 'chats' else 'email',
        'channels': list(chat_channels(db,row)) if kind=='chats' else ['email'],
        'channel_name':(row.workflow_state.get('webchat_name') or row.workflow_state.get('whatsapp_name','')) if kind=='chats' else '',
        'channel_icon_url':'/api/workspaces/'+str(row.workspace_id)+'/support/chats/'+str(row.id)+'/channel-icon' if kind=='chats' and (row.workflow_state.get('webchat_icon') or row.workflow_state.get('whatsapp_icon')) else None,
        'language': row.workflow_state.get('language', 'en') if kind == 'chats' else 'en',
        'mine': assigned == user_id, 'can_manage': manage,
        'can_transfer': manage and p.allowed(db, row.workspace_id, 'transfer_livechats' if kind == 'chats' else 'transfer_tickets', user_id, teamspace_id=space),
        'read_only': kind == 'chats' and status in {'ai', 'external', 'closed', 'resolved'}}


def match(db, kind, row, item, filters, user_id):
    if filters.kind != 'all' and filters.kind != kind: return False
    if filters.statuses and item['status'] not in filters.statuses: return False
    if filters.priority and not item['priority']: return False
    if filters.agent_id and str(filters.agent_id) != item['assigned_user_id']: return False
    teams = row.live_context.get('routing', {}).get('team_ids', []) if kind == 'chats' else __import__('app.tickets.workspace', fromlist=['team_ids']).team_ids(row)
    if filters.team_id and str(filters.team_id) not in teams: return False
    if filters.assigned == 'me' and not item['mine']: return False
    if filters.assigned == 'unassigned' and item['assigned_user_id']: return False
    if filters.assigned == 'teams' and not set(teams) & {str(t.id) for t in p.teams_for(db, row.workspace_id, user_id)}: return False
    if filters.language and filters.language != item['language']: return False
    if filters.channel and filters.channel not in {item['channel'],*item['channels']}: return False
    return r.matches_filters(db, row.workspace_id, kind, row, filters.tag_id, filters.group_id, None, None)


def content_matches(db, kind, row, query, at):
    if not query: return True
    query = query.casefold()
    if kind == 'chats':
        return any(query in m.content.casefold() for m in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == row.id, AgentMessage.created_at <= at)))
    if query in (row.subject + '\n' + row.description).casefold(): return True
    return any(query in e.body_text.casefold() for e in db.scalars(select(TicketEmail).where(TicketEmail.ticket_id == row.id, TicketEmail.recipient_type == 'customer', TicketEmail.created_at <= at))) or any(query in e.content.casefold() for e in db.scalars(select(TicketInboundEmail).where(TicketInboundEmail.ticket_id == row.id, TicketInboundEmail.created_at <= at)))


def section_matches(section,kind,row,status,user_id):
    tester=kind=='chats' and row.workflow_state.get('channel','tester')=='tester'
    if section=='mine':return status not in {'closed','resolved','ai','external'} and (row.live_assigned_user_id if kind=='chats' else row.assignee_id if row.assignee_type=='user' else None)==user_id
    if section=='livechats':return kind=='chats' and row.live_status in {'pending','active'} and status!='external' and not tester
    if section=='tickets':return kind=='tickets' and row.status not in {'closed','resolved'}
    if section=='ai':return kind=='chats' and row.live_status=='none' and not row.workflow_state.get('closed') and not tester
    if section=='test':return tester
    return True


def listing(db, workspace_id, user_id, section, selected_views, extra, space_id, query, search_at, sort, direction, offset, limit):
    if space_id and str(space_id) not in p.accessible_spaces(db, workspace_id, user_id): raise HTTPException(404, 'Teamspace not found.')
    now = utcnow(); at = search_at or now
    if at.tzinfo is None or at.timestamp() > now.timestamp() + 30: raise HTTPException(422, 'Use the search snapshot returned by Inbox.')
    available = {'view:' + str(v.id): v for v in views(db, workspace_id, user_id)}
    predicates = [];default_predicates=[]
    for key in selected_views:
        if key in DEFAULT_VIEWS:default_predicates.append(key);continue
        if key not in available: raise HTTPException(404, 'A selected view is unavailable.')
        value = available[key]
        if space_id and value.teamspace_id != space_id: return {'items': [], 'total': 0, 'search_at': at, 'offset': offset, 'limit': limit}
        predicates.append((value.teamspace_id, ViewFilters.model_validate(value.filters)))
    from app.inbox.queue_reader import QueueReader
    reader=QueueReader(db,workspace_id,user_id)
    candidates=reader.candidates(section,query,at,space_id)
    candidates=[(kind,row) for kind,row in candidates if not any(row.space_id!=s for s,_ in predicates)
        and section_matches(section,kind,row,chat_status(row) if kind=='chats' else row.status,user_id)
        and all(section_matches(v,kind,row,chat_status(row) if kind=='chats' else row.status,user_id) for v in default_predicates)]
    if query:
        matching={kind:reader.search_ids(kind,[r for k,r in candidates if k==kind],query,at) for kind in ['chats','tickets']}
        candidates=[(kind,row) for kind,row in candidates if row.id in matching[kind]]
    reader.evidence(candidates,need_labels=any(f.tag_id or f.group_id for f in [extra,*[f for _,f in predicates]]))
    rows={};items=[]
    for kind,row in candidates:
        item=reader.item(kind,row)
        if reader.matches(kind,row,item,extra) and all(reader.matches(kind,row,item,f) for _,f in predicates):
            items.append(item);rows[item['key']]=(kind,row)
    def stamp(value):
        if not value: return 0
        if isinstance(value, str):
            try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError: return 0
        return value.timestamp()
    key = {'created': 'created_at', 'latest': 'latest_at', 'wait': 'wait_since', 'handoff': 'handoff_at'}.get(sort, 'latest_at')
    # Sort duration rather than timestamp for the wait/handoff choices.
    def ordering(item):
        value = stamp(item[key])
        if key in {'wait_since', 'handoff_at'}: value = now.timestamp() - value if value else -1
        return (value, item['key'])
    items.sort(key=ordering, reverse=direction == 'desc')
    items.sort(key=lambda i: not (i['priority'] and i['status'] in {'pending', 'open', 'overdue'}))
    page=reader.decorate([(rows[item['key']][0],rows[item['key']][1],item) for item in items[offset:offset+limit]])
    return {'items':page,'total':len(items),'offset':offset,'limit':limit,'search_at':at}


def attachment_payload(row):
    return {'id': str(row.id), 'name': row.file_name, 'content_type': row.content_type, 'size_bytes': row.size_bytes,
        'url': '/api/workspaces/{}/inbox/{}/{}/attachments/{}'.format(row.workspace_id, row.kind, row.record_id, row.id)}


def attachments(db, row, kind, user_id, ids, private=False):
    result = []
    for identifier in ids:
        asset = db.scalar(select(InboxAttachment).where(InboxAttachment.id == identifier, InboxAttachment.workspace_id == row.workspace_id,
            InboxAttachment.record_id == row.id, InboxAttachment.kind == kind, InboxAttachment.actor_id == user_id).with_for_update())
        if not asset or asset.used: raise HTTPException(422, 'Select unused attachments uploaded for this conversation by your account.')
        result.append(asset)
    if sum(a.size_bytes for a in result) > 20 * 1024 * 1024: raise HTTPException(413, 'Attach at most 20 MiB per reply.')
    for asset in result: asset.used = True; asset.private = private
    return result


def ensure_budget(db, workspace_id, settings, category, exclude_email=None):
    from app.workspace_settings.billing import billing, counters
    from app.workspace_settings.usage import weights
    ledger = billing(db, workspace_id, True); remaining = counters(db, ledger, settings)['remaining']
    pending = db.scalar(select(func.count()).select_from(TicketEmail).join(SupportTicket).where(SupportTicket.workspace_id == workspace_id,
        TicketEmail.kind == 'reply', TicketEmail.status.in_(['queued', 'sending', 'uncertain']), TicketEmail.id != (exclude_email or uuid.uuid4()))) or 0
    from app.models import SupportJob
    pending_ai=db.scalar(select(func.count()).select_from(SupportJob).where(SupportJob.workspace_id==workspace_id,SupportJob.kind.in_(['analysis','copilot']),SupportJob.status.in_(['pending','running']))) or 0
    ai_reserve=pending_ai*max(weights(settings).get(k,1) for k in ['summaries','copilot','translations','mediation'])
    if remaining is not None and remaining < weights(settings).get(category, 1) + pending * weights(settings).get('manual_emails', 1) + ai_reserve:
        raise HTTPException(409, 'This workspace has exhausted its available interactions.')
