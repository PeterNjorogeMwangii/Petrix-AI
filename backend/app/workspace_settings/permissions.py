"""Independent grants; team roles cannot confer workspace administration."""
import uuid
from fastapi import HTTPException
from sqlalchemy import select
from app.models import Membership, WorkspaceRole, RolePermission, SupportTeam, Teamspace
GROUPS={
'Workspace':[(k,'Manage '+v) for k,v in [('manage_workspace','workspace'),('manage_teamspaces','teamspaces'),('manage_workspace_roles','user roles & permissions'),('manage_users','users'),('manage_teams','teams'),('manage_billing','billing')]]+[('view_billing','View billing')],
'Teamspace':[(a+'_'+k,a.title()+' '+v) for k,v in [('bans','ban rules & reasons'),('tags','tags & tag groups'),('email','email template alerts & domains'),('canned_replies','canned replies')] for a in ['manage','view']]+[('manage_custom_fields','Manage custom fields'),('manage_profile_iframe','Manage profile iFrame')],
'AI Agents':[('manage_ai_agents','Manage AI agents'),('view_ai_agents','View AI agents')]}
for kind in ['livechats','tickets']:
 GROUPS[kind.title()]=[(f'{action}_{kind}_{scope}',f'{action.title()} {kind} {suffix}') for action in ['view','manage','export','download'] for scope,suffix in [('all','in the workspace'),('teams',"assigned to the user's teams"),('own','assigned to the user')]]+[(f'transfer_{kind}','Transfer and takeover '+kind+' managed by the user')]
GROUPS['People']=[('manage_people_all','Manage all people'),('view_people_all','View all people')]+[(f'{action}_people_{field}',action.title()+' people '+label) for action in ['view','manage'] for field,label in [('details','details'),('name','name'),('email','email'),('phone','phone'),('location','location / country / IP'),('custom_fields','custom fields'),('tags','tags'),('unique_id','unique ID')]]
GROUPS['Analytics']=[('analytics_'+k,'View '+v) for k,v in [('messaging','messaging'),('chats','chats'),('csat','CSAT'),('tickets','tickets'),('users_teams','users and teams')]]+[('manage_social_surveillance','Manage social surveillance'),('view_social_surveillance','View social surveillance')]
GROUPS['AI Analysis']=[('manage_ai_analysis','Manage AI analysis'),('trigger_ai_analysis','Trigger manual AI analysis'),('view_ai_analysis','View AI analysis')]
GROUPS['Perception']=[('manage_perception','Manage perception'),('view_perception','View perception')]
GROUPS['Data Management']=[('manage_ip_allowlist','Manage IP allowlist'),('view_ip_allowlist','View IP allowlist'),('manage_whitelabelling','Manage whitelabelling'),('view_whitelabelling','View whitelabelling'),('manage_db_connections','Manage DB connection settings')]
GROUPS['Teamspace'] += [(a+'_'+k,a.title()+' '+v) for k,v in [('copilot','Inbox copilot'),('chat_assignment','chat assignment'),('custom_analytics','custom analytics'),('overdue_rules','overdue rules')] for a in ['manage','view']]+[('view_profile_iframe','View profile iFrame'),('view_mandatory_fields','View mandatory custom fields')]
GROUPS['People'] += [('export_people','Export people'),('download_people_all','Download all people exports'),('download_people_teams','Download people exports initiated by teams'),('download_people_own','Download own people exports')]
ALL={k for values in GROUPS.values() for k,_ in values}
TEAM={k for group,values in GROUPS.items() if group not in {'Workspace','Data Management'} for k,_ in values}
def catalog():return [{'name':n,'team':n not in {'Workspace','Data Management'},'permissions':[{'key':k,'label':v} for k,v in values]} for n,values in GROUPS.items()]
def membership(db,workspace_id,user_id=None):
 user_id=user_id or db.info.get('actor_id')
 return db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==user_id).execution_options(populate_existing=True)) if user_id else None

def legacy(db,workspace_id,role):
 if role=='owner':return set(ALL)
 old=db.scalar(select(RolePermission).where(RolePermission.workspace_id==workspace_id,RolePermission.role==role).execution_options(populate_existing=True));manages=old.manage_ai_agents if old else role=='admin'
 if role=='admin':return set(ALL)-({'manage_ai_agents'} if not manages else set())
 result={'view_ai_agents','view_livechats_own','view_livechats_teams','manage_livechats_own','manage_livechats_teams','transfer_livechats','view_tickets_own','view_tickets_teams','manage_tickets_own','manage_tickets_teams','transfer_tickets'}
 if manages:result|=TEAM|{'manage_teams','manage_db_connections'}
 return result

