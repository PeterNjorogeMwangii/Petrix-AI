import io,json,secrets,uuid
from copy import deepcopy
from datetime import timedelta
import pytest
from PIL import Image
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import AgentConversation,AgentMessage,SupportPerson,WebchatChannel,WebchatConversation,WebchatRequest,WebchatSession
from test_agents import agent_workspace
from test_workflows import node,message

@pytest.fixture
def webchat(client,agent_workspace,app):
 base,agent,h=agent_workspace;path=base+'/'+agent['id'];root=path+'/webchat'
 r=client.post(root,json={'client_id':str(uuid.uuid4()),'name':'Website support'},headers=h);assert r.status_code==201,r.text
 channel=r.json();r=client.post(root+'/'+channel['id'],json={'name':channel['name'],'config':channel['config'],'enabled':True,'expected_revision':channel['revision']},headers=h);assert r.status_code==200,r.text
 return {'root':root,'path':path,'base':base,'agent':agent,'h':h,'channel':r.json(),'public':'/api/webchat/'+channel['id'],'app':app}
def visitor(client,f,host='https://example.com',preview=''):
 token=secrets.token_urlsafe(32);r=client.post(f['public']+'/sessions'+('?preview='+preview if preview else ''),json={'token':token,'host_origin':host},headers={'Origin':'https://example.com','Sec-Fetch-Site':'cross-site'});assert r.status_code==201,r.text
 return {'Authorization':'Bearer '+token}
def start(client,f,h,values=None,key=None):
 r=client.post(f['public']+'/conversations',json={'request_id':key or str(uuid.uuid4()),'values':values or {}},headers=h);assert r.status_code==201,r.text;return r.json()
def save(client,f,config=None,enabled=True):
 c=f['channel'];r=client.post(f['root']+'/'+c['id'],json={'name':c['name'],'config':config or c['config'],'enabled':enabled,'expected_revision':c['revision']},headers=f['h']);assert r.status_code==200,r.text;f['channel']=r.json();return r.json()
def send(client,f,h,chat,text='Hello',key=None,**kw):return client.post(f['public']+'/conversations/'+chat['conversation_id']+'/events',json={'request_id':key or str(uuid.uuid4()),'content':text,**kw},headers=h)

def test_channels_create_retry_permissions_revision_and_enable(client,webchat):
 f=webchat;id=str(uuid.uuid4());a=client.post(f['root'],json={'client_id':id},headers=f['h']);b=client.post(f['root'],json={'client_id':id},headers=f['h']);assert a.json()['id']==b.json()['id'] and not a.json()['enabled']
 assert client.post(f['root'],json={'client_id':str(uuid.uuid4())}).status_code==403
 c=f['channel'];save(client,f)
 assert client.post(f['root']+'/'+c['id'],json={'name':'stale','enabled':True,'config':c['config'],'expected_revision':c['revision']},headers=f['h']).status_code==409
 save(client,f,enabled=False);assert client.get(f['public']+'/config?host_origin=https://example.com').status_code==404

def test_cross_site_visitor_transport_does_not_relax_staff_csrf(client,webchat):
 f=webchat;h=visitor(client,f);chat=start(client,f,h);assert chat['messages'] and not chat['closed']
 assert client.post(f['root'],json={'client_id':str(uuid.uuid4())},headers={**f['h'],'Origin':'https://example.com'}).status_code==403
 assert client.get(f['public']+'/conversations').status_code==401

def test_visitor_history_isolated_and_no_staff_or_trace_state(client,webchat):
 f=webchat;a=visitor(client,f);b=visitor(client,f);chat=start(client,f,a)
 assert len(client.get(f['public']+'/conversations',headers=a).json()['items'])==1
 assert client.get(f['public']+'/conversations',headers=b).json()['items']==[]
 assert client.get(f['public']+'/conversations/'+chat['conversation_id'],headers=b).status_code==404
 assert not {'variables','trace','custom_fields','matching','persona','actor_id'}&set(chat)
 assert all(not {'node_id','source','author_id'}&set(m) for m in chat['messages'])
 with f['app'].state.session_factory() as db:
  c=db.get(AgentConversation,uuid.UUID(chat['conversation_id']));assert c.workflow_state['channel']=='webchat'
  person=db.get(SupportPerson,c.person_id);assert not person.email and not person.user_id

