"""Signed Meta fixtures exercise the real native workflow, Inbox and durable transport."""
import hashlib,hmac,io,json,logging,uuid
from copy import deepcopy
from datetime import timedelta
import httpx,pytest
from PIL import Image
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import (Workspace,AgentConversation,AgentMessage,AgentAttachment,SupportPerson,WhatsAppChannel as Channel,
    WhatsAppContact as Contact,WhatsAppConversation as Link,WhatsAppInbound as Inbound,WhatsAppOutbox as Outbox)
from app.whatsapp.client import GraphClient,GraphError,recipient
from app.whatsapp.worker import intake_one,schedule_timeout
from app.whatsapp.delivery import send_one,enqueue
from test_agents import agent_workspace
from test_workflows import message,intent,node

APP_ID='123456789';PHONE_ID='987654321';SECRET='test-Meta-secret';TOKEN='test-System-user-token';PHONE='254700000001'

class Meta:
 def __init__(self,settings):
  self.sent=[];self.uploads=[];self.failure=None;self.invalid=False;self.data=b'PRIVATE WHATSAPP FILE';self.content_type='text/plain'
  self.client=GraphClient(settings,httpx.MockTransport(self.handle))
 def handle(self,r):
  path=r.url.path
  if path.endswith('/debug_token'):
   assert r.headers['authorization']=='Bearer '+APP_ID+'|'+SECRET
   assert r.url.params['input_token']==TOKEN
   return httpx.Response(200,json={'data':{'is_valid':not self.invalid,'app_id':APP_ID,'scopes':['whatsapp_business_messaging','whatsapp_business_management','business_management'],'expires_at':0}})
  if r.url.params.get('fields')=='id,display_phone_number,verified_name':return httpx.Response(200,json={'id':path.rsplit('/',1)[-1],'display_phone_number':'+254700000000','verified_name':'Petrix Support'})
  if path.endswith('/messages'):
   data=json.loads(r.content);self.sent.append(data)
   if self.failure=='timeout':raise httpx.ReadTimeout('SECRET must not escape',request=r)
   if self.failure=='rejected':return httpx.Response(400,json={'error':{'code':190,'message':'SECRET must not escape'}})
   return httpx.Response(200,json={'messages':[{'id':'wamid.out.'+str(len(self.sent))}]})
  if path.endswith('/media'):
   self.uploads.append(r.content);return httpx.Response(200,json={'id':'111222333'})
  if path.endswith('/111222333'):
   return httpx.Response(200,json={'url':'https://lookaside.fbsbx.com/media/file','mime_type':self.content_type,'file_size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()})
  if r.url.host=='lookaside.fbsbx.com':return httpx.Response(200,content=self.data)
  raise AssertionError('Unexpected Meta request '+str(r.url.copy_with(query=None)))

@pytest.fixture
def whatsapp(client,agent_workspace,app):
 base,agent,headers=agent_workspace;path=base+'/'+agent['id'];root=path+'/whatsapp'
 doc=client.get(path+'/workflows').json()
 fallback=next(v for v in doc['nodes'] if v['type']=='fallback');fallback['enabled']=True;fallback['children']=[message('Fixture bot reply')]
 assert client.post(path+'/workflows',json={'expected_revision':doc['revision'],'nodes':doc['nodes']},headers=headers).status_code==200
 with app.state.session_factory() as db:
  workspace=db.get(Workspace,uuid.UUID(base.split('/')[3]));workspace.plan_name='Lite';db.commit()
 meta=Meta(app.state.settings);app.state.whatsapp_client=meta.client
 r=client.post(root,json={'client_id':str(uuid.uuid4()),'name':'WhatsApp Support'},headers=headers);assert r.status_code==201,r.text
 f={'root':root,'path':path,'base':base,'h':headers,'channel':r.json(),'app':app,'meta':meta,'client':client}
 configure(f);verify(f);handshake(f);save(f,enabled=True)
 return f

def refresh(f):f['channel']=f['client'].get(f['root']+'/'+f['channel']['id'],headers=f['h']).json();return f['channel']
def post(f,suffix,values):return f['client'].post(f['root']+'/'+f['channel']['id']+suffix,json=values if suffix.startswith('/activity/') else {'expected_revision':f['channel']['revision'],**values},headers=f['h'])
def configure(f,**values):
 r=post(f,'/configuration',{'app_id':APP_ID,'phone_number_id':PHONE_ID,'app_secret':SECRET,'access_token':TOKEN,**values});assert r.status_code==200,r.text;f['channel']=r.json();return r

def verify(f):
 r=post(f,'/verify',{});assert r.status_code==200,r.text;f['channel']=r.json();return r

def handshake(f):
 secret=post(f,'/setup-secret',{});assert secret.status_code==200,secret.text
 r=f['client'].get('/api/whatsapp/webhooks/'+APP_ID,params={'hub.mode':'subscribe','hub.verify_token':secret.json()['verify_token'],'hub.challenge':'0123456789'});assert r.status_code==200 and r.text=='0123456789';refresh(f);return secret.json()['verify_token']

def save(f,enabled=None,config=None):
 c=f['channel'];r=post(f,'',{'name':c['name'],'enabled':c['enabled'] if enabled is None else enabled,'config':config or c['config']});assert r.status_code==200,r.text;f['channel']=r.json();return r

def webhook(f,messages=None,statuses=None,phone=PHONE_ID,signature=None):
 value={'metadata':{'phone_number_id':phone},'contacts':[{'wa_id':PHONE,'profile':{'name':'Ada'}}],'messages':messages or [],'statuses':statuses or []}
 data={'object':'whatsapp_business_account','entry':[{'changes':[{'field':'messages','value':value}]}]};raw=json.dumps(data,separators=(',',':')).encode()
 sig=signature or 'sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
 return f['client'].post('/api/whatsapp/webhooks/'+APP_ID,content=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':sig,'Origin':'https://meta.example','Sec-Fetch-Site':'cross-site'})

def incoming(text='Hello',identifier=None,phone=PHONE,**more):return {'id':identifier or 'wamid.in.'+uuid.uuid4().hex,'from':phone,'timestamp':str(int(utcnow().timestamp())),'type':'text','text':{'body':text},**more}
def receive(f,text='Hello',**more):
 r=webhook(f,[incoming(text,**more)]);assert r.status_code==200 and r.json()['received']==1,r.text;assert intake_one(f['app']);return r

def chat(f):
 with f['app'].state.session_factory() as db:
  person=db.scalar(select(Contact).order_by(Contact.created_at.desc()));return db.get(AgentConversation,person.current_conversation_id)
def transcript(f):
 c=chat(f)
 with f['app'].state.session_factory() as db:return list(db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==c.id).order_by(AgentMessage.sequence)))
def drain(f):
 count=0
 while send_one(f['app']):
  count+=1
  assert count<100
 return count

def test_disabled_creation_idempotency_and_secrets_write_only(client,whatsapp):
 f=whatsapp;key=str(uuid.uuid4());a=client.post(f['root'],json={'client_id':key},headers=f['h']);b=client.post(f['root'],json={'client_id':key},headers=f['h']);assert a.status_code==201 and a.json()['id']==b.json()['id'] and not a.json()['enabled']
 assert SECRET not in client.get(f['root'],headers=f['h']).text and TOKEN not in client.get(f['root'],headers=f['h']).text
 with f['app'].state.session_factory() as db:
  row=db.get(Channel,uuid.UUID(f['channel']['id']));assert SECRET not in row.credentials and TOKEN not in row.credentials

def test_lite_gate_and_connection_verification_required(client,whatsapp):
 f=whatsapp;save(f,enabled=False)
 with f['app'].state.session_factory() as db:db.get(Workspace,uuid.UUID(f['base'].split('/')[3])).plan_name='Starter';db.commit()
 r=post(f,'',{'name':'WA','config':f['channel']['config'],'enabled':True});assert r.status_code==403 and 'Lite' in r.text
 assert post(f,'/verify',{}).status_code==200

def test_management_csrf_origin_and_validation_never_echo_secret(client,whatsapp):
 f=whatsapp;url=f['root']+'/'+f['channel']['id']+'/configuration';body={'app_id':'bad','phone_number_id':PHONE_ID,'expected_revision':f['channel']['revision'],'app_secret':SECRET,'access_token':TOKEN}
 assert client.post(url,json={**body,'app_id':APP_ID}).status_code==403
 response=client.post(url,json=body,headers=f['h']);assert response.status_code==422 and SECRET not in response.text and TOKEN not in response.text
 assert client.post(url,json=body,headers={**f['h'],'Origin':'https://evil.example'}).status_code==403

def test_blank_secrets_preserved_and_rotation_disables(client,whatsapp):
 f=whatsapp;old=f['channel']['revision'];configure(f,app_secret='',access_token='');assert f['channel']['enabled'] and f['channel']['verified']
 configure(f,access_token='new-private-token');assert not f['channel']['enabled'] and not f['channel']['verified']
 assert post(f,'',{'name':'stale','config':f['channel']['config'],'enabled':False,'expected_revision':old}).status_code==409

def test_invalid_meta_token_is_safe_and_disables(client,whatsapp):
 f=whatsapp;f['meta'].invalid=True;r=verify(f);assert not r.json()['verified'] and not r.json()['enabled'] and 'does not belong' in r.json()['last_error']

def test_reissue_requires_confirmation_and_old_verify_token_fails(client,whatsapp):
 f=whatsapp;old=handshake(f)
 assert post(f,'/reissue',{'confirmed':False}).status_code==422
 r=post(f,'/reissue',{'confirmed':True});assert r.status_code==200;f['channel']=r.json();assert not f['channel']['enabled'] and not f['channel']['webhook_verified_at']
 assert r.json()['verify_token']!=old
 assert client.get('/api/whatsapp/webhooks/'+APP_ID,params={'hub.mode':'subscribe','hub.verify_token':old,'hub.challenge':'1'}).status_code==403
 assert post(f,'',{'name':'WA','config':f['channel']['config'],'enabled':True}).status_code==409

@pytest.mark.parametrize('sig',['','sha256='+'0'*64,'sha1=abc'])
def test_bad_webhook_signature_does_not_create_work(whatsapp,sig):
 f=whatsapp;r=webhook(f,[incoming()],signature=sig or 'missing');assert r.status_code==403
 with f['app'].state.session_factory() as db:assert db.scalar(select(func.count()).select_from(Inbound))==0

def test_webhook_wrong_phone_disabled_and_malformed_are_ignored(whatsapp):
 f=whatsapp;assert webhook(f,[incoming()],phone='111111111').json()['received']==0
 save(f,enabled=False);assert webhook(f,[incoming()]).json()['received']==0
 save(f,enabled=True)
 raw=json.dumps({'object':'whatsapp_business_account','entry':[{'changes':[{'field':'messages','value':{'messages':{},'statuses':'bad','metadata':[]}}]}]}).encode();sig='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
 assert f['client'].post('/api/whatsapp/webhooks/'+APP_ID,content=raw,headers={'X-Hub-Signature-256':sig}).status_code==200

def test_webhook_idempotency_native_people_and_message_order(whatsapp):
 f=whatsapp;event=incoming('A question');a=webhook(f,[event]);b=webhook(f,[event]);assert a.json()['received']==1 and b.json()['received']==0
 assert intake_one(f['app']) and not intake_one(f['app']);messages=transcript(f);user=next(v for v in messages if v.role=='user');reply=next(v for v in messages if v.content=='Fixture bot reply');assert user.sequence<reply.sequence
 c=chat(f);assert c.workflow_state['channel']=='whatsapp'
 with f['app'].state.session_factory() as db:
  p=db.get(SupportPerson,c.person_id);assert p.name=='Ada' and p.phone=='+'+PHONE and not p.user_id
  assert db.scalar(select(func.count()).select_from(Contact))==1

def test_ordered_native_delivery_status_and_reordered_callbacks(whatsapp):
 f=whatsapp;receive(f);assert drain(f)>=2;assert all(v['to']==PHONE for v in f['meta'].sent)
 with f['app'].state.session_factory() as db:row=db.scalar(select(Outbox).order_by(Outbox.message_sequence));id=row.id;provider=row.provider_id
 for value in ['read','sent','delivered']:
  r=webhook(f,statuses=[{'id':provider,'status':value,'recipient_id':PHONE,'timestamp':'123'}]);assert r.status_code==200;assert intake_one(f['app'],True)
 with f['app'].state.session_factory() as db:assert db.get(Outbox,id).status=='read'

def test_unknown_recipient_status_does_not_change_delivery(whatsapp):
 f=whatsapp;receive(f);drain(f)
 with f['app'].state.session_factory() as db:r=db.scalar(select(Outbox));id=r.id;provider=r.provider_id
 webhook(f,statuses=[{'id':provider,'status':'read','recipient_id':'254799999999'}]);intake_one(f['app'],True)
 with f['app'].state.session_factory() as db:assert db.get(Outbox,id).status=='sent'

def test_expired_service_window_blocks_and_review_retry_uses_current_window(whatsapp):
 f=whatsapp;receive(f)
 with f['app'].state.session_factory() as db:
  p=db.scalar(select(Contact));p.last_customer_at=utcnow()-timedelta(hours=25);db.commit()
 assert send_one(f['app']) and not f['meta'].sent
 with f['app'].state.session_factory() as db:r=db.scalar(select(Outbox).where(Outbox.status=='blocked'));id=r.id
 assert post(f,'/activity/outbound/'+str(id),{'action':'retry','confirmed':True}).status_code==409
 receive(f,'Open window again')
 assert post(f,'/activity/outbound/'+str(id),{'action':'retry','confirmed':True}).status_code==200
 assert drain(f)>=2

def test_uncertain_send_is_not_replayed_and_status_can_reconcile(whatsapp):
 f=whatsapp;receive(f);f['meta'].failure='timeout';assert send_one(f['app']);assert not send_one(f['app']) and len(f['meta'].sent)==1
 with f['app'].state.session_factory() as db:r=db.scalar(select(Outbox).where(Outbox.status=='uncertain'));id=r.id
 assert post(f,'/activity/outbound/'+str(id),{'action':'retry','confirmed':True}).status_code==409
 webhook(f,statuses=[{'id':'wamid.accepted','status':'delivered','recipient_id':PHONE,'biz_opaque_callback_data':str(id)}]);assert intake_one(f['app'],True)
 with f['app'].state.session_factory() as db:assert db.get(Outbox,id).status=='delivered'

def test_known_failure_retry_and_cancel_update_inbox(whatsapp):
 f=whatsapp;receive(f);f['meta'].failure='rejected';assert send_one(f['app'])
 with f['app'].state.session_factory() as db:r=db.scalar(select(Outbox).where(Outbox.status=='failed'));id=r.id;mid=r.message_id;assert 'test-' not in r.error
 f['meta'].failure=None;assert post(f,'/activity/outbound/'+str(id),{'action':'retry','confirmed':True}).status_code==200;assert send_one(f['app'])
 with f['app'].state.session_factory() as db:assert db.get(AgentMessage,mid).message_data['delivery_status']=='sent'

def test_stale_processing_is_not_replayed_and_blocks_later_contact(whatsapp):
 f=whatsapp;webhook(f,[incoming('one'),incoming('two')])
 with f['app'].state.session_factory() as db:
  row=db.scalar(select(Inbound).order_by(Inbound.position));id=row.id;row.status='running';row.lease_expires_at=utcnow()-timedelta(minutes=1);db.commit()
 assert intake_one(f['app']) and not intake_one(f['app'])
 assert post(f,'/activity/inbound/'+str(id),{'action':'dismiss','confirmed':True}).status_code==200
 assert intake_one(f['app']) and any(v.content=='two' for v in transcript(f))

def test_native_incoming_file_retained_and_outgoing_file_uploaded(whatsapp):
 f=whatsapp;receive(f,'',type='document',document={'id':'111222333','filename':'note.txt'})
 messages=transcript(f);assert any(v.message_data.get('attachments') for v in messages)
 c=chat(f)
 with f['app'].state.session_factory() as db:
  file=db.scalar(select(AgentAttachment));assert file.data==f['meta'].data
  out=AgentMessage(conversation_id=c.id,role='assistant',content='Your file',message_data={'attachments':[{'id':str(file.id),'name':'note.txt'}]});db.add(out);db.flush();enqueue(db,db.get(AgentConversation,c.id),f['app'].state.settings)
 drain(f);assert f['meta'].uploads and any(v.get('type')=='document' and v['document']['id']=='111222333' for v in f['meta'].sent)

def test_bsuid_only_customer_uses_recipient_field_and_isolation(whatsapp):
 f=whatsapp;receive(f,'Hello BSUID',phone='',from_user_id='KE.Customer123');drain(f);assert f['meta'].sent and all(v.get('recipient')=='KE.Customer123' and 'to' not in v for v in f['meta'].sent)
 receive(f,'Different person',phone='',from_user_id='KE.OtherCustomer')
 with f['app'].state.session_factory() as db:assert db.scalar(select(func.count()).select_from(Contact))==2

def test_channel_delete_retains_native_conversations_and_people(whatsapp):
 f=whatsapp;receive(f);c=chat(f)
 assert post(f,'/delete',{'confirmed':True}).status_code==200
 with f['app'].state.session_factory() as db:
  assert db.get(AgentConversation,c.id) and db.get(SupportPerson,c.person_id)
  assert db.get(Link,c.id).channel_id is None
  assert db.scalar(select(func.count()).select_from(Outbox))==0
  assert all(m.message_data.get('delivery_status')!='queued' for m in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==c.id)))

