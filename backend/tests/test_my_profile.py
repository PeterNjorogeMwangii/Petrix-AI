"""Account privacy, persona snapshots, delivery and presence behavior."""
import io,uuid
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select,func
from app.models import User,UserAlias,UserProfile,ProfileAsset,AgentMessage,AgentConversation,TicketEmail,SupportAgent,SupportTicket
from app.core.security import utcnow
from test_workflows import workflow,event
from test_live_agent_handoff import workers,support,available,configure,handoff,queued
from test_create_ticket import start_ticket,mail_settings

@pytest.fixture
def account(client,signed_in,csrf):
 result=client.get('/api/me/profile');assert result.status_code==200,result.text
 return result.json(),csrf

def image(color='orange',kind='PNG'):
 out=io.BytesIO();Image.new('RGB',(300,150),color).save(out,format=kind);return out.getvalue()

def alias(client,headers,name='Support East',**extra):
 data={'name':name,'is_default':'false','signature_name':'Support Team','signature_content':'Customer success\nEast Africa',**extra}
 result=client.post('/api/me/aliases',data=data,headers=headers);assert result.status_code==201,result.text;return result.json()

def pref(client,headers,**changes):
 p=client.get('/api/me/profile').json();result=client.post('/api/me/profile/preferences',json={**changes,'expected_revision':p['revision']},headers=headers);assert result.status_code==200,result.text;return result.json()

def live(client,workflow,workers):
 path,document,headers=workflow;worker,staff,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff);configure(client,path,document,headers,[handoff()]);result=queued(client,path,headers);chat=worker.post(base+'/inbox/chats/'+result['conversation_id']+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff);assert chat.status_code==200,chat.text;return path,base,worker,staff,user_id,chat.json()

def reply(worker,base,headers,chat,content='Hello customer',request_id=None,**extra):return worker.post(base+'/inbox/chats/'+chat['id']+'/reply',json={'expected_revision':chat['revision'],'client_message_id':request_id or str(uuid.uuid4()),'content':content,**extra},headers=headers)

def test_account_name_email_avatar_language_and_revision(client,app,account):
 p,headers=account;data={'first_name':'Chris','last_name':'Kahiga','language':'sw','expected_revision':p['revision']}
 saved=client.post('/api/me/profile/account',data=data,files={'avatar':('photo.gif',image(kind='GIF'),'image/gif')},headers=headers);assert saved.status_code==200,saved.text;saved=saved.json();assert saved['full_name']=='Chris Kahiga' and saved['language']=='sw';assert saved['email']=='chris@gmail.com'
 context=client.get('/api/auth/me').json();assert context['user']['full_name']=='Chris Kahiga' and context['user']['avatar_url']==saved['avatar_url'];assert client.get(saved['avatar_url']).content.startswith(b'\x89PNG')
 assert client.post('/api/me/profile/account',data=data,headers=headers).status_code==409
 data['expected_revision']=saved['revision'];data['remove_avatar']='true';result=client.post('/api/me/profile/account',data=data,headers=headers);assert result.status_code==200 and result.json()['avatar_url'] is None

@pytest.mark.parametrize('values',[{'first_name':'x'*101},{'language':'bad'},{'first_name':'x'*100,'last_name':'y'*100},{'last_name':'bad\x00name'}])
def test_account_invalid_fields_leave_profile_unchanged(client,account,values):
 p,h=account;result=client.post('/api/me/profile/account',data={'first_name':'Chris','last_name':'Kahiga','language':'en','expected_revision':p['revision'],**values},headers=h);assert result.status_code==422,result.text;assert client.get('/api/me/profile').json()['revision']==p['revision']

@pytest.mark.parametrize('filename,data',[('bad.png',b'not image'),('large.png',b'x'*(10*1024*1024+1)),('bad.svg',b'<svg></svg>')])
def test_account_invalid_images_are_atomic(client,account,filename,data):
 p,h=account;result=client.post('/api/me/profile/account',data={'first_name':'Changed','expected_revision':p['revision']},files={'avatar':(filename,data,'image/png')},headers=h);assert result.status_code==422;assert client.get('/api/me/profile').json()['first_name']==p['first_name']

@pytest.mark.parametrize('presence',['online','away','offline'])
def test_presence_persists_and_requires_csrf(client,account,presence):
 p,h=account;assert client.post('/api/me/profile/preferences',json={'presence':presence,'expected_revision':p['revision']}).status_code==403
 result=pref(client,h,presence=presence);assert result['presence']==presence and client.get('/api/auth/me').json()['user']['presence']==presence