def test_start_and_messages_are_idempotent(client,webchat):
 f=webchat;h=visitor(client,f);key=str(uuid.uuid4());a=start(client,f,h,key=key);b=start(client,f,h,key=key);assert a['conversation_id']==b['conversation_id']
 key=str(uuid.uuid4());a=send(client,f,h,a,'hello',key);assert a.status_code==200,a.text;b=send(client,f,h,a.json(),'hello',key);assert b.status_code==200
 assert len([m for m in b.json()['messages'] if m['role']=='user'])==1
 assert send(client,f,h,b.json(),'different',key).status_code==409

@pytest.mark.parametrize('value',['javascript:alert(1)','https://good.com/path','https://a.com@b.com','https://*.example.com','null'])
def test_invalid_whitelist_origins_rejected(client,webchat,value):
 f=webchat;c=deepcopy(f['channel']['config']);c.update(whitelist=True,domains=[value]);r=client.post(f['root']+'/'+f['channel']['id'],json={'name':'Web','enabled':True,'config':c,'expected_revision':f['channel']['revision']},headers=f['h']);assert r.status_code==422

def test_whitelist_csp_and_old_session_revocation(client,webchat):
 f=webchat;h=visitor(client,f);c=deepcopy(f['channel']['config']);c.update(whitelist=True,domains=['https://approved.example']);save(client,f,c)
 assert client.get(f['public']+'/config?host_origin=https://example.com').status_code==403
 assert client.get(f['public']+'/conversations',headers=h).status_code==403
 frame=client.get(f['public']+'/frame?host_origin=https://approved.example');assert frame.status_code==200,frame.text
 assert 'frame-ancestors' in frame.headers['content-security-policy'] and 'https://approved.example' in frame.headers['content-security-policy'] and 'x-frame-options' not in frame.headers
 assert client.get('/api/health').headers['x-frame-options']=='DENY'

def test_preview_works_disabled_and_marks_chats_as_tests(client,webchat):
 f=webchat;save(client,f,enabled=False);r=client.post(f['root']+'/'+f['channel']['id']+'/preview',json={'config':f['channel']['config']},headers=f['h']);assert r.status_code==201,r.text
 from urllib.parse import urlsplit,parse_qs
 token=parse_qs(urlsplit(r.json()['url']).query)['preview'][0];h=visitor(client,f,'http://localhost:8000',token);chat=start(client,f,h)
 with f['app'].state.session_factory() as db:assert db.get(AgentConversation,uuid.UUID(chat['conversation_id'])).workflow_state['channel']=='tester'
 assert client.get(f['public']+'/demo?preview='+token).status_code==200

def test_prechat_values_required_typed_and_saved_to_people(client,webchat):
 f=webchat;c=deepcopy(f['channel']['config']);c['prechat']={'enabled':True,'message':'About you','button':'Begin','fields':[{'key':'name','label':'Your name','required':True},{'key':'email','label':'Email','required':True}]};save(client,f,c);h=visitor(client,f)
 for values in [{},{'name':'Ada','email':'invalid'},{'name':'Ada','email':'a@b.com','internal':'no'}]:
  r=client.post(f['public']+'/conversations',json={'request_id':str(uuid.uuid4()),'values':values},headers=h);assert r.status_code==422,r.text
 chat=start(client,f,h,{'name':'Ada','email':'ada@example.com'})
 with f['app'].state.session_factory() as db:
  p=db.get(SupportPerson,db.get(AgentConversation,uuid.UUID(chat['conversation_id'])).person_id);assert p.name=='Ada' and p.email=='ada@example.com' and not p.user_id