def test_auto_timeout_is_durable_and_runs_without_browser(whatsapp):
 f=whatsapp;c=deepcopy(f['channel']['config']);c.update(auto_close=True,timeout_minutes=1);save(f,config=c);receive(f);conversation=chat(f)
 with f['app'].state.session_factory() as db:
  db.scalar(select(Contact)).last_customer_at=utcnow()-timedelta(minutes=2)
  for m in db.scalars(select(AgentMessage)):m.created_at=utcnow()-timedelta(minutes=2)
  db.commit()
 assert schedule_timeout(f['app']) and not schedule_timeout(f['app']);assert intake_one(f['app']);assert chat(f).workflow_state['closed']
 receive(f,'New conversation');assert chat(f).id!=conversation.id

@pytest.mark.parametrize('bad',['https://evil.example/file','https://lookaside.fbsbx.com:bad/file','http://lookaside.fbsbx.com/file','https://lookaside.fbsbx.com.evil.example/file','https://user:pass@lookaside.fbsbx.com/file'])
def test_media_download_rejects_unapproved_location(app,bad):
 graph=GraphClient(app.state.settings,httpx.MockTransport(lambda r:httpx.Response(200,json={'url':bad})))
 with pytest.raises(GraphError):graph.download('111222333',TOKEN)

