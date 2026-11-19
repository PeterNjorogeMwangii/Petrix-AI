import hashlib
import io
import json
import re
import unicodedata
import uuid
import warnings
from datetime import timedelta
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field, EmailStr, StrictInt
from sqlalchemy import select, update, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.agent_schemas import StrictModel
from app.auth_service import current_session, require_csrf, user_context
from app.core.security import utcnow, new_token, token_hash
from app.database import get_db
from app.models import Workspace, Membership, WorkspaceSettings, WorkspaceRole, WorkspaceInvite, Teamspace, SupportTeam, User, AuthSession, Agent, AgentConversation
from app.workspace_settings import permissions as p
from app.workspace_settings import security

router=APIRouter(prefix='/api',tags=['Workspace settings'])


def policy(db,workspace_id,lock=False):
 db.execute(insert(WorkspaceSettings).values(workspace_id=workspace_id,revision=1,ip_enabled=False,exempt_admins=True,ip_ranges=[],idle_minutes=0,colors={},branding_enabled=False).on_conflict_do_nothing())
 query=select(WorkspaceSettings).where(WorkspaceSettings.workspace_id==workspace_id)
 return db.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


def actor(request,db,workspace_id,key=None,mutation=False):
 session=current_session(request,db)
 if mutation:require_csrf(request,session)
 from app.agent_service import workspace_for
 workspace,role=workspace_for(db,workspace_id,session.user_id,lock=mutation)
 if key:p.require(db,workspace_id,key,session.user_id)
 return session,workspace,role


def revision(row,expected):
 if row.revision!=expected:raise HTTPException(409,'These settings changed in another session. Reload before saving.')


def name_key(value):return unicodedata.normalize('NFKC',value).casefold().strip()


def branding_payload(db,workspace_id):
 value=db.get(WorkspaceSettings,workspace_id)
 if not value:return {}
 return {'enabled':value.branding_enabled,'colors':value.colors if value.branding_enabled else {},'main_logo_url':f'/api/workspaces/{workspace_id}/branding/main?v={value.revision}' if value.branding_enabled and value.main_logo else None,'square_logo_url':f'/api/workspaces/{workspace_id}/branding/square?v={value.revision}' if value.branding_enabled and value.square_logo else None,'revision':value.revision}


def image_bytes(data,square=False):
 if not data or len(data)>10*1024*1024:raise HTTPException(422,'Choose a PNG, JPEG, or GIF image up to 10 MB.')
 try:
  with warnings.catch_warnings():
   warnings.simplefilter('error',Image.DecompressionBombWarning)
   with Image.open(io.BytesIO(data)) as img:
    if img.format not in {'PNG','JPEG','GIF'} or img.width*img.height>16_000_000:raise ValueError
    img.seek(0);img.load();img=ImageOps.exif_transpose(img).convert('RGBA')
    if square:img=ImageOps.fit(img,(256,256),method=Image.Resampling.LANCZOS)
    else:img.thumbnail((1024,512),Image.Resampling.LANCZOS)
    out=io.BytesIO();img.save(out,format='PNG');return out.getvalue()
 except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise HTTPException(422,'Upload a valid PNG, JPEG, or GIF image. GIF images use their first frame.') from None


@router.get('/workspaces/{workspace_id}/workspace-settings')
def overview(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id)
 row=policy(db,workspace_id);p.bootstrap(db,workspace_id);db.commit()
 return {'revision':row.revision,'name':workspace.name,'timezone':row.timezone,'permissions':{key:p.allowed(db,workspace_id,key) for key in p.ALL},'avatar_url':f'/api/workspaces/{workspace_id}/avatar?v={row.revision}' if workspace.avatar_data else None,'branding':branding_payload(db,workspace_id),'idle_minutes':row.idle_minutes}