def test_existing_chats_keep_published_workflow_new_chats_get_new_publish(client,webchat):
 f=webchat;h=visitor(client,f);first=start(client,f,h)
 doc=client.get(f['path']+'/workflows').json();greeting=next(n for n in doc['nodes'] if n['type']=='chat_started');greeting['children'][0]['config']['translations']['en']['messages']=['NEW PUBLISHED GREETING']
 assert client.post(f['path']+'/workflows',json={'expected_revision':doc['revision'],'nodes':doc['nodes']},headers=f['h']).status_code==200
 before=start(client,f,h);assert 'NEW PUBLISHED GREETING' not in json.dumps(before)
 save(client,f);after=start(client,f,h);assert 'NEW PUBLISHED GREETING' in json.dumps(after) and after['workflow_revision']!=first['workflow_revision']
 assert client.get(f['public']+'/conversations/'+first['conversation_id'],headers=h).json()['workflow_revision']==first['workflow_revision']

def test_general_upload_private_download_and_disabled_attachments(client,webchat):
 f=webchat;h=visitor(client,f);chat=start(client,f,h);path=f['public']+'/conversations/'+chat['conversation_id']
 key=str(uuid.uuid4());r=client.post(path+'/attachments',data={'request_id':key},files={'files':('note.txt',b'PRIVATE VISITOR FILE','text/plain')},headers=h);assert r.status_code==200,r.text
 file=r.json()['messages'][-1]['attachments'][0];download=path+'/files/'+file['id'];assert client.get(download,headers=h).content==b'PRIVATE VISITOR FILE'
 assert client.get(download).status_code==401 and client.get(download,headers=visitor(client,f)).status_code==404
 repeat=client.post(path+'/attachments',data={'request_id':key},files={'files':('note.txt',b'PRIVATE VISITOR FILE','text/plain')},headers=h);assert repeat.status_code==200
 c=deepcopy(f['channel']['config']);c['attachments']=False;save(client,f,c)
 assert client.post(path+'/attachments',data={'request_id':str(uuid.uuid4())},files={'files':('x.txt',b'x')},headers=h).status_code==403

def test_voice_requires_both_agent_options_and_images_validate(client,webchat):
 f=webchat;c=deepcopy(f['channel']['config']);c['voice']=True
 assert client.post(f['root']+'/'+f['channel']['id'],json={'name':'Web','enabled':True,'config':c,'expected_revision':f['channel']['revision']},headers=f['h']).status_code==422
 assert client.post(f['root']+'/assets',files={'file':('bad.png',b'not image','image/png')},headers=f['h']).status_code==422
 image=io.BytesIO();Image.new('RGB',(20,20),'red').save(image,'PNG');r=client.post(f['root']+'/assets',files={'file':('icon.png',image.getvalue(),'image/png')},headers=f['h']);assert r.status_code==201,r.text
 c=deepcopy(f['channel']['config']);c['appearance']['launcher_icon']=r.json()['id'];save(client,f,c)
 assert client.get(f['public']+'/assets/'+r.json()['id']).headers['content-type']=='image/png'

def test_auto_timeout_runs_once_and_closes(client,webchat):
 f=webchat;c=deepcopy(f['channel']['config']);c.update(auto_close=True,timeout_minutes=1);save(client,f,c);h=visitor(client,f);chat=start(client,f,h)
 with f['app'].state.session_factory() as db:
  for m in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(chat['conversation_id']))):m.created_at=utcnow()-timedelta(minutes=2)
  db.commit()
 path=f['public']+'/conversations/'+chat['conversation_id']+'/poll';a=client.post(path,json={},headers=h);assert a.status_code==200,a.text;assert a.json()['closed']
 assert client.post(path,json={},headers=h).json()['closed']

