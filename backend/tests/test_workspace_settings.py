import io
import json
import uuid
from datetime import timedelta
from urllib.parse import urlsplit,parse_qs
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import Workspace,Membership,WorkspaceRole,WorkspaceSettings,WorkspaceInvite,Teamspace,SupportTeam,User,AuthSession,Agent,AgentConversation,AgentMessage,UsageEvent
from app.workspace_settings import permissions as grants
from test_agents import agent_workspace

@pytest.fixture
def ws(client,agent_workspace):
 base,agent,headers=agent_workspace
 root=base.rsplit('/agents',1)[0];path=root+'/workspace-settings'
 result=client.get(path);assert result.status_code==200,result.text
 return root,path,agent,headers

def picture(kind='PNG'):
 output=io.BytesIO();Image.new('RGBA' if kind!='JPEG' else 'RGB',(300,150),'orange').save(output,kind);return output.getvalue()

def role(client,path,headers,permissions=None,kind='workspace',name='Reviewers'):
 result=client.post(path+'/roles',json={'name':name,'kind':kind,'permissions':permissions or []},headers=headers)
 assert result.status_code==201,result.text
 return result.json()

def custom_actor(app,workspace_id,permissions,kind='workspace'):
 with app.state.session_factory() as db:
  member=db.scalar(select(Membership).where(Membership.workspace_id==uuid.UUID(workspace_id),Membership.role=='owner'))
  r=WorkspaceRole(workspace_id=member.workspace_id,name='Restricted',name_key='restricted',kind=kind,permissions=permissions);db.add(r);db.flush();member.role='member';member.custom_role_id=r.id if kind=='workspace' else None
  if kind=='team':
   base=WorkspaceRole(workspace_id=member.workspace_id,name='Base access',name_key='base-access',kind='workspace',permissions=[]);db.add(base);db.flush();member.custom_role_id=base.id
   db.add(SupportTeam(workspace_id=member.workspace_id,name='Assigned team',alternative_names=[],description='',member_ids=[str(member.user_id)],role_id=r.id))
  db.commit();return member.user_id,r.id


def test_general_avatar_cache_revision_and_context(client,ws):
 root,path,_,headers=ws;value=client.get(path).json()
 result=client.post(path+'/general',data={'name':'Acme Support','expected_revision':value['revision']},files={'avatar':('avatar.gif',picture('GIF'),'image/gif')},headers=headers)
 assert result.status_code==200,result.text
 context=client.get('/api/auth/me').json();assert context['workspace']['name']=='Acme Support'
 first=context['workspace']['avatar_url'];assert client.get(first).content.startswith(b'\x89PNG')
 assert client.post(path+'/general',data={'name':'Stale','expected_revision':value['revision']},headers=headers).status_code==409
 result=client.post(path+'/general',data={'name':'Acme Support','expected_revision':result.json()['revision'],'remove_avatar':'true'},headers=headers)
 assert result.json()['avatar_url'] is None and client.get(root+'/avatar').status_code==404
 assert client.post(path+'/general',data={'name':'x','expected_revision':result.json()['revision']},files={'avatar':('bad.png',b'broken','image/png')},headers=headers).status_code==422

@pytest.mark.parametrize('name',['',' ','x'*101,'Bad\nName'])
def test_general_validation(client,ws,name):
 _,path,_,headers=ws;assert client.post(path+'/general',data={'name':name,'expected_revision':1},headers=headers).status_code==422