@pytest.mark.parametrize('theme',['system','light','dark'])
def test_theme_and_multichat_are_personal_persistent_preferences(client,account,theme):
 _,h=account;result=pref(client,h,theme=theme,multichat=True);assert result['theme']==theme and result['multichat'];assert client.get('/api/auth/me').json()['user']['theme']==theme


def test_notifications_saved_independently_and_validated(client,account):
 p,h=account;notifications={**p['notifications'],'browser':True,'platform':False,'assigned_team':'bell','message_open':'chime'}
 result=client.post('/api/me/profile/notifications',json={'expected_revision':p['revision'],'notifications':notifications},headers=h);assert result.status_code==200,result.text
 assert client.get('/api/me/profile').json()['notifications']==notifications
 assert client.post('/api/me/profile/notifications',json={'expected_revision':result.json()['revision'],'notifications':{**notifications,'message_open':'javascript:alert(1)'}},headers=h).status_code==422
 assert client.post('/api/me/profile/preferences',json={'expected_revision':result.json()['revision'],'theme':'invalid'},headers=h).status_code==422


def test_alias_default_uniqueness_edit_delete_and_personal_scope(client,app,account):
 _,h=account;a=alias(client,h,is_default='true');b=alias(client,h,'Support West',is_default='true');rows=client.get('/api/me/aliases').json()['aliases'];assert [r['id'] for r in rows if r['is_default']]==[b['id']]
 assert client.post('/api/me/aliases',data={'name':'SUPPORT WEST'},headers=h).status_code==409
 updated=client.post('/api/me/aliases/'+b['id'],data={'name':'Support Central','expected_revision':b['revision'],'signature_name':'Chris','signature_content':'Support desk'},headers=h);assert updated.status_code==200,updated.text
 assert client.post('/api/me/aliases/'+b['id'],data={'name':'Stale','expected_revision':b['revision']},headers=h).status_code==409
 assert client.post('/api/me/aliases/'+b['id']+'/delete',json={'expected_revision':updated.json()['revision']},headers=h).status_code==200
 assert len(client.get('/api/me/aliases').json()['aliases'])==1
 other=TestClient(app,base_url='http://localhost:4200');other.post('/api/auth/magic-link',json={'email':'other@example.com'});other.post('/api/auth/verify',json={'token':app.state.mailer.token});oh={'X-Petrix-CSRF':other.cookies.get('petrix_csrf')};assert other.get('/api/me/aliases').json()['aliases']==[];assert other.post('/api/me/aliases/'+a['id']+'/delete',json={'expected_revision':a['revision']},headers=oh).status_code==404;other.close()


def test_alias_no_default_uses_account_and_remembers_per_conversation(client,app,workflow,workers):
 _,base,worker,h,user,chat=live(client,workflow,workers);a=alias(worker,h);persona=base+'/support/chats/'+chat['id']+'/persona';before=worker.get(persona).json();assert before['current']['alias_id'] is None
 result=worker.post(persona,json={'alias_id':a['id'],'expected_revision':before['revision']},headers=h);assert result.status_code==200,result.text
 sent=reply(worker,base,h,chat);assert sent.status_code==200,sent.text;assert sent.json()['messages'][-1]['author_name']==a['name']
 alias(worker,h,'Different default',is_default='true');assert worker.get(persona).json()['current']['alias_id']==a['id']
 assert worker.post(persona,json={'alias_id':None,'expected_revision':0},headers=h).status_code==409
 p=worker.get(persona).json();changed=worker.post(persona,json={'alias_id':None,'expected_revision':p['revision']},headers=h);assert changed.status_code==200 and changed.json()['current']['alias_id'] is None and changed.json()['revision']>p['revision']
 sent=reply(worker,base,h,chat,'Account identity');assert sent.status_code==200 and sent.json()['messages'][-1]['alias_id'] is None