def test_public_reply_never_exposes_internal_messages(client,webchat):
 f=webchat;h=visitor(client,f);chat=start(client,f,h)
 with f['app'].state.session_factory() as db:
  db.add(AgentMessage(conversation_id=uuid.UUID(chat['conversation_id']),role='internal',content='SECRET STAFF NOTE'));db.commit()
 r=client.get(f['public']+'/conversations/'+chat['conversation_id'],headers=h);assert 'SECRET STAFF NOTE' not in r.text

def test_workflow_pdf_bot_answers_general_questions_and_runs_exact_workflow(client,agent_workspace,app):
 from test_agent_bundle import upload,GroundingProbe,DEFINITION
 base,agent,h=agent_workspace;r=upload(client,base,h);assert r.status_code==201,r.text
 path=base+'/'+r.json()['id'];root=path+'/webchat';r=client.post(root,json={'client_id':str(uuid.uuid4())},headers=h);c=r.json()
 f={'root':root,'path':path,'h':h,'channel':c,'public':'/api/webchat/'+c['id'],'app':app};save(client,f);app.state.llm_processing_client=GroundingProbe()
 v=visitor(client,f);chat=start(client,f,v);answer=send(client,f,v,chat,'What is KCB M-PESA?');assert answer.status_code==200,answer.text
 assert any(m['content']==DEFINITION for m in answer.json()['messages'])
 contact=send(client,f,v,chat,'Contact support');assert contact.status_code==200 and 'support@example.invalid' in contact.text
 assert 'trace' not in answer.json() and 'knowledge_references' not in answer.text

def test_survey_questions_and_answers_stay_on_frozen_revision(client,agent_workspace,app):
 from test_agent_bundle import upload,GroundingProbe
 base,agent,h=agent_workspace;new=upload(client,base,h).json();path=base+'/'+new['id'];root=path+'/webchat';c=client.post(root,json={'client_id':str(uuid.uuid4())},headers=h).json()
 f={'root':root,'path':path,'h':h,'channel':c,'public':'/api/webchat/'+c['id'],'app':app};save(client,f);app.state.llm_processing_client=GroundingProbe();v=visitor(client,f);chat=start(client,f,v)
 r=send(client,f,v,chat,'Log demo complaint');assert r.status_code==200,r.text;survey=r.json()['survey'];assert survey and 'variable_name' not in survey
 r=send(client,f,v,chat,'Demo Person',survey_answer_token=survey['answer_token']);assert r.status_code==200,r.text
 r=send(client,f,v,chat,'Demo complaint',survey_answer_token=r.json()['survey']['answer_token']);assert r.status_code==200,r.text;assert 'DEMO-1001' in r.text

def test_person_ban_blocks_messages_and_future_chats(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v)
 with f['app'].state.session_factory() as db:
  p=db.get(SupportPerson,db.get(AgentConversation,uuid.UUID(chat['conversation_id'])).person_id);p.ban_permanent=True;db.commit()
 assert send(client,f,v,chat,'blocked').status_code==403
 assert client.post(f['public']+'/conversations',json={'request_id':str(uuid.uuid4()),'values':{}},headers=v).status_code==403

def test_disabled_channel_stops_existing_sessions(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v);save(client,f,enabled=False)
 assert send(client,f,v,chat).status_code==404
 assert client.get(f['public']+'/conversations/'+chat['conversation_id'],headers=v).status_code==404

def test_hidden_offline_widget_and_start_are_consistent(client,webchat):
 f=webchat;c=deepcopy(f['channel']['config']);c['hide_offline']=True;save(client,f,c)
 assert not client.get(f['public']+'/config?host_origin=https://example.com').json()['available']
 v=visitor(client,f);assert client.post(f['public']+'/conversations',json={'request_id':str(uuid.uuid4())},headers=v).status_code==409