def test_roles_unique_protected_independent_groups(client,ws):
 _,path,_,headers=ws;listed=client.get(path+'/roles').json();admin=next(r for r in listed['roles'] if r['protected'])
 assert len(listed['catalog'])==10
 created=role(client,path,headers,['view_billing','manage_users'])
 assert created['permissions']==['manage_users','view_billing']
 assert client.post(path+'/roles',json={'name':'REVIEWERS','kind':'workspace','permissions':[]},headers=headers).status_code==422
 assert client.post(path+'/roles',json={'name':'Bad','kind':'team','permissions':['manage_workspace']},headers=headers).status_code==422
 assert client.post(path+'/roles/'+admin['id'],json={'name':'Other','kind':'workspace','permissions':[],'expected_revision':1},headers=headers).status_code==403
 assert client.post(path+'/roles-delete',json={'ids':[admin['id'],created['id']]},headers=headers).status_code==403
 updated=client.post(path+'/roles/'+created['id'],json={'name':'Reviewers renamed','kind':'workspace','permissions':['view_billing'],'expected_revision':created['revision']},headers=headers)
 assert updated.status_code==200,updated.text
 assert client.post(path+'/roles/'+created['id'],json={'name':'Old','kind':'workspace','permissions':[],'expected_revision':1},headers=headers).status_code==409
 assert client.post(path+'/roles-delete',json={'ids':[created['id']]},headers=headers).status_code==200


def test_team_role_delete_preserves_every_assignment(client,app,ws):
 root,path,_,headers=ws;r=role(client,path,headers,['view_tickets_all'],'team','Team Agent')
 owner=client.get('/api/auth/me').json()['user']['id'];space=client.get(path+'/teamspaces').json()['items'][0]['id']
 team=client.post(root+'/teams',json={'name':'Support','alternative_names':[],'member_ids':[owner],'role_id':r['id'],'teamspace_ids':[space]},headers=headers)
 assert team.status_code==201,team.text
 assert team.json()['role_id']==r['id']
 assert client.post(path+'/roles-delete',json={'ids':[r['id']]},headers=headers).status_code==200
 after=client.get(root+'/teams').json()['teams'][0]
 assert after['id']==team.json()['id'] and after['member_ids']==[owner] and after['teamspace_ids']==[space] and after['role_id'] is None

@pytest.mark.parametrize('permission,path_suffix,status',[('view_ai_agents','/agents',200),('manage_ai_agents','/agents',403),('view_billing','/workspace-settings/billing',200),('manage_billing','/workspace-settings/billing',403),('manage_users','/workspace-settings/roles',403)])
def test_permissions_are_independent(client,app,ws,permission,path_suffix,status):
 root,path,_,headers=ws;custom_actor(app,root.split('/')[3],[permission])
 result=client.get(root+path_suffix);assert result.status_code==status,result.text
 assert client.post(path+'/general',data={'name':'Forbidden','expected_revision':1},headers=headers).status_code==403
 assert client.get('/api/auth/me').json()['workspace']['permissions'][permission]


def test_custom_manage_ai_cannot_manage_users_teams_billing(client,app,ws):
 root,path,_,headers=ws;custom_actor(app,root.split('/')[3],['view_ai_agents','manage_ai_agents'])
 assert client.post(root+'/agents',data={'name':'Allowed'},headers=headers).status_code==201
 assert client.post(root+'/teams',json={'name':'Denied'},headers=headers).status_code==403
 assert client.get(path+'/users').status_code==403
 assert client.get(path+'/billing').status_code==403
 assert client.post(root+'/roles',json={'admin':True,'member':True},headers=headers).status_code==403


def test_teamspace_restrictions_and_team_role_scope(client,app,ws):
 root,path,agent,headers=ws;second=client.post(path+'/teamspaces',json={'name':'Private'},headers=headers).json()
 first=client.get(path+'/teamspaces').json()['items'];default=next(s for s in first if s['is_default'])
 custom_actor(app,root.split('/')[3],['view_ai_agents','manage_ai_agents'],'team')
 assert client.get(root+'/agents').status_code==200
 with app.state.session_factory() as db:
  row=db.get(Agent,uuid.UUID(agent['id']));row.teamspace_id=uuid.UUID(second['id']);db.commit()
 assert client.get(root+'/agents/'+agent['id']+'/versions').status_code==403
 assert client.post(root+'/agents/'+agent['id']+'/identity',data={'name':'Denied','expected_revision':1},headers=headers).status_code==403
 assert client.post(path+'/teamspaces',json={'name':'Denied'},headers=headers).status_code==403


