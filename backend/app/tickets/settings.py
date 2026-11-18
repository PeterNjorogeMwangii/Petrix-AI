from datetime import timedelta
from urllib.parse import urlsplit
import secrets
import uuid
from fastapi import HTTPException
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import Agent, AgentChange, WorkflowDocument, TicketTagGroup, TicketTag, TicketEmailDomain, TicketEmailTemplate
from app.workflows.numbering import walk
from app.tickets.html import sanitize

MODELS={'groups':TicketTagGroup,'tags':TicketTag,'email-domains':TicketEmailDomain,'email-templates':TicketEmailTemplate}


def active(db,workspace_id,kind):
    model=MODELS[kind]
    return db.scalars(select(model).where(model.workspace_id==workspace_id,model.archived.is_(False)).order_by(model.created_at,model.id)).all()


def get(db,workspace_id,kind,identifier,expected=None):
    model=MODELS[kind]
    row=db.scalar(select(model).where(model.id==identifier,model.workspace_id==workspace_id,model.archived.is_(False)).with_for_update().execution_options(populate_existing=True))
    if not row:raise HTTPException(404,'This setting was not found in this workspace.')
    if expected is not None and row.revision!=expected:raise HTTPException(409,'This setting changed in another session. Reload before saving.')
    return row


def references(db,workspace_id,kind,identifier):
    key={'groups':'tag_group_ids','tags':'tag_ids','email-domains':'email_domain_id','email-templates':'templates','fields':'fields','teams':'team','users':'user'}[kind]
    for nodes in db.scalars(select(WorkflowDocument.nodes).join(Agent,Agent.id==WorkflowDocument.agent_id).where(Agent.workspace_id==workspace_id)):
        for node in walk(nodes):
            if node['kind']!='action' or node['type']!='create_ticket':continue
            config=node['config'];value=str(identifier)
            if key in {'tag_group_ids','tag_ids'} and value in config.get(key,[]):return True
            if key=='email_domain_id' and value==config.get(key):return True
            if key=='templates' and value in {config.get('customer_template_id'),config.get('assignee_template_id')}:return True
            if key=='fields' and any(item['field_id']==value for item in config.get('custom_fields',[])):return True
            if key in {'team','user'} and config.get('assignee_type')==key and config.get('assignee_id')==value:return True
    return False


def valid_sender(row):
    return bool(row and not row.archived and row.enabled and row.verified_at and row.verified_at>utcnow()-timedelta(days=30))


def proof(row,settings):
    host=urlsplit(settings.api_base_url).hostname or 'petrix.invalid'
    if '.' not in host or ':' in host or host.replace('.','').isdigit():host='petrix.invalid'
    return {'type':'CNAME' if row.verification_method=='cname' else 'TXT','name':'_petrix-verify.'+row.domain,
        'value':row.challenge+'.verify.'+host if row.verification_method=='cname' else 'petrix-verification='+row.challenge}


def payload(row,settings=None):
    data={column.name:getattr(row,column.name) for column in row.__table__.columns if column.name not in {'archived','challenge','workspace_id'}}
    if isinstance(row,TicketEmailDomain):
        data.update(verified=valid_sender(row),status='Disabled' if not row.enabled else 'Verified' if valid_sender(row) else 'Expired' if row.verified_at else 'Unverified',
            from_email=row.local_part+'@'+row.domain,dns_record=proof(row,settings))
    if isinstance(row,TicketTag):
        from sqlalchemy.orm import object_session
        from app.teamspace.records import tag_group_ids
        db=object_session(row)
        data['group_ids']=tag_group_ids(db,row) if db else ([str(row.group_id)] if row.group_id else [])
    return data


def audit(db,workspace_id,actor_id,summary):
    db.add(AgentChange(workspace_id=workspace_id,actor_id=actor_id,agent_name='',action='ticket_settings',summary=summary))