def test_meta_http_logs_redact_token(app,caplog,monkeypatch):
 meta=Meta(app.state.settings);monkeypatch.setattr(logging.getLogger('httpx'),'disabled',False);monkeypatch.setattr(logging.getLogger('httpx'),'propagate',True)
 with caplog.at_level(logging.INFO,logger='httpx'):meta.client.verify(APP_ID,SECRET,TOKEN,PHONE_ID)
 assert TOKEN not in caplog.text and SECRET not in caplog.text and 'REDACTED' in caplog.text

@pytest.mark.parametrize('bad',['<script>','+254700000001','US.','US.bad/value','x'*100])
def test_recipient_rejects_invalid_ids(bad):
 with pytest.raises(GraphError):recipient(bad)

def publish_nodes(f,change):
 doc=f['client'].get(f['path']+'/workflows').json();change(doc['nodes'])
 r=f['client'].post(f['path']+'/workflows',json={'expected_revision':doc['revision'],'nodes':doc['nodes']},headers=f['h']);assert r.status_code==200,r.text;save(f)

def test_saved_workflows_frozen_per_conversation(whatsapp):
 f=whatsapp;receive(f,'First');old=chat(f)
 publish_nodes(f,lambda nodes:next(v for v in nodes if v['type']=='fallback').update(children=[message('New published reply')]))
 receive(f,'Keep version');assert transcript(f)[-1].content=='Fixture bot reply'
 receive(f,'New person',phone='254700000002');assert transcript(f)[-1].content=='New published reply' and chat(f).id!=old.id