def test_pending_request_is_not_replayed_and_scoped_status_is_private(client,webchat):
 from app.webchat.service import digest
 f=webchat;v=visitor(client,f);chat=start(client,f,v);key=uuid.uuid4();body={'request_id':str(key),'event':'message_received','content':'Pending','survey_answer_token':None,'feedback_token':None,'rating':None,'feedback_answers':{},'voice':False}
 with f['app'].state.session_factory() as db:
  record_visitor=db.scalar(select(WebchatSession).where(WebchatSession.channel_id==uuid.UUID(f['channel']['id'])));db.add(WebchatRequest(session_id=record_visitor.id,request_id=key,conversation_id=uuid.UUID(chat['conversation_id']),digest=digest({'conversation':chat['conversation_id'],**body}),status='running'));db.commit()
 assert send(client,f,v,chat,'Pending',str(key)).status_code==409
 assert client.get(f['public']+'/requests/'+str(key),headers=v).json()['status']=='running'
 assert client.get(f['public']+'/requests/'+str(key),headers=visitor_header(client,f)).status_code==404

def visitor_header(client,f):return visitor(client,f)

def test_voice_settings_persist_and_gate_channel(client,webchat):
 f=webchat;agent=client.get(f['base']).json()['agents'][0]
 r=client.post(f['path']+'/voice',json={'expected_revision':agent['revision'],'voice_enabled':True,'voice_name':'','voice_speed':1.2,'speech_to_text_enabled':True,'speech_languages':['en'],'include_audio_transcript':False},headers=f['h']);assert r.status_code==200,r.text
 assert r.json()['speech_to_text_enabled'] and r.json()['speech_languages']==['en'] and not r.json()['include_audio_transcript']
 c=deepcopy(f['channel']['config']);c.update(voice=True,audio=True,playback=True);save(client,f,c)
 config=client.get(f['public']+'/config?host_origin=https://example.com').json();assert config['speech_to_text'] and config['text_to_speech'] and not config['include_audio_transcript']

def test_deleting_channel_retains_customer_conversation(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v);c=f['channel']
 assert client.post(f['root']+'/'+c['id']+'/delete',json={'expected_revision':c['revision'],'confirmed':False},headers=f['h']).status_code==409
 assert client.post(f['root']+'/'+c['id']+'/delete',json={'expected_revision':c['revision'],'confirmed':True},headers=f['h']).status_code==200
 with f['app'].state.session_factory() as db:assert db.get(AgentConversation,uuid.UUID(chat['conversation_id'])) is not None

def test_live_support_sees_visitor_and_replies_are_public_without_internal_notes(client,webchat):
 from test_live_agent_handoff import handoff
 f=webchat
 profile=client.get(f['path']+'/workflows').json();profile['nodes'][0]['children']=[message('Connecting to support.'),handoff(availability_check=False)]
 # Handoff eligibility is independent of availability when explicitly configured.
 uid=client.get('/api/auth/me').json()['user']['id']
 team=client.post(f['base'].split('/agents')[0]+'/teams',json={'name':'Support','description':'','alternative_names':[],'member_ids':[uid]},headers=f['h']);assert team.status_code==201,team.text
 assert client.post(f['path']+'/workflows',json={'expected_revision':profile['revision'],'nodes':profile['nodes']},headers=f['h']).status_code==200
 root=f['base'].split('/agents')[0];profile=client.get(root+'/inbox/profile').json();assert client.post(root+'/inbox/profile',json={'expected_revision':profile['revision'],'available':True,'languages':['en']},headers=f['h']).status_code==200
 save(client,f);v=visitor(client,f);chat=start(client,f,v);assert chat['live_status']=='pending'
 root=f['base'].split('/agents')[0];detail=client.get(root+'/inbox/chats/'+chat['conversation_id']).json()
 claimed=client.post(root+'/inbox/chats/'+chat['conversation_id']+'/claim',json={'expected_revision':detail['revision']},headers=f['h']);assert claimed.status_code==200,claimed.text
 reply=client.post(root+'/inbox/chats/'+chat['conversation_id']+'/reply',json={'content':'Hello from a human','client_message_id':str(uuid.uuid4()),'expected_revision':claimed.json()['revision']},headers=f['h']);assert reply.status_code==200,reply.text
 public=client.get(f['public']+'/conversations/'+chat['conversation_id'],headers=v).json();assert any(m['content']=='Hello from a human' and m['live_agent'] for m in public['messages'])