def test_users_role_teamspace_edit_delete_and_owner_protected(client,app,ws):
 root,path,_,headers=ws;r=role(client,path,headers,['view_ai_agents'])
 with app.state.session_factory() as db:
  user=User(email='member@example.com',full_name='Member');db.add(user);db.flush();db.add(Membership(user_id=user.id,workspace_id=uuid.UUID(root.split('/')[3]),role='member'));db.commit();identifier=str(user.id)
 team=client.post(root+'/teams',json={'name':'Member team','member_ids':[]},headers=headers).json()
 space=client.get(path+'/teamspaces').json()['items'][0]['id']
 result=client.post(path+'/users/'+identifier,json={'role_id':r['id'],'team_ids':[team['id']],'teamspace_ids':[space]},headers=headers)
 assert result.status_code==200,result.text
 user=next(u for u in client.get(path+'/users').json()['users'] if u['id']==identifier)
 assert user['role_id']==r['id'] and user['teamspace_ids']==[space] and user['team_ids']==[team['id']]
 owner=client.get('/api/auth/me').json()['user']['id']
 assert client.post(path+'/users-delete',json={'ids':[owner]},headers=headers).status_code==403
 assert client.post(path+'/users-delete',json={'ids':[identifier]},headers=headers).status_code==200
 assert client.get(root+'/teams').json()['teams'][0]['member_ids']==[]
 with app.state.session_factory() as db:assert db.get(User,uuid.UUID(identifier)) is not None


def test_manage_users_cannot_assign_higher_grants(client,app,ws):
 root,path,_,headers=ws;admin=next(r for r in client.get(path+'/roles').json()['roles'] if r['protected'])
 with app.state.session_factory() as db:
  user=User(email='target@example.com');db.add(user);db.flush();db.add(Membership(user_id=user.id,workspace_id=uuid.UUID(root.split('/')[3]),role='member'));db.commit();identifier=str(user.id)
 custom_actor(app,root.split('/')[3],['manage_users'])
 assert client.post(path+'/users/'+identifier,json={'role_id':admin['id'],'team_ids':[]},headers=headers).status_code==403


def test_invitation_verified_email_expiry_single_use_and_revoke(client,app,ws):
 root,path,_,headers=ws;mail=[];app.state.mailer.send_invitation=lambda email,link,name:mail.append((email,link,name))
 invited=client.post(path+'/invites',json={'email':'colleague@example.com','team_ids':[]},headers=headers)
 assert invited.status_code==201,invited.text
 token=parse_qs(urlsplit(mail[0][1]).query)['token'][0]
 assert client.post('/api/workspace-invites/accept',json={'token':token},headers=headers).status_code==403
 with TestClient(app,base_url='http://localhost:4200') as other:
  assert other.post('/api/auth/magic-link',json={'email':'colleague@example.com'}).status_code==202
  assert other.post('/api/auth/verify',json={'token':app.state.mailer.token}).status_code==200
  h={'X-Petrix-CSRF':other.cookies.get('petrix_csrf')}
  accepted=other.post('/api/workspace-invites/accept',json={'token':token},headers=h)
  assert accepted.status_code==200,accepted.text
  assert accepted.json()['workspace']['id']==root.split('/')[3]
  assert other.post('/api/workspace-invites/accept',json={'token':token},headers=h).status_code==400
 assert client.get(path+'/invites').json()['items'][0]['status']=='accepted'
 second=client.post(path+'/invites',json={'email':'another@example.com'},headers=headers).json()
 assert client.post(path+'/invites/'+second['id']+'/revoke',json={},headers=headers).status_code==200
 assert client.get(path+'/invites').json()['items'][0]['status']=='revoked'


def test_invitation_smtp_failure_does_not_leave_live_invite(client,app,ws):
 _,path,_,headers=ws
 def fail(*args):raise OSError('private smtp data')
 app.state.mailer.send_invitation=fail
 result=client.post(path+'/invites',json={'email':'new@example.com'},headers=headers)
 assert result.status_code==503 and 'private' not in result.text
 assert client.get(path+'/invites').json()['items'][0]['status']=='revoked'