@router.post('/workspaces/{workspace_id}/workspace-settings/general')
async def general(workspace_id:uuid.UUID,request:Request,name:str=Form(...),expected_revision:int=Form(...),remove_avatar:bool=Form(False),avatar:Optional[UploadFile]=File(None),timezone:Optional[str]=Form(None,max_length=64),db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'manage_workspace',True)
 if not name.strip() or len(name.strip())>100 or any(ord(c)<32 for c in name):raise HTTPException(422,'Enter a workspace name of 1–100 characters.')
 row=policy(db,workspace_id,True);revision(row,expected_revision)
 if timezone is not None:
  from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
  try:ZoneInfo(timezone)
  except (ZoneInfoNotFoundError,ValueError):raise HTTPException(422,'Choose a valid IANA workspace timezone.') from None
  row.timezone=timezone
 if avatar:workspace.avatar_data=image_bytes(await avatar.read(10*1024*1024+1),True)
 elif remove_avatar:workspace.avatar_data=None
 workspace.name=name.strip();row.revision+=1;db.commit()
 return overview(workspace_id,request,db)


class IPBody(StrictModel):
 expected_revision:StrictInt=Field(ge=1)
 enabled:bool
 exempt_admins:bool
 ranges:List[dict]=Field(max_length=100)


@router.get('/workspaces/{workspace_id}/workspace-settings/ip-allowlist')
def ip_config(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'view_ip_allowlist');row=policy(db,workspace_id)
 return {'enabled':row.ip_enabled,'exempt_admins':row.exempt_admins,'ranges':row.ip_ranges,'revision':row.revision,'current_ip':str(security.peer_ip(request) or 'Unavailable'),'can_manage':p.allowed(db,workspace_id,'manage_ip_allowlist')}


@router.post('/workspaces/{workspace_id}/workspace-settings/ip-allowlist')
def save_ip(workspace_id:uuid.UUID,body:IPBody,request:Request,db:Session=Depends(get_db)):
 session,workspace,_=actor(request,db,workspace_id,'manage_ip_allowlist',True);entitlement(request,workspace,'total')
 ranges=security.validate_ranges(body.ranges);member=p.membership(db,workspace_id,session.user_id)
 if body.enabled and not ranges:raise HTTPException(422,'Add at least one allowed IP range before enabling the allowlist.')
 if body.enabled and not (body.exempt_admins and security.admin(db,member)) and not security.ip_allowed(security.peer_ip(request),ranges):raise HTTPException(422,'This would block your current IP address. Include it in a range or exempt administrators before saving.')
 row=policy(db,workspace_id,True);revision(row,body.expected_revision);row.ip_enabled=body.enabled;row.exempt_admins=body.exempt_admins;row.ip_ranges=ranges;row.revision+=1;db.commit()
 return {'revision':row.revision}


def entitlement(request,workspace,tier):
 if not request.app.state.settings.stripe_secret_key.get_secret_value():return
 allowed={'enterprise'} if tier=='enterprise' else {'total','enterprise'}
 if workspace.plan_name.lower().replace(' plan','') not in allowed:raise HTTPException(403,'This setting requires the '+tier.title()+' plan.')


@router.get('/workspaces/{workspace_id}/workspace-settings/branding')
def get_branding(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'view_whitelabelling');row=policy(db,workspace_id)
 return {**branding_payload(db,workspace_id),'colors':row.colors,'can_manage':p.allowed(db,workspace_id,'manage_whitelabelling')}