@pytest.mark.parametrize('rating_enabled',[True,False])
def test_public_feedback_keeps_field_types_and_hides_workflow_variables(client,webchat,rating_enabled):
 from test_collect_feedback import form,field
 f=webchat;doc=client.get(f['path']+'/workflows').json();request=form(rating=rating_enabled,survey=True,fields=[field('choice',True,choices=['Helpful','Needs improvement'])]);next(n for n in doc['nodes'] if n['type']=='chat_closed')['children']=[request]
 assert client.post(f['path']+'/workflows',json={'expected_revision':doc['revision'],'nodes':doc['nodes']},headers=f['h']).status_code==200;save(client,f)
 v=visitor(client,f);chat=start(client,f,v);r=send(client,f,v,chat,'',event='chat_closed');assert r.status_code==200,r.text
 feedback=r.json()['feedback'];assert feedback and feedback['fields'][0]['reply_type']=='choice';assert 'variable_name' not in feedback['fields'][0] and 'rating_variable_name' not in feedback
 r=send(client,f,v,chat,'',event='collect_feedback',rating=5 if rating_enabled else None,feedback_token=feedback['token'],feedback_answers={feedback['fields'][0]['id']:'Helpful'});assert r.status_code==200,r.text
 assert r.json()['feedback'] is None and 'Thank you' in r.json()['feedback_confirmation']

def test_invalid_empty_message_does_not_leave_a_running_receipt(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v);key=str(uuid.uuid4())
 assert send(client,f,v,chat,'',key).status_code==422
 assert client.get(f['public']+'/requests/'+key,headers=v).status_code==404

def test_timeout_worker_closes_without_browser_polling_and_does_not_repeat(client,webchat):
 from app.webchat.worker import run_one
 f=webchat;c=deepcopy(f['channel']['config']);c.update(auto_close=True,timeout_minutes=1);save(client,f,c);v=visitor(client,f);chat=start(client,f,v)
 with f['app'].state.session_factory() as db:
  for m in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(chat['conversation_id']))):m.created_at=utcnow()-timedelta(minutes=2)
  db.commit()
 assert run_one(f['app'])
 assert not run_one(f['app'])
 assert client.get(f['public']+'/conversations/'+chat['conversation_id'],headers=v).json()['closed']

def test_audio_media_type_parameters_keep_clips_playable(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v)
 r=client.post(f['public']+'/conversations/'+chat['conversation_id']+'/attachments',headers=v,data={'request_id':str(uuid.uuid4())},files={'files':('voice.webm',b'webm audio fixture','audio/webm; codecs=opus')});assert r.status_code==200,r.text
 assert r.json()['messages'][-1]['attachments'][0]['content_type']=='audio/webm'

def test_public_pagination_counts_public_messages_before_private_rows(client,webchat):
 f=webchat;v=visitor(client,f);chat=start(client,f,v);cid=uuid.UUID(chat['conversation_id'])
 with f['app'].state.session_factory() as db:
  for i in range(105):db.add(AgentMessage(conversation_id=cid,role='internal',content='PRIVATE NOTE '+str(i)))
  db.commit()
 data=client.get(f['public']+'/conversations/'+str(cid),headers=v).json();assert len(data['messages'])==1 and not data['has_more']
 with f['app'].state.session_factory() as db:
  for i in range(105):db.add(AgentMessage(conversation_id=cid,role='user',content='Public '+str(i)))
  db.commit()
 data=client.get(f['public']+'/conversations/'+str(cid),headers=v).json();assert len(data['messages'])==100 and data['has_more'] and 'PRIVATE NOTE' not in json.dumps(data)
 prior=client.get(f['public']+'/conversations/'+str(cid)+'?before='+data['messages'][0]['id'],headers=v).json();assert len(prior['messages'])==6 and not prior['has_more']
