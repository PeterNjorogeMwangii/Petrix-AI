import ipaddress
import re
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from app.core.security import utcnow
from app.models import Membership, WorkspaceSettings, WorkspaceRole


def peer_ip(request):
 # Uvicorn proxy forwarding is disabled in the supplied start commands. Only the
 # socket peer is trusted; an untrusted X-Forwarded-For must never bypass policy.
 value=request.client.host if request.client else ''
 try:return ipaddress.ip_address(value)
 except ValueError:return None


def admin(db,member):
 if not member:return False
 if member.role=='owner':return True
 if member.custom_role_id:
  role=db.get(WorkspaceRole,member.custom_role_id)
  return bool(role and role.protected)
 return member.role=='admin'


def ip_allowed(ip,ranges):
 return bool(ip and any(ip.version==ipaddress.ip_address(item['start']).version and int(ipaddress.ip_address(item['start']))<=int(ip)<=int(ipaddress.ip_address(item['end'])) for item in ranges))


def validate_ranges(ranges):
 if len(ranges)>100:raise HTTPException(422,'Add up to 100 IP ranges.')
 result=[]
 for item in ranges:
  try:
   start=ipaddress.ip_address(item['start'].strip());end=ipaddress.ip_address(item['end'].strip())
   if start.version!=end.version or int(start)>int(end):raise ValueError
  except (KeyError,AttributeError,ValueError):raise HTTPException(422,'Each range needs valid Start and End addresses of the same IP version, in ascending order.') from None
  result.append({'start':str(start),'end':str(end)})
 return result


def enforce(request,db,session):
 db.info['actor_id']=session.user_id
 match=re.match(r'/api/workspaces/([0-9a-fA-F-]{36})(?:/|$)',request.url.path)
 workspace_id=match.group(1) if match else session.active_workspace_id
 if not workspace_id:
  workspace_id=db.scalar(select(Membership.workspace_id).where(Membership.user_id==session.user_id).order_by(Membership.created_at,Membership.id).limit(1))
 if not workspace_id:return
 import uuid
 workspace_id=uuid.UUID(str(workspace_id))
 member=db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==session.user_id))
 if not member:return
 policy=db.get(WorkspaceSettings,workspace_id)
 if not policy:return
 now=utcnow()
 if policy.idle_minutes and (session.last_activity_at or session.created_at)+timedelta(minutes=policy.idle_minutes)<=now:
  session.revoked_at=now;db.commit()
  raise HTTPException(401,'You were signed out due to inactivity. Please sign in again.')
 if request.url.path in {'/api/auth/logout','/api/workspaces'}:return
 if policy.ip_enabled and not (policy.exempt_admins and admin(db,member)) and not ip_allowed(peer_ip(request),policy.ip_ranges):
  raise HTTPException(403,'Your IP address is outside this workspace’s allowed ranges.')