def test_native_quick_reply_survey_and_stale_button_guard(whatsapp):
 from test_ask_survey import field,survey
 f=whatsapp
 publish_nodes(f,lambda nodes:nodes[0].update(children=[survey(field('service',quick_answers=['Banking','Insurance']),field('amount','number')),message('Survey saved')]))
 receive(f,'Insurance')
 with f['app'].state.session_factory() as db:
  first=next(v for v in db.scalars(select(Outbox).order_by(Outbox.message_sequence)) if v.buttons);old=next(k for k,v in first.buttons.items() if v['value']=='Banking')
 receive(f,'',type='interactive',interactive={'button_reply':{'id':old,'title':'Banking'}})
 assert 'already changed' in transcript(f)[-1].content
 receive(f,'100');c=chat(f);assert c.workflow_state['variables']['service']=='Insurance' and c.workflow_state['variables']['amount']==100
 assert any(v.content=='Survey saved' for v in transcript(f))

def test_closed_chat_feedback_rating_typed_fields_and_confirmation(whatsapp):
 from test_collect_feedback import form,field
 from test_chat_closed import close as unused
 f=whatsapp
 def update(nodes):
  nodes.append(intent('Close now',[node('action','close_chat')]))
  next(v for v in nodes if v['type']=='chat_closed')['children']=[form(fields=[field('choice',True,choices=['Helpful','Needs improvement'])])]
 publish_nodes(f,update);receive(f,'Close now');c=chat(f);assert c.workflow_state['closed'] and c.workflow_state.get('feedback_request')
 receive(f,'5');receive(f,'Helpful');c=chat(f);assert c.workflow_state.get('feedback_submitted') and c.workflow_state['csat_rating']==5
 assert any('Thank you' in v.content for v in transcript(f))
 receive(f,'New question');assert chat(f).id!=c.id