@pytest.mark.parametrize('ranges',[[{'start':'bad','end':'bad'}],[{'start':'1.2.3.10','end':'1.2.3.1'}],[{'start':'1.2.3.4','end':'::1'}],[{}]])
def test_invalid_ip_ranges(client,ws,ranges):
 _,path,_,headers=ws
 assert client.post(path+'/ip-allowlist',json={'expected_revision':1,'enabled':False,'exempt_admins':True,'ranges':ranges},headers=headers).status_code==422


def test_ip_allowlist_self_lockout_and_enforced_across_endpoints(client,app,ws):
 root,path,_,headers=ws
 body={'expected_revision':1,'enabled':True,'exempt_admins':False,'ranges':[{'start':'192.0.2.1','end':'192.0.2.255'}]}
 assert client.post(path+'/ip-allowlist',json=body,headers={**headers,'X-Forwarded-For':'192.0.2.5'}).status_code==422
 body['exempt_admins']=True;assert client.post(path+'/ip-allowlist',json=body,headers=headers).status_code==200
 assert client.get(root+'/agents').status_code==200
 custom_actor(app,root.split('/')[3],['view_ai_agents'])
 assert client.get(root+'/agents',headers={'X-Forwarded-For':'192.0.2.4'}).status_code==403
 assert client.get('/api/auth/me').status_code==403
 assert client.post('/api/auth/activity',json={},headers=headers).status_code==403


def test_ipv6_ranges_and_plan_entitlements(client,app,ws):
 _,path,_,headers=ws
 body={'expected_revision':1,'enabled':True,'exempt_admins':True,'ranges':[{'start':'2001:db8::1','end':'2001:db8::ffff'}]}
 assert client.post(path+'/ip-allowlist',json=body,headers=headers).status_code==200
 from pydantic import SecretStr
 app.state.settings.stripe_secret_key=SecretStr('sk_test_controlled')
 body['expected_revision']=2
 assert client.post(path+'/ip-allowlist',json=body,headers=headers).status_code==403


def test_idle_logout_polling_does_not_keep_session_alive(client,app,ws):
 root,path,_,headers=ws
 assert client.post(path+'/idle-logout',json={'expected_revision':1,'minutes':5},headers=headers).status_code==200
 with app.state.session_factory() as db:
  session=db.scalar(select(AuthSession).where(AuthSession.revoked_at.is_(None)));session.last_activity_at=utcnow()-timedelta(minutes=4);db.commit();identifier=session.id;before=session.last_activity_at
 assert client.get('/api/auth/me').status_code==200
 with app.state.session_factory() as db:assert db.get(AuthSession,identifier).last_activity_at==before
 assert client.post('/api/auth/activity',json={},headers=headers).status_code==200
 with app.state.session_factory() as db:
  session=db.get(AuthSession,identifier);assert session.last_activity_at>before;session.last_activity_at=utcnow()-timedelta(minutes=6);db.commit()
 assert client.get(root+'/agents').status_code==401
 with app.state.session_factory() as db:assert db.get(AuthSession,identifier).revoked_at is not None


def test_branding_images_colors_public_and_disabled(client,app,ws):
 root,path,_,headers=ws
 result=client.post(path+'/branding',data={'expected_revision':1,'enabled':'true','colors':'{"primary":"#126699","sidebar":"#111111"}'},files={'main_logo':('logo.png',picture(),'image/png'),'square_logo':('square.gif',picture('GIF'),'image/gif')},headers=headers)
 assert result.status_code==200,result.text
 brand=client.get('/api/auth/me').json()['workspace']['branding'];assert brand['colors']['primary']=='#126699'
 with TestClient(app) as public:
  assert public.get(root+'/branding').status_code==200
  image=Image.open(io.BytesIO(public.get(brand['main_logo_url']).content));assert image.size==(300,150)
  image=Image.open(io.BytesIO(public.get(brand['square_logo_url']).content));assert image.size==(256,256)
 assert client.post(path+'/branding',data={'expected_revision':2,'enabled':'true','colors':'{"primary":"url(javascript:bad)"}'},headers=headers).status_code==422
 assert client.post(path+'/branding',data={'expected_revision':2,'enabled':'false'},headers=headers).status_code==200
 assert client.get(root+'/branding').status_code==404


