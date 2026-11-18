import uuid
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select,func,or_,false
from sqlalchemy.dialects.postgresql import insert
from app.core.security import utcnow
from app.models import Agent,AgentConversation,AgentMessage,SupportTicket,SupportPerson,User,Membership,AIInboundSession,RecordLabels,TicketTag,TicketTagGroup,TagGroupMembership,ConversationField,PersonBanEvent,BanRule,BanReason,Teamspace
from app.workspace_settings import permissions as p
from app.teamspace.configuration import operations,validate_labels,checked_revision


def person_for(db,conversation,network=False):
 if conversation.person_id:
  row=db.get(SupportPerson,conversation.person_id)
  if row:return row
 network=network or db.scalar(select(AIInboundSession.id).where(AIInboundSession.conversation_id==conversation.id)) is not None
 key=('transfer:'+str(conversation.id)) if network else 'account:'+str(conversation.user_id)
 user=db.get(User,conversation.user_id);agent=db.get(Agent,conversation.agent_id) if conversation.agent_id else None
 identifier=uuid.uuid5(conversation.workspace_id,key)
 db.execute(insert(SupportPerson).values(id=identifier,workspace_id=conversation.workspace_id,teamspace_id=agent.teamspace_id if agent else p.default_space(db,conversation.workspace_id),user_id=None if network else conversation.user_id,identity_key=key,name='' if network else (user.full_name or '') if user else '',email='' if network else user.email if user else '',phone='',location='',country='',custom_fields={},offences=0,ban_permanent=False,ban_reason='',revision=1,created_at=utcnow(),updated_at=utcnow()).on_conflict_do_nothing())
 row=db.scalar(select(SupportPerson).where(SupportPerson.workspace_id==conversation.workspace_id,SupportPerson.identity_key==key))
 conversation.person_id=row.id;db.flush();return row


def is_banned(person):return bool(person and (person.ban_permanent or person.banned_until and person.banned_until>utcnow()))

def check_ban(db,conversation,network=False):
 person=person_for(db,conversation,network)
 if is_banned(person):raise HTTPException(403,'This person cannot exchange messages while a ban is active.')
 return person


def record_space(db,kind,row):
 if kind=='people':return row.teamspace_id or p.default_space(db,row.workspace_id)
 if kind=='tickets':
  conversation=db.get(AgentConversation,row.conversation_id) if row.conversation_id else None
 else:conversation=row
 agent=db.get(Agent,conversation.agent_id) if conversation and conversation.agent_id else None
 return agent.teamspace_id if agent and agent.teamspace_id else p.default_space(db,row.workspace_id)


def person_visible(db,workspace_id,person,user_id,manage=False):
 key='manage_people_all' if manage else 'view_people_all'
 if key in p.workspace_grants(db,workspace_id,user_id):return str(record_space(db,'people',person)) in p.accessible_spaces(db,workspace_id,user_id)
 conversations=db.scalars(select(AgentConversation).where(AgentConversation.workspace_id==workspace_id,AgentConversation.person_id==person.id)).all()
 for team in p.teams_for(db,workspace_id,user_id):
  from app.models import WorkspaceRole
  role=db.get(WorkspaceRole,team.role_id) if team.role_id else None
  if not role or key not in role.permissions:continue
  spaces=team.teamspace_ids if team.teamspace_ids is not None else [str(p.default_space(db,workspace_id))]
  if str(person.team_id)==str(team.id) and str(person.teamspace_id) in spaces:return True
  if any(str(team.id) in c.live_context.get('routing',{}).get('team_ids',[]) and str(record_space(db,'chats',c)) in spaces for c in conversations):return True
 return False


def record(db,workspace_id,kind,identifier,user_id=None,action='view',lock=False,system=False):
 model={'chats':AgentConversation,'tickets':SupportTicket,'people':SupportPerson}.get(kind)
 if not model:raise HTTPException(422,'Choose chats, tickets, or people.')
 query=select(model).where(model.workspace_id==workspace_id,model.id==identifier)
 row=db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)
 if not row:raise HTTPException(404,'Record not found.')
 if system:return row
 user_id=user_id or db.info.get('actor_id')
 if kind=='people':permitted=person_visible(db,workspace_id,row,user_id,action=='manage')
 elif kind=='tickets':
  from app.tickets.service import access_filter
  member=p.membership(db,workspace_id,user_id)
  from app.agent_service import can_manage
  permitted=bool(member and db.scalar(select(SupportTicket.id).where(SupportTicket.id==identifier,access_filter(db,workspace_id,user_id,can_manage(db,workspace_id,member.role),action))))
 else:
  from app.inbox.service import can_view,require_manage
  # Native AI chats are visible to their tester owner or an authorized all-record operator.
  permitted=can_view(db,row,user_id) or (row.user_id==user_id and p.allowed(db,workspace_id,'view_ai_agents',user_id,teamspace_id=record_space(db,kind,row))) or p.record_allowed(db,workspace_id,user_id,'livechats',action,row.live_assigned_user_id,None,record_space(db,kind,row))
  if permitted and action=='manage':require_manage(db,row,user_id)
 if not permitted:raise HTTPException(404,'Record not found or your role does not permit this operation.')
 return row