def test_real_inbox_handoff_reply_delivery_and_service_window(whatsapp):
 from test_live_agent_handoff import handoff,available
 f=whatsapp;client=f['client'];root=f['base'].split('/agents')[0];uid=client.get('/api/auth/me').json()['user']['id']
 r=client.post(root+'/teams',json={'name':'Support','description':'','alternative_names':[],'member_ids':[uid]},headers=f['h']);assert r.status_code==201,r.text
 available(client,root,f['h']);publish_nodes(f,lambda nodes:nodes[0].update(children=[message('Connecting to support.'),handoff(availability_check=False)]))
 receive(f,'Customer question');c=chat(f);assert c.live_status=='pending'
 detail=client.get(root+'/inbox/chats/'+str(c.id)).json();assert c.workflow_state['channel']=='whatsapp'
 unified=client.get(root+'/inbox/conversations/chats/'+str(c.id));assert unified.status_code==200,unified.text;assert unified.json()['channel']=='whatsapp' and unified.json()['channel_name']=='WhatsApp Support'
 claimed=client.post(root+'/inbox/chats/'+str(c.id)+'/claim',json={'expected_revision':detail['revision']},headers=f['h']);assert claimed.status_code==200,claimed.text
 uploaded=client.post(root+'/inbox/chats/'+str(c.id)+'/attachments',files={'files':('staff.txt',b'STAFF FILE','text/plain')},headers=f['h']);assert uploaded.status_code==201,uploaded.text
 body={'attachment_ids':[uploaded.json()['attachments'][0]['id']],'content':'Hello from a human','client_message_id':str(uuid.uuid4()),'expected_revision':claimed.json()['revision']}
 reply=client.post(root+'/inbox/chats/'+str(c.id)+'/reply',json=body,headers=f['h']);assert reply.status_code==200,reply.text
 duplicate=client.post(root+'/inbox/chats/'+str(c.id)+'/reply',json=body,headers=f['h']);assert duplicate.status_code==200
 drain(f);assert sum(v.get('text',{}).get('body')=='Hello from a human' for v in f['meta'].sent)==1 and f['meta'].uploads
 with f['app'].state.session_factory() as db:db.scalar(select(Contact)).last_customer_at=utcnow()-timedelta(hours=25);db.commit()
 r=client.post(root+'/inbox/chats/'+str(c.id)+'/reply',json={**body,'client_message_id':str(uuid.uuid4()),'expected_revision':reply.json()['revision'],'content':'Outside window'},headers=f['h']);assert r.status_code==409 and '24-hour' in r.text