def test_default_alias_frozen_name_avatar_and_deleted_alias_not_reusable(client,app,workflow,workers):
 _,base,worker,h,user,chat=live(client,workflow,workers)
 result=worker.post('/api/me/aliases',data={'name':'Persona A','is_default':'true'},files={'avatar':('avatar.png',image(),'image/png')},headers=h);assert result.status_code==201,result.text;a=result.json();idempotency=str(uuid.uuid4());sent=reply(worker,base,h,chat,request_id=idempotency);assert sent.status_code==200,sent.text;message=sent.json()['messages'][-1];assert message['author_name']=='Persona A' and message['avatar_url']==a['avatar_url']
 assert client.get(a['avatar_url']).status_code==200  # customer/tester can see the saved reply avatar
 edited=worker.post('/api/me/aliases/'+a['id'],data={'name':'Persona B','expected_revision':a['revision'],'is_default':'true'},files={'avatar':('next.png',image('blue'),'image/png')},headers=h);assert edited.status_code==200,edited.text;edited=edited.json();assert edited['avatar_url']!=a['avatar_url']
 assert worker.post('/api/me/aliases/'+a['id']+'/delete',json={'expected_revision':edited['revision']},headers=h).status_code==200
 history=worker.get(base+'/inbox/chats/'+chat['id']).json()['messages'];assert history[-1]['author_name']=='Persona A' and worker.get(message['avatar_url']).status_code==200
 assert reply(worker,base,h,chat,'New',alias_id=a['id']).status_code==404
 assert reply(worker,base,h,chat,request_id=idempotency).status_code==200
 with app.state.session_factory() as db:
  row=db.get(AgentMessage,uuid.UUID(message['id']));assert row.author_id==uuid.UUID(user);assert row.message_data['author_name']=='Persona A'


def test_alias_cannot_use_another_users_alias(client,app,workflow,workers):
 _,base,worker,h,user,chat=live(client,workflow,workers);a=alias(client,workflow[2]);result=reply(worker,base,h,chat,alias_id=a['id']);assert result.status_code==404


def test_away_offline_remove_eligibility_heartbeat_cannot_override(client,app,workflow,workers):
 _,base,worker,h,user,chat=live(client,workflow,workers)
 for state in ['away','offline']:
  pref(worker,h,presence=state);assert not worker.post(base+'/inbox/heartbeat',headers=h).json()['available'];assert not worker.get(base+'/inbox/profile').json()['available']
 result=pref(worker,h,presence='online');assert result['presence']=='online';assert worker.get(base+'/inbox/profile').json()['available']


def test_personal_avatar_is_private_before_used_in_visible_reply(client,app,account):
 p,h=account;result=client.post('/api/me/profile/account',data={'expected_revision':p['revision']},files={'avatar':('avatar.png',image(),'image/png')},headers=h);url=result.json()['avatar_url'];other=TestClient(app,base_url='http://localhost:4200');other.post('/api/auth/magic-link',json={'email':'other@example.com'});other.post('/api/auth/verify',json={'token':app.state.mailer.token});assert other.get(url).status_code==404;other.close()


def test_ticket_reply_has_alias_signature_escaped_html_and_idempotent_delivery(client,app,workflow):
 domain,_=mail_settings(client,workflow,app);path,h,started=start_ticket(client,workflow);base=path.rsplit('/agents/',1)[0];identifier=started.json()['last_ticket']['id'];a=alias(client,h,is_default='true',signature_name='<Support East>',signature_content='Thank you\nSupport & success');before=client.get(base+'/tickets/'+identifier).json()['ticket'];body={'expected_revision':before['revision'],'client_request_id':str(uuid.uuid4()),'content':'Hello <customer>','domain_id':domain['id']}
 result=client.post(base+'/tickets/'+identifier+'/reply',json=body,headers=h);assert result.status_code==200,result.text;email=result.json()['emails'][-1];assert email['kind']=='reply' and email['status']=='sent' and email['persona']['name']==a['name'];assert '<Support East>' in email['body_text']
 assert client.post(base+'/tickets/'+identifier+'/reply',json=body,headers=h).status_code==200;assert len(app.state.ticket_mailer.messages)==1
 assert client.post(base+'/tickets/'+identifier+'/reply',json={**body,'content':'Different'},headers=h).status_code==409
 with app.state.session_factory() as db:
  row=db.get(TicketEmail,uuid.UUID(email['id']));assert '&lt;customer&gt;' in row.body_html and '&lt;Support East&gt;' in row.body_html and row.actor_id is not None
 assert client.post('/api/me/aliases/'+a['id']+'/delete',json={'expected_revision':a['revision']},headers=h).status_code==200;assert client.get(base+'/tickets/'+identifier).json()['emails'][-1]['persona']['name']==a['name']


def test_profile_updates_scoped_and_cursor_validated(client,app,workflow,workers):
 _,base,worker,h,user,chat=live(client,workflow,workers);result=worker.get(base+'/profile-updates');assert result.status_code==200,result.text;assert result.json()['chats'][0]['id']==chat['id'];assert worker.get(base+'/profile-updates?since=2026-01-01').status_code==422
 assert client.get('/api/workspaces/'+str(uuid.uuid4())+'/profile-updates').status_code==404