def labels(db,workspace_id,kind,row):
 value=db.get(RecordLabels,(workspace_id,kind,row.id))
 if value:return value
 tags=[item['id'] for item in (row.tags or []) if item.get('id')] if kind=='tickets' else []
 return RecordLabels(workspace_id=workspace_id,kind=kind,record_id=row.id,tag_ids=tags,group_ids=[],revision=0)


def tag_group_ids(db,tag):
 active={str(i) for i in db.scalars(select(TicketTagGroup.id).where(TicketTagGroup.workspace_id==tag.workspace_id,TicketTagGroup.archived.is_(False)))}
 return sorted(({str(g) for g in db.scalars(select(TagGroupMembership.group_id).where(TagGroupMembership.tag_id==tag.id))}|({str(tag.group_id)} if tag.group_id else set()))&active)


def label_payload(db,workspace_id,kind,row):
 assigned=labels(db,workspace_id,kind,row);groups={str(g.id):g for g in db.scalars(select(TicketTagGroup).where(TicketTagGroup.workspace_id==workspace_id,TicketTagGroup.archived.is_(False)))}
 all_tags=list(db.scalars(select(TicketTag).where(TicketTag.workspace_id==workspace_id,TicketTag.archived.is_(False))))
 effective=[tag for tag in all_tags if str(tag.id) in assigned.tag_ids or set(tag_group_ids(db,tag))&set(assigned.group_ids)]
 return {'revision':assigned.revision,'tag_ids':[i for i in assigned.tag_ids if i in {str(t.id) for t in all_tags}],'group_ids':[i for i in assigned.group_ids if i in groups],'effective_tags':[{'id':str(t.id),'name':t.name,'color':t.color,'direct':str(t.id) in assigned.tag_ids} for t in effective],'groups':[{'id':key,'name':groups[key].name,'count':sum(key in tag_group_ids(db,t) for t in all_tags)} for key in assigned.group_ids if key in groups]}


def set_labels(db,workspace_id,kind,row,body,commit=True):
 validate_labels(db,workspace_id,body.tag_ids,body.group_ids)
 current=labels(db,workspace_id,kind,row)
 if body.expected_revision is not None and current.revision!=body.expected_revision:raise HTTPException(409,'Tags changed in another session. Reload first.')
 if current.revision==0:current.revision=1;db.add(current)
 current.tag_ids=sorted(set(current.tag_ids if body.mode=='add' else [])|set(map(str,body.tag_ids)));current.group_ids=sorted(set(current.group_ids if body.mode=='add' else [])|set(map(str,body.group_ids)));current.revision+=1
 if kind=='tickets':
  snapshot=label_payload(db,workspace_id,kind,row);row.tags=[{**tag,'group':None} for tag in snapshot['effective_tags']];row.revision+=1
 if commit:db.commit()
 return label_payload(db,workspace_id,kind,row)


def check_resolution(db,workspace_id,kind,row):
 config=operations(db,workspace_id,record_space(db,kind,row)).enforcement
 if config.get('enabled') and len(label_payload(db,workspace_id,kind,row)['effective_tags'])<config.get('minimum',1):raise HTTPException(422,'Apply at least '+str(config.get('minimum',1))+' tags before resolving or closing this conversation.')
 if kind=='tickets' and config.get('mandatory_enabled'):
  from app.custom_fields import service as fields
  values=row.custom_fields or {}
  for field in db.scalars(select(ConversationField).where(ConversationField.workspace_id==workspace_id,ConversationField.archived.is_(False),ConversationField.id.in_([uuid.UUID(i) for i in config.get('field_ids',[])]))):
   value=(values.get(field.key) or {}).get('value',field.default_value)
   if value is None or value=='':raise HTTPException(422,'Complete the mandatory field “'+field.name+'” before resolving or closing this ticket.')


def field_definitions(db,workspace_id,scope):return list(db.scalars(select(ConversationField).where(ConversationField.workspace_id==workspace_id,ConversationField.scope==scope,ConversationField.archived.is_(False)).order_by(ConversationField.created_at)))