def test_workflow_pdf_knowledge_and_exact_intent_run_on_whatsapp(client,agent_workspace,app):
 from test_agent_bundle import upload,GroundingProbe,DEFINITION
 base,agent,headers=agent_workspace;r=upload(client,base,headers);assert r.status_code==201,r.text
 path=base+'/'+r.json()['id'];root=path+'/whatsapp'
 with app.state.session_factory() as db:db.get(Workspace,uuid.UUID(base.split('/')[3])).plan_name='Lite';db.commit()
 meta=Meta(app.state.settings);app.state.whatsapp_client=meta.client;app.state.llm_processing_client=GroundingProbe()
 r=client.post(root,json={'client_id':str(uuid.uuid4()),'name':'PDF WhatsApp'},headers=headers);assert r.status_code==201,r.text
 f={'root':root,'path':path,'base':base,'h':headers,'channel':r.json(),'app':app,'meta':meta,'client':client}
 configure(f);verify(f);handshake(f);save(f,enabled=True)
 receive(f,'What is KCB M-PESA?');assert any(v.content==DEFINITION for v in transcript(f))
 receive(f,'Contact support');assert any('support@example.invalid' in v.content for v in transcript(f))
 drain(f);assert any(v.get('text',{}).get('body')==DEFINITION for v in meta.sent)