def save(db,workspace_id,actor_id,kind,body,settings,identifier=None):
    model=MODELS[kind];row=get(db,workspace_id,kind,identifier,body.expected_revision) if identifier else None
    values=body.model_dump(mode='json',exclude={'expected_revision','group_ids'})
    selected_groups=body.group_ids if kind=='tags' else None
    if kind=='tags' and selected_groups is not None:
        for identifier_value in selected_groups:get(db,workspace_id,'groups',identifier_value)
        values['group_id']=str(selected_groups[0]) if selected_groups else None
    label=values.get('name') or values.get('domain')
    if len(active(db,workspace_id,kind))>=(200 if kind!='tags' else 1000) and not row:raise HTTPException(409,'The workspace limit for these settings was reached.')
    name_key='domain' if kind=='email-domains' else 'name'
    if db.scalar(select(model.id).where(model.workspace_id==workspace_id,model.archived.is_(False),func.lower(getattr(model,name_key))==label.lower(),model.id!=(row.id if row else uuid.uuid4()))):
        raise HTTPException(409,'This name is already used in the workspace.')
    if kind=='tags' and values['group_id']:get(db,workspace_id,'groups',uuid.UUID(values['group_id']))
    if kind=='email-templates':
        values['content'],plain=sanitize(values['content'])
        if not plain:raise HTTPException(422,'Add some visible email text. Scripts, images, forms and embedded pages are removed.')
    if row:
        if kind=='email-domains' and values['domain']!=row.domain:raise HTTPException(422,'Create a separate sender for a different domain.')
        changed=any(getattr(row,key)!=value for key,value in values.items())
        if kind=='tags' and selected_groups is not None:
            from app.teamspace.records import tag_group_ids
            changed=changed or set(map(str,selected_groups))!=set(tag_group_ids(db,row))
        if changed:
            if kind=='email-domains' and row.verification_method!=values['verification_method']:
                row.verified_at=None;row.challenge=secrets.token_hex(20)
            for key,value in values.items():setattr(row,key,value)
            row.revision+=1;row.updated_at=utcnow()
    else:
        if kind=='email-domains':values.update(challenge=secrets.token_hex(20))
        row=model(workspace_id=workspace_id,**values);db.add(row)
    if kind in {'tags','groups'} and not identifier:
        from app.teamspace.configuration import check_count
        check_count(db,workspace_id,'tags' if kind=='tags' else 'tag_groups',len(active(db,workspace_id,kind))-1)
    if kind=='tags':
        from app.models import TagGroupMembership
        from sqlalchemy import delete
        db.flush()
        if selected_groups is not None or 'group_id' in body.model_fields_set or not identifier:
            db.execute(delete(TagGroupMembership).where(TagGroupMembership.tag_id==row.id))
            for group_id in selected_groups if selected_groups is not None else ([row.group_id] if row.group_id else []):db.add(TagGroupMembership(tag_id=row.id,group_id=group_id))
    audit(db,workspace_id,actor_id,'{} “{}” saved.'.format(kind,label));db.commit();return payload(row,settings)


def remove(db,workspace_id,actor_id,kind,identifier,expected,commit=True):
    row=get(db,workspace_id,kind,identifier,expected)
    if references(db,workspace_id,kind,identifier):raise HTTPException(409,'Remove this setting from Create Ticket actions before deleting it.')
    if kind=='groups':
        children=db.scalars(select(TicketTag).where(TicketTag.group_id==identifier,TicketTag.archived.is_(False))).all()
        for tag in children:tag.group_id=None;tag.revision+=1;tag.updated_at=utcnow()
    row.archived=True;row.revision+=1;row.updated_at=utcnow()
    from app.teamspace.assignment import cleanup_priorities
    db.flush();cleanup_priorities(db,workspace_id)
    audit(db,workspace_id,actor_id,'{} removed.'.format(kind))
    if commit:db.commit()


def verify(db,workspace_id,actor_id,identifier,expected,settings,resolver):
    row=get(db,workspace_id,'email-domains',identifier,expected);record=proof(row,settings)
    # The workspace mutation lock serializes settings updates during this bounded lookup.
    values=resolver.records(record['name'],record['type'])
    match=record['value'] in values if record['type']=='TXT' else record['value'].lower().rstrip('.') in [v.lower().rstrip('.') for v in values]
    row.verified_at=utcnow() if match else None;row.revision+=1;row.updated_at=utcnow()
    audit(db,workspace_id,actor_id,'Email domain ownership {}.'.format('verified' if match else 'not confirmed'))
    db.commit();result=payload(row,settings);result['verification_message']='DNS ownership verified. Sending still requires SMTP authorization and provider SPF/DKIM setup.' if match else 'The required DNS record was not found. Add the displayed record and allow DNS propagation before checking again.'
    return result