def save_fields(db,workspace_id,kind,row,body):
 from app.custom_fields.schemas import FieldCreate
 from app.custom_fields.service import option_snapshot
 current_revision=row.live_revision if kind=='chats' else row.revision
 if body.expected_revision!=current_revision:raise HTTPException(409,'This record changed. Reload before editing fields.')
 definitions={f.key:f for f in field_definitions(db,workspace_id,'person' if kind=='people' else 'conversation')}
 if set(body.values)-set(definitions):raise HTTPException(422,'Choose active fields for this record category.')
 values=dict(row.custom_fields or {})
 for key,value in body.values.items():
  field=definitions[key]
  try:FieldCreate.model_validate({'scope':field.scope,'type':field.type,'name':field.name,'options':field.options,'default_value':value})
  except ValueError:raise HTTPException(422,'The value for “'+field.name+'” does not match its type or options.') from None
  values[key]=option_snapshot(field,value,'manual')
 row.custom_fields=values
 if kind=='chats':
  row.live_revision+=1;row.workflow_state={**row.workflow_state,'custom_fields':values}
 else:row.revision+=1
 db.commit();return values


def person_payload(db,person,user_id):
 space=record_space(db,'people',person)
 permitted=lambda field:p.allowed(db,person.workspace_id,'view_people_'+field,user_id,teamspace_id=space)
 result={'id':str(person.id),'revision':person.revision,'banned':is_banned(person),'offences':person.offences,'banned_until':person.banned_until,'ban_permanent':person.ban_permanent,'ban_reason':person.ban_reason,'created_at':person.created_at,'updated_at':person.updated_at}
 for field,key in [('name','name'),('email','email'),('phone','phone'),('location','location'),('country','location'),('external_id','unique_id')]:
  if permitted(key):result[field]=getattr(person,field)
 if permitted('custom_fields'):result['custom_fields']=person.custom_fields
 if permitted('tags'):result['labels']=label_payload(db,person.workspace_id,'people',person)
 result['can_manage']=person_visible(db,person.workspace_id,person,user_id,True)
 return result


def apply_ban(db,workspace_id,person,space_id,actor_id,body):
 checked_revision(person,body.expected_revision)
 reason=db.scalar(select(BanReason).where(BanReason.id==body.reason_id,BanReason.workspace_id==workspace_id,BanReason.teamspace_id==space_id))
 if not reason:raise HTTPException(422,'Configure and select a ban reason in this teamspace.')
 rule=db.scalar(select(BanRule).where(BanRule.workspace_id==workspace_id,BanRule.teamspace_id==space_id,BanRule.offences<=person.offences).order_by(BanRule.offences.desc()).limit(1))
 duration=body.duration if body.duration is not None else rule.duration if rule else 60
 unit=body.unit if body.unit is not None else rule.unit if rule else 'minutes'
 factors={'minutes':1,'hours':60,'days':1440,'weeks':10080,'months':43200}
 person.ban_permanent=unit=='permanent';person.banned_until=None if person.ban_permanent else utcnow()+timedelta(minutes=duration*factors[unit]);person.ban_reason=reason.name;person.offences+=1;person.revision+=1;person.updated_at=utcnow()
 db.add(PersonBanEvent(person_id=person.id,actor_id=actor_id,reason=reason.name,permanent=person.ban_permanent,until=person.banned_until));db.commit()


def matches_filters(db,workspace_id,kind,row,tag=None,group=None,custom_key=None,custom_value=None):
 if custom_key and str((row.custom_fields.get(custom_key) or {}).get('value',''))!=str(custom_value or ''):return False
 if tag or group:
  labels=label_payload(db,workspace_id,kind,row)
  if tag and str(tag) not in {t['id'] for t in labels['effective_tags']}:return False
  if group and str(group) not in labels['group_ids']:return False
 return True


def filter_options(db,workspace_id,kind,user_id):
 from app.tickets.settings import active,payload
 spaces=p.accessible_spaces(db,workspace_id,user_id)
 tags=kind!='people' or any(p.allowed(db,workspace_id,'view_people_tags',user_id,teamspace_id=i) for i in spaces)
 fields=kind!='people' or any(p.allowed(db,workspace_id,'view_people_custom_fields',user_id,teamspace_id=i) for i in spaces)
 return {'tags':[payload(t) for t in active(db,workspace_id,'tags')] if tags else [],'groups':[payload(t) for t in active(db,workspace_id,'groups')] if tags else [],'fields':[{'key':f.key,'name':f.name,'type':f.type} for f in field_definitions(db,workspace_id,'person' if kind=='people' else 'conversation')] if fields else []}