def test_failed_callback_cannot_be_downgraded_by_delayed_sent(whatsapp):
 f=whatsapp;receive(f);drain(f)
 with f['app'].state.session_factory() as db:row=db.scalar(select(Outbox));identifier=row.id;provider=row.provider_id
 for state in ['failed','sent']:
  webhook(f,statuses=[{'id':provider,'status':state,'recipient_id':PHONE,'errors':[{'code':131026}]}]);assert intake_one(f['app'],True)
 with f['app'].state.session_factory() as db:assert db.get(Outbox,identifier).status=='failed'


def test_stale_timeout_does_not_close_after_new_customer_activity(whatsapp):
 f=whatsapp;c=deepcopy(f['channel']['config']);c.update(auto_close=True,timeout_minutes=1);save(f,config=c);receive(f)
 with f['app'].state.session_factory() as db:
  db.scalar(select(Contact)).last_customer_at=utcnow()-timedelta(minutes=2)
  for m in db.scalars(select(AgentMessage)):m.created_at=utcnow()-timedelta(minutes=2)
  db.commit()
 assert schedule_timeout(f['app']);webhook(f,[incoming('Still here')]);assert intake_one(f['app']);assert not chat(f).workflow_state.get('closed')
 assert intake_one(f['app']);assert any(m.content=='Still here' for m in transcript(f))