def workspace_grants(db,workspace_id,user_id=None,role=None):
 member=membership(db,workspace_id,user_id)
 if member:
  if member.role=='owner':return set(ALL)
  if member.custom_role_id:
   custom=db.get(WorkspaceRole,member.custom_role_id,populate_existing=True)
   return set(ALL) if custom and custom.protected else set(custom.permissions) if custom and custom.kind=='workspace' else set()
  return legacy(db,workspace_id,member.role)
 return legacy(db,workspace_id,role) if role else set()

def teams_for(db,workspace_id,user_id=None):
 identifier=str(user_id or db.info.get('actor_id') or '')
 return [t for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False))).all() if identifier in (t.member_ids or [])]

def accessible_spaces(db,workspace_id,user_id=None):
 member=membership(db,workspace_id,user_id)
 if not member:return set()
 ids={str(v) for v in db.scalars(select(Teamspace.id).where(Teamspace.workspace_id==workspace_id)).all()}
 return ids if member.role=='owner' or member.teamspace_ids is None else set(member.teamspace_ids)&ids

def default_space(db,workspace_id):return db.scalar(select(Teamspace.id).where(Teamspace.workspace_id==workspace_id,Teamspace.is_default.is_(True)))

def allowed(db,workspace_id,key,user_id=None,role=None,teamspace_id=None):
 if key in workspace_grants(db,workspace_id,user_id,role):return teamspace_id is None or str(teamspace_id) in accessible_spaces(db,workspace_id,user_id)
 if key not in TEAM or teamspace_id is None or str(teamspace_id) not in accessible_spaces(db,workspace_id,user_id):return False
 for team in teams_for(db,workspace_id,user_id):
  custom=db.get(WorkspaceRole,team.role_id,populate_existing=True) if team.role_id else None
  spaces=team.teamspace_ids if team.teamspace_ids is not None else [str(default_space(db,workspace_id))]
  if custom and custom.kind=='team' and key in custom.permissions and str(teamspace_id) in spaces:return True
 return False

def require(db,workspace_id,key,user_id=None,teamspace_id=None):
 if not allowed(db,workspace_id,key,user_id,teamspace_id=teamspace_id):raise HTTPException(403,'Your role does not allow this operation ('+key+').')

def has_custom(db,workspace_id,user_id=None):
 member=membership(db,workspace_id,user_id)
 return bool(member and (member.custom_role_id or any(t.role_id for t in teams_for(db,workspace_id,user_id))))

def record_scopes(db,workspace_id,user_id,kind,action):
 grants=workspace_grants(db,workspace_id,user_id)
 return {s for s in ['all','teams','own'] if f'{action}_{kind}_{s}' in grants},{str(t.id) for t in teams_for(db,workspace_id,user_id)}

def team_record_grants(db,workspace_id,user_id,kind,action):
 result=[]
 for team in teams_for(db,workspace_id,user_id):
  custom=db.get(WorkspaceRole,team.role_id,populate_existing=True) if team.role_id else None
  if not custom or custom.kind!='team':continue
  scopes={s for s in ['all','teams','own'] if f'{action}_{kind}_{s}' in custom.permissions}
  spaces=set(team.teamspace_ids) if team.teamspace_ids is not None else {str(default_space(db,workspace_id))}
  result.append((str(team.id),scopes,spaces&accessible_spaces(db,workspace_id,user_id)))
 return result

def record_allowed(db,workspace_id,user_id,kind,action,assigned_user=None,assigned_team=None,teamspace_id=None):
 scopes,teams=record_scopes(db,workspace_id,user_id,kind,action)
 member=membership(db,workspace_id,user_id)
 if teamspace_id is None and member and member.teamspace_ids is not None:return False
 if teamspace_id and str(teamspace_id) not in accessible_spaces(db,workspace_id,user_id):return False
 if 'all' in scopes or ('own' in scopes and str(assigned_user)==str(user_id)) or ('teams' in scopes and str(assigned_team) in teams):return True
 for team_id,scoped,spaces in team_record_grants(db,workspace_id,user_id,kind,action):
  if str(teamspace_id) not in spaces:continue
  if scoped&{'all','teams'} and str(assigned_team)==team_id:return True
  if 'own' in scoped and str(assigned_user)==str(user_id):return True
 return False


def bootstrap(db,workspace_id):
 from sqlalchemy.dialects.postgresql import insert
 existing=default_space(db,workspace_id)
 space_id=existing or uuid.uuid5(workspace_id,'default-teamspace')
 db.execute(insert(Teamspace).values(id=space_id,workspace_id=workspace_id,name='Default',name_key='default',is_default=True,revision=1).on_conflict_do_nothing())
 db.execute(insert(WorkspaceRole).values(id=uuid.uuid5(workspace_id,'protected-admin-role'),workspace_id=workspace_id,name='Admin',name_key='admin',description='Default admin role',kind='workspace',permissions=sorted(ALL),protected=True,revision=1).on_conflict_do_nothing())
 return space_id
