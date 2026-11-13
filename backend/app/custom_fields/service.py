"""Workspace definitions, protected workflow references and durable chat snapshots."""
from copy import deepcopy
import re
import keyword
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select

from app.core.security import utcnow
from app.models import Agent, AgentChange, ConversationField, WorkflowDocument
from app.workflows.numbering import walk


def payload(field):
    return {key: getattr(field, key) for key in ('id','key','scope','type','name','description','options','default_value','revision','created_at','updated_at')}


def definitions(db, workspace_id):
    return db.scalars(select(ConversationField).where(ConversationField.workspace_id == workspace_id,
        ConversationField.archived.is_(False)).order_by(ConversationField.created_at, ConversationField.id)).all()


def selected(db, workspace_id, ids, lock=False):
    query = select(ConversationField).where(ConversationField.workspace_id == workspace_id,
        ConversationField.id.in_([uuid.UUID(str(value)) for value in ids]), ConversationField.archived.is_(False),
        ConversationField.type == 'single_select_grouped').order_by(ConversationField.id)
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalars(query).all()


def references(db, workspace_id, field_id):
    documents = db.execute(select(Agent.name, WorkflowDocument.nodes).join(WorkflowDocument, WorkflowDocument.agent_id == Agent.id)
        .where(Agent.workspace_id == workspace_id)).all()
    return [name for name,nodes in documents if any(node['kind'] == 'action' and node['type'] == 'auto_fill_field' and
        str(field_id) in node['config'].get('field_ids', []) for node in walk(nodes))]


def validate_references(db, workspace_id, nodes):
    ids = {value for node in walk(nodes) if node['kind']=='action' and node['type']=='auto_fill_field' for value in node['config']['field_ids']}
    if ids and {str(field.id) for field in selected(db, workspace_id, ids)} != ids:
        raise HTTPException(422, 'Choose active Single Select (Grouped) conversation fields from this workspace for every Auto Fill Field action.')


def get_field(db, workspace_id, identifier):
    field = db.scalar(select(ConversationField).where(ConversationField.id == identifier,
        ConversationField.workspace_id == workspace_id, ConversationField.archived.is_(False)).with_for_update().execution_options(populate_existing=True))
    if not field: raise HTTPException(404, 'Custom field not found.')
    return field


def check_revision(field, expected):
    if field.revision != expected: raise HTTPException(409, 'This custom field changed in another session. Reload before saving.')


def unique_name(db, workspace_id, name, identifier=None):
    duplicate = db.scalar(select(ConversationField.id).where(ConversationField.workspace_id == workspace_id,
        ConversationField.archived.is_(False), func.lower(ConversationField.name) == name.lower(), ConversationField.id != identifier))
    if duplicate: raise HTTPException(409, 'An active custom field with this name already exists.')


def audit(db, workspace_id, actor_id, action, name):
    db.add(AgentChange(workspace_id=workspace_id, actor_id=actor_id, agent_name='', action=action,
        summary='Conversation custom field “{}” {}.'.format(name, action.replace('custom_field_', ''))))


def create(db, workspace_id, actor_id, body):
    if len(definitions(db, workspace_id)) >= 100: raise HTTPException(409, 'A workspace supports up to 100 active conversation fields.')
    unique_name(db, workspace_id, body.name)
    identifier = uuid.uuid4()
    key = re.sub(r'[^a-z0-9]+','_',body.name.lower()).strip('_')[:54]
    if not key or not key[0].isalpha() or keyword.iskeyword(key): key = 'field_' + key
    exists = db.scalar(select(ConversationField.id).where(ConversationField.workspace_id==workspace_id, ConversationField.key==key))
    if exists: key = key[:54] + '_' + identifier.hex[:8]
    field = ConversationField(id=identifier, workspace_id=workspace_id, key=key, revision=1, archived=False, **body.model_dump(mode='json'))
    db.add(field); audit(db,workspace_id,actor_id,'custom_field_created',body.name); db.commit()
    return payload(field)


def update(db, workspace_id, actor_id, identifier, body):
    field = get_field(db, workspace_id, identifier); check_revision(field,body.expected_revision)
    unique_name(db,workspace_id,body.name,field.id)
    if field.type == 'single_select_grouped' and body.type != field.type and references(db,workspace_id,field.id):
        raise HTTPException(409, 'This field is used by Auto Fill Field. Remove those references before changing its type.')
    values = body.model_dump(mode='json',exclude={'expected_revision'})
    if any(getattr(field,key)!=value for key,value in values.items()):
        for key,value in values.items(): setattr(field,key,value)
        field.revision += 1; field.updated_at=utcnow(); audit(db,workspace_id,actor_id,'custom_field_updated',field.name)
    db.commit(); return payload(field)


def remove(db, workspace_id, actor_id, body):
    fields = []
    for value in sorted(body.fields,key=lambda item:str(item.id)):
        field=get_field(db,workspace_id,value.id);check_revision(field,value.expected_revision)
        if references(db,workspace_id,field.id): raise HTTPException(409,'Custom field “{}” is used by Auto Fill Field. Remove its workflow references before deleting it.'.format(field.name))
        fields.append(field)
    for field in fields:
        field.archived=True; field.revision+=1;field.updated_at=utcnow();audit(db,workspace_id,actor_id,'custom_field_deleted',field.name)
    db.commit()


def option_snapshot(field, value, source):
    result = {'field_id':str(field.id),'key':field.key,'name':field.name,'type':field.type,'value':value,
        'label':str(value) if value is not None else '', 'group':None,'source':source,'updated_at':utcnow().isoformat()}
    groups = field.options if field.type=='single_select_grouped' else [{'name':None,'options':field.options}] if field.type=='dropdown' else []
    for group in groups:
        option=next((option for option in group['options'] if option['value']==value),None)
        if option:result.update(label=option['label'],group=group['name']);break
    return result


def with_defaults(db, workspace_id, conversation):
    values=deepcopy(conversation.custom_fields or {})
    for field in definitions(db,workspace_id):
        if field.default_value is not None and field.key not in values: values[field.key]=option_snapshot(field,field.default_value,'default')
    return values


def ticket_field_snapshot(conversation):
    """Shared copy contract for the future ticket-conversion service."""
    return deepcopy(conversation.custom_fields or {})