def test_one_meta_app_routes_multiple_phone_numbers_to_isolated_channels(whatsapp):
 f=whatsapp;client=f['client'];second=client.post(f['root'],json={'client_id':str(uuid.uuid4()),'name':'Second business number'},headers=f['h']);assert second.status_code==201
 g={**f,'channel':second.json()};other='987654322';configure(g,phone_number_id=other);verify(g);assert g['channel']['verified'];handshake(g);save(g,enabled=True)
 assert webhook(f,[incoming('First phone')]).json()['received']==1
 assert webhook(g,[incoming('Second phone')],phone=other).json()['received']==1
 assert intake_one(f['app']) and intake_one(f['app'])
 with f['app'].state.session_factory() as db:
  contacts=list(db.scalars(select(Contact)));assert len(contacts)==2 and len({v.channel_id for v in contacts})==2
  assert {v.phone_number_id for v in contacts}=={PHONE_ID,other}


def test_phone_number_cannot_be_assigned_twice(whatsapp):
 f=whatsapp;r=f['client'].post(f['root'],json={'client_id':str(uuid.uuid4())},headers=f['h']);assert r.status_code==201
 g={**f,'channel':r.json()};response=post(g,'/configuration',{'app_id':APP_ID,'phone_number_id':PHONE_ID,'app_secret':SECRET,'access_token':TOKEN});assert response.status_code==409


def test_banned_person_does_not_receive_bot_or_staff_delivery(whatsapp):
 f=whatsapp;receive(f)
 with f['app'].state.session_factory() as db:
  c=db.scalar(select(Contact));person=db.get(SupportPerson,c.person_id);person.ban_permanent=True;db.commit()
 assert send_one(f['app']) and not f['meta'].sent


def test_oversized_webhook_is_rejected_before_queue(whatsapp):
 f=whatsapp;r=f['client'].post('/api/whatsapp/webhooks/'+APP_ID,content=b' '*(2*1024*1024+1));assert r.status_code==413


def test_provider_malformed_scopes_and_expiry_are_safe(app):
 for values in [{'scopes':None},{'expires_at':'not-a-time'},{'data_access_expires_at':1}]:
  body={'is_valid':True,'app_id':APP_ID,'scopes':['whatsapp_business_messaging','whatsapp_business_management'],'expires_at':0,**values}
  client=GraphClient(app.state.settings,httpx.MockTransport(lambda r:httpx.Response(200,json={'data':body})))
  with pytest.raises(GraphError):client.verify(APP_ID,SECRET,TOKEN,PHONE_ID)


def test_chart_and_carousel_have_readable_native_reply_fallbacks():
 from types import SimpleNamespace
 from app.whatsapp.delivery import parts
 conversation=SimpleNamespace(workflow_state={})
 message=SimpleNamespace(content='',message_data={'graph':{'title':'Sales','bar_label':'Bar','line_label':'Line','bar_data':[{'x':'Jan','y':4}],'line_data':[{'x':'Jan','y':7}]}})
 rendered=parts(message,conversation);body=rendered[0]['text']['body'];assert 'Bar' in body and 'Line' in body and 'y: 4' in body and 'y: 7' in body
 message.message_data={'carousel':{'cards':[{'title':'Support','subtitle':'Contact us','buttons':[{'type':'reply','label':'Contact','message':'Contact support'},{'type':'link','label':'Website','url':'https://example.com/help'}]}]}}
 rendered=parts(message,conversation);assert any('https://example.com/help' in str(v) for v in rendered)
 assert rendered[0]['_options']==[{'label':'Contact','value':'Contact support'}]


def test_cancelled_uncertain_send_can_still_receive_truthful_meta_status(whatsapp):
 f=whatsapp;receive(f);f['meta'].failure='timeout';assert send_one(f['app'])
 with f['app'].state.session_factory() as db:row=db.scalar(select(Outbox).where(Outbox.status=='uncertain'));identifier=row.id
 assert post(f,'/activity/outbound/'+str(identifier),{'action':'cancel','confirmed':True}).status_code==200
 webhook(f,statuses=[{'id':'wamid.accepted.after.cancel','status':'delivered','recipient_id':PHONE,'biz_opaque_callback_data':str(identifier)}]);assert intake_one(f['app'],True)
 with f['app'].state.session_factory() as db:assert db.get(Outbox,identifier).status=='delivered'