def test_alias_and_account_do_not_require_workspace_management(client,app,account,workflow):
 from test_workspace_settings import custom_actor
 p,h=account;ws=client.get('/api/auth/me').json()['workspace']['id'];custom_actor(app,ws,[])
 assert client.get('/api/me/profile').status_code==200
 saved=client.post('/api/me/profile/account',data={'first_name':'Private','last_name':'Account','expected_revision':p['revision']},headers=h);assert saved.status_code==200,saved.text
 assert alias(client,h,'Independent persona')['name']=='Independent persona'
 assert client.get('/api/workspaces/'+ws+'/workspace-settings/roles').status_code==403


def test_ticket_allows_multiple_replies_but_no_duplicate_initial_mail(client,app,workflow):
 from app.workspace_settings.usage import aggregate
 domain,_=mail_settings(client,workflow,app);path,h,started=start_ticket(client,workflow);base=path.rsplit('/agents/',1)[0];identifier=started.json()['last_ticket']['id']
 for i in range(2):
  before=client.get(base+'/tickets/'+identifier).json()['ticket'];result=client.post(base+'/tickets/'+identifier+'/reply',json={'expected_revision':before['revision'],'client_request_id':str(uuid.uuid4()),'content':'Reply '+str(i),'domain_id':domain['id']},headers=h);assert result.status_code==200,result.text
 assert len(app.state.ticket_mailer.messages)==2
 with app.state.session_factory() as db:
  emails=db.scalars(select(TicketEmail).where(TicketEmail.ticket_id==uuid.UUID(identifier))).all();assert len(emails)==2 and all(e.kind=='reply' for e in emails)
  report=aggregate(db,uuid.UUID(base.split('/')[3]),app.state.settings,utcnow()-timedelta(days=1),utcnow()+timedelta(minutes=1))
  assert next(r for r in report['breakdown'] if r['key']=='manual_emails')['value']==2


def test_failed_ticket_reply_keeps_original_persona_on_retry(client,app,workflow):
 from app.tickets.notifications import DeliveryError
 from test_create_ticket import CaptureTickets
 domain,_=mail_settings(client,workflow,app);path,h,started=start_ticket(client,workflow);base=path.rsplit('/agents/',1)[0];identifier=started.json()['last_ticket']['id'];a=alias(client,h,is_default='true');app.state.ticket_mailer=CaptureTickets(DeliveryError(False))
 before=client.get(base+'/tickets/'+identifier).json()['ticket'];body={'expected_revision':before['revision'],'client_request_id':str(uuid.uuid4()),'content':'Pending delivery','domain_id':domain['id']};response=client.post(base+'/tickets/'+identifier+'/reply',json=body,headers=h);assert response.status_code==200,response.text;email=response.json()['emails'][-1];assert email['status']=='failed'
 assert client.post('/api/me/aliases/'+a['id']+'/delete',json={'expected_revision':a['revision']},headers=h).status_code==200
 app.state.ticket_mailer=CaptureTickets();retried=client.post(base+'/tickets/'+identifier+'/emails/'+email['id']+'/retry',json={'expected_revision':email['revision'],'confirm_not_sent':False},headers=h);assert retried.status_code==200,retried.text
 final=retried.json()['emails'][-1];assert final['status']=='sent' and final['persona']['name']==a['name'];assert len(app.state.ticket_mailer.messages)==1


def test_ticket_reply_requires_sender_access_open_status_and_csrf(client,app,workflow):
 domain,_=mail_settings(client,workflow,app);path,h,started=start_ticket(client,workflow);base=path.rsplit('/agents/',1)[0];identifier=started.json()['last_ticket']['id'];before=client.get(base+'/tickets/'+identifier).json()['ticket'];body={'expected_revision':before['revision'],'client_request_id':str(uuid.uuid4()),'content':'Hello','domain_id':domain['id']}
 assert client.post(base+'/tickets/'+identifier+'/reply',json=body).status_code==403
 assert client.post(base+'/tickets/'+identifier+'/reply',json={**body,'domain_id':str(uuid.uuid4())},headers=h).status_code==422
 with app.state.session_factory() as db:db.get(SupportTicket,uuid.UUID(identifier)).status='closed';db.commit()
 assert client.post(base+'/tickets/'+identifier+'/reply',json=body,headers=h).status_code==409
