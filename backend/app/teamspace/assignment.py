import secrets,uuid
from sqlalchemy import select
from app.core.security import utcnow
from app.models import AgentConversation,Agent,SupportAgent,ChatAssignment,SupportPerson,TeamspaceOperations,TicketTag,TicketTagGroup
from app.teamspace.configuration import operations
from app.teamspace import records as r
from app.workspace_settings import permissions as p


def policy(db,workspace_id,space_id,lock=False):return operations(db,workspace_id,space_id or p.default_space(db,workspace_id),lock)

def priority_for(db,workspace_id,conversation,config):
 if not config.get('priority_enabled') or not conversation:return False
 person=r.person_for(db,conversation);labels=r.label_payload(db,workspace_id,'people',person)
 return bool(set(config.get('tag_ids',[]))&{t['id'] for t in labels['effective_tags']} or set(config.get('group_ids',[]))&set(labels['group_ids']) or any(set(config.get('group_ids',[]))&set(r.tag_group_ids(db,t)) for t in db.scalars(select(TicketTag).where(TicketTag.workspace_id==workspace_id,TicketTag.id.in_([uuid.UUID(i) for i in labels['tag_ids']]),TicketTag.archived.is_(False)))))

def pick(db,workspace_id,ops,people,priority=False):
 config=ops.assignment
 online=[person for person in people if person['available']]
 if config['mode']=='manual':return None
 ignore=priority and config.get('priority_type')=='ignore_limit'
 available=[person for person in online if ignore or person['active_chats']<config['limit']]
 if not available:return None
 mode=config['mode']
 if mode=='random':chosen=secrets.choice(available)
 else:
  if mode in {'balanced','least_busy','balanced_round_robin'}:
   lowest=min(person['active_chats'] for person in available);available=[person for person in available if person['active_chats']==lowest]
  if mode=='balanced':chosen=secrets.choice(available)
  else:
   def order(person):
    profile=db.get(SupportAgent,(workspace_id,uuid.UUID(person['id'])));stamp=(profile.online_since or profile.last_seen_at).timestamp() if profile and (profile.online_since or profile.last_seen_at) else 0
    return stamp,person['id']
   ordered=sorted(online,key=order);cursor=config.get('cursor');position=next((i for i,v in enumerate(ordered) if v['id']==cursor),-1);rotated=ordered[position+1:]+ordered[:position+1]
   ids={person['id'] for person in available};chosen=next(person for person in rotated if person['id'] in ids)
 config={**config,'cursor':chosen['id']};ops.assignment=config
 profile=db.get(SupportAgent,(workspace_id,uuid.UUID(chosen['id'])));profile.last_assigned_at=utcnow()
 return chosen['id']

def offer(db,workspace_id,config,context,conversation=None):
 from app.inbox.service import eligible
 space_id=context.agent.teamspace_id or p.default_space(db,workspace_id);ops=policy(db,workspace_id,space_id,True)
 if ops.assignment.get('algorithm',1)<2:return None
 routing={key:config[key] for key in ['assignment_mode','user_ids','team_ids','assign_by_language']};routing['language']=context.language
 if config['team_variable']:
  from app.models import SupportTeam
  value=context.variables.get(config['team_variable']);identifier=value.get('id') if isinstance(value,dict) else value
  try:identifier=str(uuid.UUID(str(identifier)))
  except (ValueError,TypeError):return None,'team_not_set'
  team=db.get(SupportTeam,uuid.UUID(identifier))
  if not team or team.workspace_id!=workspace_id or team.archived:return None,'team_unavailable'
  routing['team_ids']=list(dict.fromkeys([*routing['team_ids'],identifier]))
 people=eligible(db,workspace_id,routing,space_id=space_id)
 if not people:return None,'no_eligible_agents'
 if config['availability_check'] and not any(person['available'] for person in people):return None,'no_available_agents'
 priority=priority_for(db,workspace_id,conversation,ops.assignment);chosen=pick(db,workspace_id,ops,people,priority)
 return {'routing':routing,'eligible_ids':[person['id'] for person in people],'assigned_id':chosen,'priority':priority,'priority_type':ops.assignment.get('priority_type','skip_queue')},None

def drain(db,workspace_id):
 from app.inbox.service import eligible,ensure_profile
 # Serialize occupancy decisions with the legacy handoff/claim lock as well.
 db.scalar(select(ChatAssignment).where(ChatAssignment.workspace_id==workspace_id).with_for_update())
 rows=list(db.scalars(select(AgentConversation).where(AgentConversation.workspace_id==workspace_id,AgentConversation.live_status=='pending').order_by(AgentConversation.live_updated_at,AgentConversation.id).with_for_update()))
 rows.sort(key=lambda row:(not bool(row.live_context.get('priority')),row.live_updated_at or row.created_at,str(row.id)))
 for row in rows:
  if r.is_banned(db.get(SupportPerson,row.person_id)) if row.person_id else False:continue
  space_id=r.record_space(db,'chats',row);ops=policy(db,workspace_id,space_id,True)
  if ops.assignment.get('algorithm',1)<2 or ops.assignment['mode']=='manual':continue
  people=eligible(db,workspace_id,row.live_context['routing'],space_id=space_id)
  chosen=pick(db,workspace_id,ops,people,row.live_context.get('priority',False))
  if chosen:row.live_assigned_user_id=uuid.UUID(chosen);row.live_status='active';row.live_revision+=1;row.live_updated_at=utcnow();db.flush()
 db.commit()

def cleanup_priorities(db,workspace_id):
 tags={str(i) for i in db.scalars(select(TicketTag.id).where(TicketTag.workspace_id==workspace_id,TicketTag.archived.is_(False)))}
 groups={str(i) for i in db.scalars(select(TicketTagGroup.id).where(TicketTagGroup.workspace_id==workspace_id,TicketTagGroup.archived.is_(False)))}
 for ops in db.scalars(select(TeamspaceOperations).where(TeamspaceOperations.workspace_id==workspace_id).with_for_update()):
  value=dict(ops.assignment);value['tag_ids']=[i for i in value.get('tag_ids',[]) if i in tags];value['group_ids']=[i for i in value.get('group_ids',[]) if i in groups]
  if not(value['tag_ids'] or value['group_ids']):value['priority_enabled']=False
  if value!=ops.assignment:ops.assignment=value;ops.revision+=1