@router.post('/workspaces/{workspace_id}/workspace-settings/branding')
async def save_branding(workspace_id:uuid.UUID,request:Request,expected_revision:int=Form(...),enabled:bool=Form(True),colors:str=Form('{}'),remove_main:bool=Form(False),remove_square:bool=Form(False),main_logo:Optional[UploadFile]=File(None),square_logo:Optional[UploadFile]=File(None),db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'manage_whitelabelling',True);entitlement(request,workspace,'enterprise')
 try:
  parsed=json.loads(colors)
  if not isinstance(parsed,dict) or set(parsed)-{'primary','background','sidebar','text'} or any(not isinstance(v,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',v) for v in parsed.values()):raise ValueError
 except (ValueError,TypeError):raise HTTPException(422,'Use six-digit hex colours for primary, background, sidebar, and text.') from None
 row=policy(db,workspace_id,True);revision(row,expected_revision)
 for kind,upload,remove,square in [('main_logo',main_logo,remove_main,False),('square_logo',square_logo,remove_square,True)]:
  if upload:setattr(row,kind,image_bytes(await upload.read(10*1024*1024+1),square))
  elif remove:setattr(row,kind,None)
 row.colors=parsed;row.branding_enabled=enabled;row.revision+=1;db.commit();return branding_payload(db,workspace_id)


@router.get('/workspaces/{workspace_id}/branding')
def public_branding(workspace_id:uuid.UUID,db:Session=Depends(get_db)):
 workspace=db.get(Workspace,workspace_id);row=db.get(WorkspaceSettings,workspace_id)
 if not workspace or not row or not row.branding_enabled:raise HTTPException(404,'Branding not found.')
 return {'name':workspace.name,**branding_payload(db,workspace_id)}


@router.get('/workspaces/{workspace_id}/branding/{kind}')
def logo(workspace_id:uuid.UUID,kind:Literal['main','square'],db:Session=Depends(get_db)):
 row=db.get(WorkspaceSettings,workspace_id);data=getattr(row,kind+'_logo') if row and row.branding_enabled else None
 if not data:raise HTTPException(404,'Logo not found.')
 return Response(data,media_type='image/png',headers={'Cache-Control':'public,max-age=300'})


def role_payload(row):return {key:getattr(row,key) for key in ['id','name','description','kind','permissions','protected','revision']}


@router.get('/workspaces/{workspace_id}/workspace-settings/roles')
def roles(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_workspace_roles');p.bootstrap(db,workspace_id);db.commit()
 return {'roles':[role_payload(row) for row in db.scalars(select(WorkspaceRole).where(WorkspaceRole.workspace_id==workspace_id).order_by(WorkspaceRole.protected,WorkspaceRole.name_key))],'catalog':p.catalog()}


class RoleBody(StrictModel):
 name:str=Field(min_length=1,max_length=100)
 description:str=Field(default='',max_length=2000)
 kind:Literal['workspace','team']='workspace'
 permissions:List[str]=Field(default_factory=list,max_length=200)
 expected_revision:Optional[StrictInt]=Field(default=None,ge=1)


def save_role(db,workspace_id,body,identifier=None):
 key=name_key(body.name)
 if not key or any(ord(c)<32 for c in body.name):raise HTTPException(422,'Name: enter a valid role name.')
 if not set(body.permissions)<=(p.ALL if body.kind=='workspace' else p.TEAM):raise HTTPException(422,'Select only permissions available for this role type.')
 row=db.scalar(select(WorkspaceRole).where(WorkspaceRole.workspace_id==workspace_id,WorkspaceRole.id==identifier).with_for_update()) if identifier else None
 if identifier and not row:raise HTTPException(404,'Role not found.')
 if row:
  if row.protected:raise HTTPException(403,'The default Admin role is protected.')
  revision(row,body.expected_revision)
  if row.kind!=body.kind:raise HTTPException(422,'Create a new role to change its type. Existing assignments keep their scope.')
 else:row=WorkspaceRole(id=uuid.uuid4(),workspace_id=workspace_id)
 duplicate=db.scalar(select(WorkspaceRole.id).where(WorkspaceRole.workspace_id==workspace_id,WorkspaceRole.name_key==key,WorkspaceRole.id!=row.id))
 if duplicate:raise HTTPException(422,'Name: a role with this name already exists.')
 db.add(row)
 row.name=body.name.strip();row.name_key=key;row.description=body.description.strip();row.kind=body.kind;row.permissions=sorted(set(body.permissions));row.revision=(row.revision or 0)+1
 try:db.commit()
 except IntegrityError:db.rollback();raise HTTPException(422,'Name: a role with this name already exists.') from None
 return role_payload(row)


@router.post('/workspaces/{workspace_id}/workspace-settings/roles',status_code=201)
def create_role(workspace_id:uuid.UUID,body:RoleBody,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_workspace_roles',True);return save_role(db,workspace_id,body)


@router.post('/workspaces/{workspace_id}/workspace-settings/roles/{identifier}')
def edit_role(workspace_id:uuid.UUID,identifier:uuid.UUID,body:RoleBody,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_workspace_roles',True);return save_role(db,workspace_id,body,identifier)


class Selection(StrictModel):
 ids:List[uuid.UUID]=Field(min_length=1,max_length=200)


@router.post('/workspaces/{workspace_id}/workspace-settings/roles-delete')
def remove_roles(workspace_id:uuid.UUID,body:Selection,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_workspace_roles',True)
 rows=db.scalars(select(WorkspaceRole).where(WorkspaceRole.workspace_id==workspace_id,WorkspaceRole.id.in_(body.ids)).with_for_update()).all()
 if len(rows)!=len(set(body.ids)):raise HTTPException(404,'A selected role was not found.')
 if any(row.protected for row in rows):raise HTTPException(403,'The default Admin role cannot be deleted.')
 for row in rows:
  # FK SET NULL preserves teams, users, teamspaces and all memberships.
  db.delete(row)
 db.commit();return {'deleted':len(rows)}


def spaces_payload(db,workspace_id):return [{'id':str(t.id),'name':t.name,'is_default':t.is_default,'revision':t.revision} for t in db.scalars(select(Teamspace).where(Teamspace.workspace_id==workspace_id).order_by(Teamspace.created_at if hasattr(Teamspace,'created_at') else Teamspace.name))]


@router.get('/workspaces/{workspace_id}/workspace-settings/teamspaces')
def spaces(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id);p.bootstrap(db,workspace_id);db.commit();ids=p.accessible_spaces(db,workspace_id)
 return {'items':[t for t in spaces_payload(db,workspace_id) if t['id'] in ids],'can_manage':p.allowed(db,workspace_id,'manage_teamspaces')}


class SpaceBody(StrictModel):
 name:str=Field(min_length=1,max_length=100)


@router.post('/workspaces/{workspace_id}/workspace-settings/teamspaces',status_code=201)
def add_space(workspace_id:uuid.UUID,body:SpaceBody,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_teamspaces',True)
 key=name_key(body.name)
 if not key or db.scalar(select(Teamspace.id).where(Teamspace.workspace_id==workspace_id,Teamspace.name_key==key)):raise HTTPException(422,'Use a unique teamspace name.')
 row=Teamspace(workspace_id=workspace_id,name=body.name.strip(),name_key=key);db.add(row);db.commit();return {'id':str(row.id),'name':row.name,'is_default':False,'revision':row.revision}


class AgentSpace(StrictModel):
 teamspace_id:uuid.UUID
 expected_revision:StrictInt=Field(ge=1)


@router.post('/workspaces/{workspace_id}/workspace-settings/agents/{identifier}/teamspace')
def move_agent(workspace_id:uuid.UUID,identifier:uuid.UUID,body:AgentSpace,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_teamspaces',True)
 row=db.scalar(select(Agent).where(Agent.workspace_id==workspace_id,Agent.id==identifier).with_for_update());space=db.get(Teamspace,body.teamspace_id)
 if not row or not space or space.workspace_id!=workspace_id:raise HTTPException(404,'Agent or teamspace not found.')
 revision(row,body.expected_revision);row.teamspace_id=space.id;row.revision+=1;db.commit();return {'revision':row.revision}


def user_options(db,workspace_id):
 return {'roles':[role_payload(t) for t in db.scalars(select(WorkspaceRole).where(WorkspaceRole.workspace_id==workspace_id)) if t.kind=='workspace'],'teams':[{'id':str(t.id),'name':t.name,'role_id':str(t.role_id) if t.role_id else None,'teamspace_ids':t.teamspace_ids} for t in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)))],'teamspaces':spaces_payload(db,workspace_id)}


@router.get('/workspaces/{workspace_id}/workspace-settings/users')
def users(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_users');p.bootstrap(db,workspace_id);db.commit()
 from app.inbox.service import roster
 people={str(u['id']):u for u in roster(db,workspace_id)}
 result=[]
 for member,user in db.execute(select(Membership,User).join(User,User.id==Membership.user_id).where(Membership.workspace_id==workspace_id).order_by(Membership.created_at)):
  person=people.get(str(user.id),{})
  result.append({'id':str(user.id),'name':user.full_name or user.email.split('@')[0],'email':user.email,'role':member.role,'role_id':str(member.custom_role_id) if member.custom_role_id else None,'team_ids':[str(t.id) for t in p.teams_for(db,workspace_id,user.id)],'teamspace_ids':member.teamspace_ids,'availability':person['presence'] if person.get('presence') in {'away','offline'} else 'online' if person.get('available') else 'signed_out','online':person.get('available',False),'protected':member.role=='owner'})
 return {'users':result,**user_options(db,workspace_id)}


class UserBody(StrictModel):
 role_id:Optional[uuid.UUID]=None
 team_ids:List[uuid.UUID]=Field(default_factory=list,max_length=200)
 teamspace_ids:Optional[List[uuid.UUID]]=Field(default=None,max_length=200)


def checked_assignment(db,workspace_id,body):
 role=db.get(WorkspaceRole,body.role_id) if body.role_id else None
 if body.role_id and (not role or role.workspace_id!=workspace_id or role.kind!='workspace'):raise HTTPException(422,'Select a workspace role.')
 actor_grants=p.workspace_grants(db,workspace_id)
 if role and not p.allowed(db,workspace_id,'manage_workspace_roles') and not set(role.permissions)<=actor_grants:raise HTTPException(403,'You cannot assign a role with permissions you do not hold.')
 teams=list(db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False),SupportTeam.id.in_(body.team_ids))))
 if len(teams)!=len(set(body.team_ids)):raise HTTPException(422,'Choose current teams in this workspace.')
 for team in teams:
  team_role=db.get(WorkspaceRole,team.role_id) if team.role_id else None
  if team_role and not p.allowed(db,workspace_id,'manage_workspace_roles') and not set(team_role.permissions)<=actor_grants:raise HTTPException(403,'You cannot assign a team with permissions you do not hold.')
 if body.teamspace_ids is not None:
  all_ids={str(t) for t in db.scalars(select(Teamspace.id).where(Teamspace.workspace_id==workspace_id))}
  if not set(map(str,body.teamspace_ids))<=all_ids:raise HTTPException(422,'Select teamspaces in this workspace.')
 return role,teams


def assign(db,workspace_id,member,body):
 role,teams=checked_assignment(db,workspace_id,body)
 member.custom_role_id=role.id if role else None
 member.role='member'
 member.teamspace_ids=list(map(str,body.teamspace_ids)) if body.teamspace_ids is not None else None
 selected={t.id for t in teams}
 for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id,SupportTeam.archived.is_(False)).with_for_update()):
  old=set(team.member_ids or []);new=(old|{str(member.user_id)}) if team.id in selected else old-{str(member.user_id)}
  if old!=new:team.member_ids=sorted(new);team.revision+=1


@router.post('/workspaces/{workspace_id}/workspace-settings/users/{identifier}')
def update_user(workspace_id:uuid.UUID,identifier:uuid.UUID,body:UserBody,request:Request,db:Session=Depends(get_db)):
 session,_,_=actor(request,db,workspace_id,'manage_users',True)
 member=p.membership(db,workspace_id,identifier)
 if not member:raise HTTPException(404,'User not found.')
 if member.role=='owner' or identifier==session.user_id:raise HTTPException(403,'The owner and your own access are protected. Ask another administrator to change your role.')
 if security.admin(db,member) and not p.allowed(db,workspace_id,'manage_workspace_roles'):raise HTTPException(403,'Managing an administrator requires Manage user roles & permissions.')
 assign(db,workspace_id,member,body);db.commit();return {'saved':True}


@router.post('/workspaces/{workspace_id}/workspace-settings/users-delete')
def delete_users(workspace_id:uuid.UUID,body:Selection,request:Request,db:Session=Depends(get_db)):
 session,_,_=actor(request,db,workspace_id,'manage_users',True)
 members=list(db.scalars(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id.in_(body.ids)).with_for_update()))
 if len(members)!=len(set(body.ids)):raise HTTPException(404,'A selected user was not found.')
 if any(m.role=='owner' or m.user_id==session.user_id for m in members):raise HTTPException(403,'The owner and your own access cannot be removed.')
 if any(security.admin(db,m) for m in members) and not p.allowed(db,workspace_id,'manage_workspace_roles'):raise HTTPException(403,'Removing administrators requires Manage user roles & permissions.')
 ids={str(i) for i in body.ids}
 active=db.scalar(select(AgentConversation.id).where(AgentConversation.workspace_id==workspace_id,AgentConversation.live_status=='active',AgentConversation.live_assigned_user_id.in_(body.ids)).limit(1))
 if active:raise HTTPException(409,'Transfer active livechats before removing these users.')
 for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id).with_for_update()):
  if set(team.member_ids)&ids:team.member_ids=[i for i in team.member_ids if i not in ids];team.revision+=1
 for member in members:db.delete(member)
 db.execute(update(AuthSession).where(AuthSession.user_id.in_(body.ids),AuthSession.active_workspace_id==workspace_id).values(active_workspace_id=None))
 db.commit();return {'deleted':len(members)}


