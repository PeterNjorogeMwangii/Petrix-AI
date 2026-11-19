import copy,uuid
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.dialects.postgresql import insert
from app.core.security import utcnow
from app.models import Teamspace,TeamspaceOperations,WorkspaceBilling,Workspace,TicketTag,TicketTagGroup,ConversationField,SupportTeam,Membership,ChatAssignment
from app.workspace_settings import permissions as p
from app.teamspace.schemas import AnalysisType
DEFAULTS={'analysis':{k:AnalysisType().model_dump() for k in ['chats','tickets','people']},'copilot':{'all_conversations':False,'person_history':False,'current_conversation':True},'assignment':{'mode':'balanced','limit':10,'priority_enabled':False,'priority_type':'skip_queue','tag_ids':[],'group_ids':[],'cursor':None,'algorithm':2},'enforcement':{'enabled':False,'minimum':1,'sort':'a_z','mandatory_enabled':False,'field_ids':[]},'overdue':{'enabled':False,'days':15,'exclude_weekends':False,'user_id':None,'team_ids':[]},'analytics':{'enabled':False,'url':''},'profile_iframe':{'enabled':False,'url':''}}
PERMISSIONS={'analysis':('view_ai_analysis','manage_ai_analysis'),'copilot':('view_copilot','manage_copilot'),'assignment':('view_chat_assignment','manage_chat_assignment'),'enforcement':('view_tags','manage_tags'),'mandatory':('view_mandatory_fields','manage_custom_fields'),'overdue':('view_overdue_rules','manage_overdue_rules'),'analytics':('view_custom_analytics','manage_custom_analytics'),'profile_iframe':('view_profile_iframe','manage_profile_iframe')}

def space_for(db,workspace_id,space_id):
 row=db.get(Teamspace,space_id)
 if not row or row.workspace_id!=workspace_id:raise HTTPException(404,'Teamspace not found.')
 return row

def operations(db,workspace_id,space_id,lock=False):
 space_for(db,workspace_id,space_id)
 values=copy.deepcopy(DEFAULTS)
 if space_id==p.default_space(db,workspace_id):
  legacy=db.get(ChatAssignment,workspace_id)
  if legacy:values['assignment'].update(mode=legacy.mode,limit=legacy.max_active_chats,algorithm=1)
 db.execute(insert(TeamspaceOperations).values(workspace_id=workspace_id,teamspace_id=space_id,revision=1,**values).on_conflict_do_nothing())
 query=select(TeamspaceOperations).where(TeamspaceOperations.teamspace_id==space_id)
 return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)

def checked_revision(row,expected):
 if row.revision!=expected:raise HTTPException(409,'These settings changed in another session. Reload before saving.')

def plan_rank(db,workspace_id,settings):
 if not settings.stripe_secret_key.get_secret_value():return 99
 name=db.get(Workspace,workspace_id).plan_name.lower().replace(' plan','')
 return {'starter':0,'lite':1,'pro':2,'total':3,'enterprise':4}.get(name,0)

def require_plan(db,workspace_id,settings,minimum):
 if plan_rank(db,workspace_id,settings)<minimum:raise HTTPException(403,'This feature is not included in the current workspace plan.')

def limit(db,workspace_id,key):
 billing=db.get(WorkspaceBilling,workspace_id)
 return (billing.snapshot or {}).get('limits',{}).get(key) if billing else None

def check_count(db,workspace_id,key,current):
 maximum=limit(db,workspace_id,key)
 if maximum is not None and current>=maximum:raise HTTPException(409,'The workspace plan limit for '+key.replace('_',' ')+' has been reached.')

def validate_labels(db,workspace_id,tag_ids,group_ids):
 for model,ids in [(TicketTag,tag_ids),(TicketTagGroup,group_ids)]:
  existing={str(v) for v in db.scalars(select(model.id).where(model.workspace_id==workspace_id,model.archived.is_(False),model.id.in_([uuid.UUID(str(i)) for i in ids])))}
  if set(map(str,ids))!=existing:raise HTTPException(422,'Choose active tags and groups from this workspace.')

def save_config(db,workspace_id,space_id,kind,body,settings):
 row=operations(db,workspace_id,space_id,True);checked_revision(row,body.expected_revision)
 data=body.model_dump(mode='json',exclude={'expected_revision'})
 if kind=='analysis':
  old=row.analysis;now=utcnow().isoformat()
  for key in ['chats','tickets','people']:
   data[key]['auto_since']=old.get(key,{}).get('auto_since',now) if old.get(key,{}).get('auto') else now
 elif kind=='assignment':
  previous=row.assignment;part=data.pop('section');new=copy.deepcopy(previous)
  if part=='method':
   require_plan(db,workspace_id,settings,{'balanced':0,'least_busy':0,'random':1,'round_robin':2,'balanced_round_robin':2,'manual':3}[data['mode']])
   if data['mode']=='manual' and previous.get('priority_type')=='ignore_limit':
    if not data['confirm_voluntary']:raise HTTPException(409,'Confirm switching priority handling to Skip queue for Voluntary assignment.')
    new['priority_type']='skip_queue'
   new.update(mode=data['mode'],algorithm=2,cursor=None)
  elif part=='limit':new['limit']=data['limit']
  else:
   validate_labels(db,workspace_id,data['tag_ids'],data['group_ids'])
   if data['priority_enabled'] and not (data['tag_ids'] or data['group_ids']):raise HTTPException(422,'Select at least one priority tag or group.')
   if previous['mode']=='manual' and data['priority_type']=='ignore_limit':raise HTTPException(422,'Voluntary assignment supports Skip queue priority only.')
   new.update({key:data[key] for key in ['priority_enabled','priority_type','tag_ids','group_ids']})
  data=new
  if space_id==p.default_space(db,workspace_id):
   legacy=db.get(ChatAssignment,workspace_id);legacy.mode=data['mode'];legacy.max_active_chats=data['limit'];legacy.revision+=1
 elif kind=='mandatory':
  ids=data['field_ids'];fields={str(v) for v in db.scalars(select(ConversationField.id).where(ConversationField.workspace_id==workspace_id,ConversationField.scope=='conversation',ConversationField.archived.is_(False)))}
  if not set(ids)<=fields:raise HTTPException(422,'Choose active conversation custom fields.')
  if data['enabled'] and not ids:raise HTTPException(422,'Select at least one mandatory field.')
  data={**row.enforcement,'mandatory_enabled':data['enabled'],'field_ids':ids};kind='enforcement'
 elif kind=='enforcement':data={**row.enforcement,**data}
 elif kind=='overdue':
  if data['user_id'] and not p.membership(db,workspace_id,uuid.UUID(data['user_id'])):raise HTTPException(422,'Choose a current workspace member.')
  teams={str(t) for t in db.scalars(select(SupportTeam.id).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))}
  if not set(data['team_ids'])<=teams:raise HTTPException(422,'Choose current workspace teams.')
 elif kind=='copilot':
  models={v.strip() for v in settings.support_ai_models.split(',') if v.strip()}|{settings.support_ai_model}
  if data['model'] and data['model'] not in models:raise HTTPException(422,'Select a model enabled on this server.')
 elif kind=='analytics':require_plan(db,workspace_id,settings,4)
 setattr(row,kind,data);row.revision+=1;db.commit();return row