def test_usage_weights_categories_filter_and_no_history_duplicates(client,app,ws):
 root,path,agent,headers=ws;workspace_id=uuid.UUID(root.split('/')[3]);user_id=uuid.UUID(client.get('/api/auth/me').json()['user']['id']);now=utcnow()
 with app.state.session_factory() as db:
  conversation=AgentConversation(workspace_id=workspace_id,agent_id=uuid.UUID(agent['id']),user_id=user_id,agent_name=agent['name']);db.add(conversation);db.flush()
  for role,data in [('user',{}),('assistant',{}),('assistant',{'source':'live_agent'}),('assistant',{'transferred_history':True}),('assistant',{'author_name':'Remote agent'}),('user',{'source_request_id':'same'}),('user',{'source_request_id':'same'})]:db.add(AgentMessage(conversation_id=conversation.id,role=role,content='Private message never returned',message_data=data))
  db.add(UsageEvent(workspace_id=workspace_id,event_key='metered-action',category='sql_query',agent_id=uuid.UUID(agent['id']),source='workflow',units=1));db.commit()
 app.state.settings.usage_weights_json='{"sql_query":2}'
 params={'start':(now-timedelta(days=1)).isoformat(),'end':(now+timedelta(days=1)).isoformat()}
 response=client.get(path+'/usage',params=params);assert response.status_code==200,response.text
 result=response.json();assert result['total']==6 and 'Private message' not in response.text
 counts={r['key']:r['value'] for r in result['breakdown']};assert counts['ai_messages']==1 and counts['people_messages']==3 and counts['sql_query']==2
 assert client.get(path+'/usage',params={**params,'interaction_type':'sql_query'}).json()['total']==2
 assert client.get(path+'/usage',params={**params,'ai_agent':agent['id']}).json()['total']==6
 assert result['peak_day'] and result['peak_hour'] and result['timezone']=='Africa/Nairobi'
 for group in ['hour','day','week','month','quarter','year']:assert client.get(path+'/usage',params={**params,'group':group}).status_code==200
 assert client.get(path+'/billing').status_code==200


def test_mutations_require_csrf_and_cross_workspace_hidden(client,app,ws):
 root,path,_,headers=ws
 assert client.post(path+'/roles',json={'name':'No CSRF'}).status_code==403
 assert client.get('/api/workspaces/'+str(uuid.uuid4())+'/workspace-settings').status_code==404

@pytest.mark.parametrize('scope',['own','teams','all'])
def test_team_ticket_grants_do_not_expand_to_workspace(client,app,ws,scope):
 root,path,agent,headers=ws;workspace_id=uuid.UUID(root.split('/')[3]);user_id,role_id=custom_actor(app,root.split('/')[3],['view_tickets_'+scope],'team')
 with app.state.session_factory() as db:
  team=db.scalar(select(SupportTeam).where(SupportTeam.workspace_id==workspace_id));space=grants.default_space(db,workspace_id)
  assert not grants.allowed(db,workspace_id,'manage_billing',user_id)
  assert not grants.record_allowed(db,workspace_id,user_id,'tickets','view',None,uuid.uuid4(),space)
  assert grants.record_allowed(db,workspace_id,user_id,'tickets','view',user_id,None,space)==(scope=='own')
  assert grants.record_allowed(db,workspace_id,user_id,'tickets','view',None,team.id,space)==(scope in {'all','teams'})
  assert not grants.record_allowed(db,workspace_id,user_id,'tickets','manage',user_id,team.id,space)