class InviteBody(UserBody):
 email:EmailStr


def invite_payload(row):
 status='accepted' if row.accepted_at else 'revoked' if row.revoked_at else 'expired' if row.expires_at<=utcnow() else 'pending'
 return {'id':str(row.id),'email':row.email,'role_id':str(row.role_id) if row.role_id else None,'created_at':row.created_at,'expires_at':row.expires_at,'status':status,'team_ids':row.team_ids,'teamspace_ids':row.teamspace_ids}


@router.get('/workspaces/{workspace_id}/workspace-settings/invites')
def invites(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_users')
 return {'items':[invite_payload(t) for t in db.scalars(select(WorkspaceInvite).where(WorkspaceInvite.workspace_id==workspace_id).order_by(WorkspaceInvite.created_at.desc()))]}


@router.post('/workspaces/{workspace_id}/workspace-settings/invites',status_code=201)
def invite(workspace_id:uuid.UUID,body:InviteBody,request:Request,db:Session=Depends(get_db)):
 _,workspace,_=actor(request,db,workspace_id,'manage_users',True);checked_assignment(db,workspace_id,body)
 from app.models import RateLimit
 from sqlalchemy import func
 bucket=int(utcnow().timestamp())//60
 key=token_hash('invite:'+str(workspace_id)+':'+str(bucket))
 count=db.execute(insert(RateLimit).values(key=key,count=1,expires_at=utcnow()+timedelta(minutes=2)).on_conflict_do_update(index_elements=[RateLimit.key],set_={'count':RateLimit.count+1}).returning(RateLimit.count)).scalar_one()
 if count>10:db.commit();raise HTTPException(429,'Send up to ten invitations per minute.')
 email=str(body.email).lower();existing=db.scalar(select(User.id).join(Membership,Membership.user_id==User.id).where(Membership.workspace_id==workspace_id,User.email==email))
 if existing:raise HTTPException(409,'This account is already a workspace member.')
 raw=new_token();now=utcnow()
 db.execute(update(WorkspaceInvite).where(WorkspaceInvite.workspace_id==workspace_id,WorkspaceInvite.email==email,WorkspaceInvite.accepted_at.is_(None)).values(revoked_at=now))
 row=WorkspaceInvite(workspace_id=workspace_id,email=email,token_hash=token_hash(raw),role_id=body.role_id,team_ids=list(map(str,body.team_ids)),teamspace_ids=list(map(str,body.teamspace_ids)) if body.teamspace_ids is not None else None,expires_at=now+timedelta(days=7));db.add(row);db.commit()
 link=request.app.state.settings.frontend_origin+'/join-workspace?token='+raw
 try:request.app.state.mailer.send_invitation(email,link,workspace.name)
 except Exception:
  row.revoked_at=utcnow();db.commit();raise HTTPException(503,'The invitation email could not be sent. Check SMTP settings and send a new invitation.') from None
 return invite_payload(row)


@router.post('/workspaces/{workspace_id}/workspace-settings/invites/{identifier}/revoke')
def revoke_invite(workspace_id:uuid.UUID,identifier:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_users',True);row=db.get(WorkspaceInvite,identifier)
 if not row or row.workspace_id!=workspace_id:raise HTTPException(404,'Invitation not found.')
 if row.accepted_at:raise HTTPException(409,'This invitation has already been accepted.')
 row.revoked_at=utcnow();db.commit();return {'revoked':True}


class AcceptInvite(StrictModel):
 token:str=Field(min_length=20,max_length=256)


@router.post('/workspace-invites/accept')
def accept_invite(body:AcceptInvite,request:Request,db:Session=Depends(get_db)):
 session=current_session(request,db);require_csrf(request,session);now=utcnow()
 row=db.scalar(select(WorkspaceInvite).where(WorkspaceInvite.token_hash==token_hash(body.token)).with_for_update())
 if not row or row.accepted_at or row.revoked_at or row.expires_at<=now:raise HTTPException(400,'This invitation is invalid, expired, or already used.')
 user=db.get(User,session.user_id)
 if user.email!=row.email:raise HTTPException(403,'Sign in with the email address that received this invitation.')
 member=p.membership(db,row.workspace_id,user.id)
 if not member:
  member=Membership(workspace_id=row.workspace_id,user_id=user.id,role='member');db.add(member);db.flush()
  # Validate references afresh; revoked roles must not silently become Admin.
  role=db.get(WorkspaceRole,row.role_id) if row.role_id else None
  member.custom_role_id=role.id if role and role.kind=='workspace' and role.workspace_id==row.workspace_id else None
  member.teamspace_ids=row.teamspace_ids
  for team in db.scalars(select(SupportTeam).where(SupportTeam.workspace_id==row.workspace_id,SupportTeam.archived.is_(False),SupportTeam.id.in_([uuid.UUID(i) for i in row.team_ids])).with_for_update()):
   team.member_ids=sorted(set(team.member_ids)|{str(user.id)});team.revision+=1
 row.accepted_at=now;session.active_workspace_id=row.workspace_id;session.last_activity_at=now;db.commit();return user_context(db,user,row.workspace_id)


class IdleBody(StrictModel):
 expected_revision:StrictInt=Field(ge=1)
 minutes:StrictInt=Field(ge=0,le=1440)


@router.get('/workspaces/{workspace_id}/workspace-settings/idle-logout')
def idle(workspace_id:uuid.UUID,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_users');row=policy(db,workspace_id);return {'minutes':row.idle_minutes,'revision':row.revision}


@router.post('/workspaces/{workspace_id}/workspace-settings/idle-logout')
def set_idle(workspace_id:uuid.UUID,body:IdleBody,request:Request,db:Session=Depends(get_db)):
 actor(request,db,workspace_id,'manage_users',True)
 if body.minutes and body.minutes<5:raise HTTPException(422,'Choose 5–1440 minutes, or 0 to disable idle logout.')
 row=policy(db,workspace_id,True);revision(row,body.expected_revision);row.idle_minutes=body.minutes;row.revision+=1
 ids=select(Membership.user_id).where(Membership.workspace_id==workspace_id)
 db.execute(update(AuthSession).where(AuthSession.user_id.in_(ids),AuthSession.revoked_at.is_(None)).values(last_activity_at=utcnow()))
 db.commit();return {'minutes':row.idle_minutes,'revision':row.revision}


@router.post('/auth/activity')
def activity(request:Request,db:Session=Depends(get_db)):
 session=current_session(request,db);require_csrf(request,session);session.last_activity_at=utcnow();db.commit();return {'active':True}
