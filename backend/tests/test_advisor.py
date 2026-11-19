"""Private Advisor state, grounded replies, files, leases and documentation."""
import io,json,uuid
from datetime import timedelta
from types import SimpleNamespace
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import AdvisorConversation,AdvisorMessage,AdvisorAttachment,Membership,User,Workspace,Teamspace
from app.advisor import worker,knowledge
from app.advisor.client import Reply,AdvisorAI
from test_agents import agent_workspace
from test_workspace_settings import ws

@pytest.fixture
def advisor(client,ws,app):
 root,path,agent,h=ws;o=client.get(root+'/advisor/options');assert o.status_code==200,o.text
 return {'root':root,'base':root+'/advisor','h':h,'space':o.json()['teamspaces'][0]['id'],'ws':uuid.UUID(root.split('/')[3]),'app':app}

def post(client,f,path,body,status=200):
 r=client.post(f['base']+path,json=body,headers=f['h']);assert r.status_code==status,r.text;return r.json()
def chat(client,f,**kw):return post(client,f,'/conversations',{'client_id':str(uuid.uuid4()),'teamspace_id':f['space'],**kw},201)
def send(client,f,c,text='How do I test an AI agent?',files=None,request=None,status=202):return post(client,f,'/conversations/'+c['id']+'/messages',{'content':text,'client_request_id':request or str(uuid.uuid4()),'expected_revision':c['revision'],'attachment_ids':files or []},status)
def upload(client,f,name='guide.txt',data=b'A readable platform attachment.'):
 r=client.post(f['base']+'/attachments',files={'file':(name,data)},headers=f['h']);assert r.status_code==201,r.text;return r.json()
def run(f,ai=None):return worker.run_one(f['app'].state.session_factory,ai or SimpleNamespace(available=False,vision=False))
def detail(client,f,c):
 r=client.get(f['base']+'/conversations/'+c['id']);assert r.status_code==200,r.text;return r.json()
class FakeAI:
 available=True;vision=False
 def __init__(self):self.calls=[]
 def generate(self,messages):
  self.calls.append(messages);content=messages[-1]['content'];value=json.loads(content if isinstance(content,str) else content[0]['text']);e=value['evidence'];return Reply(True,text=json.dumps({'answer':'Open **AI agents**, save changes, and use the tester.','citation_ids':[e[0]['id']] if e else [],'navigation_ids':[v['id'] for v in value['navigation'] if v['id']=='agents']}))

def test_homepage_suggestions_model_flags_and_working_footer_targets(client,advisor):
 f=advisor;o=client.get(f['base']+'/options').json();assert len(o['suggestions'])==5 and not o['model_ready'] and not o['vision_ready']
 assert o['links']=={'apis':'/app/developer-api','documentation':'/app/documentation','privacy':'/privacy','terms':'/terms'}
 docs=client.get(f['base']+'/docs').json();assert docs['version']=='0.35.1' and any(d['slug']=='PLATFORM_GUIDE' for d in docs['documents'])
 assert client.get(f['base']+'/docs/PLATFORM_GUIDE').json()['content'].startswith('# Petrix AI platform guide')
 assert client.get(f['base']+'/docs/NOT_A_DOCUMENT').status_code==404
 schema=client.get(f['base']+'/openapi').json();assert schema['info']['version']=='0.35.1' and any('/advisor/' in k for k in schema['paths'])
 download=client.get(f['base']+'/openapi?download=true');assert 'attachment' in download.headers['content-disposition'] and download.json()['openapi']


def test_private_chat_create_idempotency_and_independent_start_chat(client,advisor):
 f=advisor;key=str(uuid.uuid4());a=chat(client,f,client_id=key);again=chat(client,f,client_id=key);b=chat(client,f)
 assert a['id']==again['id'] and a['id']!=b['id']
 assert client.get(f['base']+'/conversations').json()['total']==2
 assert client.post(f['base']+'/conversations',json={'client_id':str(uuid.uuid4()),'teamspace_id':f['space']}).status_code==403


def test_suggested_question_documentation_reply_survives_reload_and_continues(client,advisor):
 f=advisor;c=chat(client,f);d=send(client,f,c);assert d['busy'] and [v['status'] for v in d['messages']]==['completed','pending'];assert run(f)
 d=detail(client,f,c);answer=d['messages'][-1];assert answer['mode']=='documentation' and answer['status']=='completed' and 'tester' in answer['content'] and answer['sources']
 assert 'model' in answer['notice'].lower() and any(n['id']=='agents' for n in answer['navigation'])
 d=send(client,f,d,'What is a teamspace?');run(f);d=detail(client,f,c);assert len(d['messages'])==4 and 'teamspace' in d['messages'][-1]['content'].lower()
 assert client.get(f['base']+'/conversations').json()['items'][0]['title']=='How do I test an AI agent?'


@pytest.mark.parametrize('question,needle',[('What is a teamspace?','teamspace'),('How do I add custom fields?','Create new field'),('Can I ban customers?','Banning'),('How do I add team members?','Users'),('Can I publish my bot?','Webchat'),('What can Perception do?','Alerts'),('How do I use aliases?','Aliases')])
def test_common_questions_return_current_platform_guidance(client,advisor,question,needle):
 f=advisor;c=send(client,f,chat(client,f),question);run(f);d=detail(client,f,c);assert needle in d['messages'][-1]['content']


def test_unknown_question_does_not_get_an_unrelated_canned_answer(client,advisor):
 f=advisor;c=send(client,f,chat(client,f),'What is the capital of Neptune?');run(f);a=detail(client,f,c)['messages'][-1]
 assert 'Petrix AI' in a['content'] and 'test an AI agent' not in a['content'] and not a['sources']


def test_model_receives_retrieved_evidence_and_only_valid_navigation(client,advisor):
 f=advisor;c=send(client,f,chat(client,f));ai=FakeAI();run(f,ai);a=detail(client,f,c)['messages'][-1]
 assert a['mode']=='model' and a['sources'] and a['navigation'];assert ai.calls[0][0]['role']=='system'
 payload=json.loads(ai.calls[0][-1]['content']);assert payload['question']=='How do I test an AI agent?' and payload['evidence']
 assert 'api_key' not in json.dumps(payload) and 'execute actions' in ai.calls[0][0]['content']


def test_model_invented_sources_and_unavailable_provider_fall_back_honestly(client,advisor):
 f=advisor
 class BadAI(FakeAI):
  def generate(self,messages):return Reply(True,text=json.dumps({'answer':'Trust me.','citation_ids':['invented'],'navigation_ids':[]}))
 c=send(client,f,chat(client,f));run(f,BadAI());a=detail(client,f,c)['messages'][-1];assert a['mode']=='documentation' and 'could not be verified' in a['notice'] and 'Trust me' not in a['content']
 class Down(FakeAI):
  def generate(self,messages):return Reply(False,notice='Model connection is unavailable.')
 c=send(client,f,chat(client,f));run(f,Down());a=detail(client,f,c)['messages'][-1];assert a['mode']=='documentation' and a['notice']=='Model connection is unavailable.'


def test_duplicate_send_is_idempotent_and_conflicts_do_not_reuse_files(client,advisor):
 f=advisor;c=chat(client,f);key=str(uuid.uuid4());d=send(client,f,c,request=key);same=send(client,f,c,request=key);assert [v['id'] for v in same['messages']]==[v['id'] for v in d['messages']]
 assert client.post(f['base']+'/conversations/'+c['id']+'/messages',json={'content':'Changed','expected_revision':c['revision'],'client_request_id':key},headers=f['h']).status_code==409
 assert client.post(f['base']+'/conversations/'+c['id']+'/messages',json={'content':'Another','expected_revision':d['revision'],'client_request_id':str(uuid.uuid4())},headers=f['h']).status_code==409
 run(f);assert len(detail(client,f,c)['messages'])==2


def test_question_limits_empty_payload_and_rate_limit(client,advisor):
 f=advisor;c=chat(client,f)
 for text in ['', 'x'*8001]:
  r=client.post(f['base']+'/conversations/'+c['id']+'/messages',json={'content':text,'expected_revision':1,'client_request_id':str(uuid.uuid4())},headers=f['h']);assert r.status_code==422
 with f['app'].state.session_factory() as db:
  for i in range(20):db.add(AdvisorMessage(conversation_id=uuid.UUID(c['id']),client_request_id=uuid.uuid4(),role='user',content='Previous question'))
  db.commit()
 assert client.post(f['base']+'/conversations/'+c['id']+'/messages',json={'content':'Too fast','expected_revision':1,'client_request_id':str(uuid.uuid4())},headers=f['h']).status_code==429


def test_files_are_attached_to_actual_message_and_private_downloads(client,advisor):
 f=advisor;file=upload(client,f);c=send(client,f,chat(client,f),'Read the attached file.',[file['id']]);run(f);d=detail(client,f,c)
 assert d['messages'][0]['attachments'][0]['id']==file['id'] and d['messages'][-1]['sources'][0]['kind']=='attachment'
 assert client.get(file['url']).content==b'A readable platform attachment.'
 assert client.post(file['url']+'/delete',json={},headers=f['h']).status_code==409
 post(client,f,'/conversations/'+c['id']+'/delete',{'expected_revision':d['revision'],'confirmed':True});assert client.get(file['url']).status_code==404


def test_cross_account_history_records_and_attachments_never_visible_even_to_member(client,advisor):
 f=advisor;c=chat(client,f);file=upload(client,f);send(client,f,c,files=[file['id']]);run(f)
 with TestClient(f['app'],base_url='http://localhost:4200') as other:
  assert other.post('/api/auth/magic-link',json={'email':'other@gmail.com'}).status_code==202
  signed=other.post('/api/auth/verify',json={'token':f['app'].state.mailer.token}).json()
  with f['app'].state.session_factory() as db:db.add(Membership(workspace_id=f['ws'],user_id=uuid.UUID(signed['user']['id']),role='admin'));db.commit()
  assert other.get(f['base']+'/conversations').json()['total']==0
  assert other.get(f['base']+'/conversations/'+c['id']).status_code==404
  assert other.get(file['url']).status_code==404


def test_lost_teamspace_access_is_rechecked_for_records_files_and_background_completion(client,advisor):
 f=advisor;c=chat(client,f);file=upload(client,f);send(client,f,c,files=[file['id']]);factory=f['app'].state.session_factory;job=worker.claim(factory);assert job
 with factory() as db:
  member=db.scalar(select(Membership).where(Membership.workspace_id==f['ws'],Membership.role=='owner'));member.role='member';member.teamspace_ids=[];db.commit()
 assert client.get(f['base']+'/conversations/'+c['id']).status_code==404 and client.get(file['url']).status_code==404
 assert not worker.finish(factory,job,{'content':'Private answer','sources':[],'navigation':[],'mode':'model','notice':''})
 with factory() as db:assert db.get(AdvisorMessage,job['id']).content==''


@pytest.mark.parametrize('filename,data',[('bad.exe',b'file'),('bad.pdf',b'%PDF-broken'),('bad.docx',b'not zip'),('empty.txt',b''),('bad.png',b'not image')])
def test_unsupported_or_corrupt_files_rejected(client,advisor,filename,data):
 r=client.post(advisor['base']+'/attachments',files={'file':(filename,data)},headers=advisor['h']);assert r.status_code==422


def test_staged_file_removal_and_expiry_cleanup(client,advisor):
 f=advisor;one=upload(client,f);post(client,f,'/attachments/'+one['id']+'/delete',{});assert client.get(one['url']).status_code==404
 old=upload(client,f)
 with f['app'].state.session_factory() as db:db.get(AdvisorAttachment,uuid.UUID(old['id'])).created_at=utcnow()-timedelta(days=2);db.commit()
 worker.claim(f['app'].state.session_factory);assert client.get(old['url']).status_code==404


def test_image_preview_and_vision_input_are_normalized_and_cited(client,advisor):
 f=advisor;out=io.BytesIO();Image.new('RGB',(100,60),'orange').save(out,'PNG');file=upload(client,f,'screenshot.png',out.getvalue());assert file['is_image'] and client.get(file['preview_url']).headers['content-type'].startswith('image/jpeg')
 c=send(client,f,chat(client,f),'What does this attached screenshot show?',[file['id']])
 class Vision(FakeAI):vision=True
 ai=Vision();run(f,ai);a=detail(client,f,c)['messages'][-1];assert a['mode']=='model';parts=ai.calls[0][-1]['content'];assert parts[-1]['image_url']['url'].startswith('data:image/jpeg;base64,')
 c=send(client,f,chat(client,f),'What is in this image?',[upload(client,f,'screen.png',out.getvalue())['id']]);run(f);a=detail(client,f,c)['messages'][-1];assert 'does not inspect' in a['notice']


def test_expired_turn_can_retry_without_duplicate_user_message_and_old_worker_is_fenced(client,advisor):
 f=advisor;c=send(client,f,chat(client,f));factory=f['app'].state.session_factory;job=worker.claim(factory);assert worker.claim(factory) is None
 with factory() as db:db.get(AdvisorMessage,job['id']).lease_until=utcnow()-timedelta(seconds=1);db.commit()
 worker.claim(factory);d=detail(client,f,c);assert d['messages'][-1]['status']=='failed'
 assert not worker.finish(factory,job,{'content':'Late','sources':[],'navigation':[],'mode':'model','notice':''})
 post(client,f,'/conversations/'+c['id']+'/retry/'+str(job['id']),{},202);run(f);d=detail(client,f,c);assert len(d['messages'])==2 and d['messages'][-1]['status']=='completed' and d['messages'][-1]['attempts']==2


def test_history_and_message_pagination_stay_private_and_stable(client,advisor):
 f=advisor;c=chat(client,f)
 with f['app'].state.session_factory() as db:
  for i in range(65):db.add(AdvisorMessage(conversation_id=uuid.UUID(c['id']),client_request_id=uuid.uuid4(),role='user',content='Question '+str(i),created_at=utcnow()+timedelta(microseconds=i)))
  db.commit()
 first=client.get(f['base']+'/conversations/'+c['id']+'?limit=40').json();assert first['has_more'] and len(first['messages'])==40
 older=client.get(f['base']+'/conversations/'+c['id'],params={'before':first['messages'][0]['id'],'limit':40}).json();assert not older['has_more'] and len(older['messages'])==25 and not(set(v['id'] for v in first['messages'])&set(v['id'] for v in older['messages']))


def test_deleted_conversation_drops_inflight_result(client,advisor):
 f=advisor;c=send(client,f,chat(client,f));factory=f['app'].state.session_factory;job=worker.claim(factory)
 post(client,f,'/conversations/'+c['id']+'/delete',{'expected_revision':c['revision'],'confirmed':True})
 assert not worker.finish(factory,job,{'content':'Late','sources':[],'navigation':[],'mode':'model','notice':''})


def test_attachment_passages_retrieve_late_content_and_cannot_link_other_workspaces(client,advisor):
 f=advisor;text=('Generic notes about the platform.\n'*900)+'MAGIC-7781 is the requested setting.';file=upload(client,f,'long.txt',text.encode());c=send(client,f,chat(client,f),'Find MAGIC-7781 in the attached file.',[file['id']]);ai=FakeAI();run(f,ai);payload=json.loads(ai.calls[0][-1]['content']);assert 'MAGIC-7781 is the requested setting.' in str(payload['evidence'])
 assert client.get(f['root'].replace(str(f['ws']),str(uuid.uuid4()))+'/advisor/attachments/'+file['id']).status_code in {403,404}


def test_connection_fallback_and_http_errors_never_expose_credentials(monkeypatch,app):
 import httpx
 import app.advisor.client as module
 settings=app.state.settings.model_copy(update={'support_ai_base_url':'https://advisor.example/v1','support_ai_model':'model','support_ai_api_key':__import__('pydantic').SecretStr('PRIVATE-KEY')})
 ai=AdvisorAI(settings);assert ai.available and ai.connection[2]=='model';assert not ai.vision
 original=httpx.Client;seen=[]
 def handler(request):seen.append(request);return httpx.Response(401,text='PRIVATE-KEY')
 monkeypatch.setattr(module.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw));r=ai.generate([{'role':'user','content':'Question'}]);assert not r.ok and 'PRIVATE-KEY' not in r.notice and seen[0].headers['authorization']=='Bearer PRIVATE-KEY'


def test_short_new_topic_does_not_inherit_the_previous_question():
 previous=[{'role':'user','content':'How do I test an AI agent?'}]
 assert knowledge.topic('What is a teamspace?',previous)['key']=='teamspace'
 assert knowledge.topic('Where do I save it?',previous)['key']=='test'
 found=knowledge.retrieve('What is a teamspace?',previous);assert found and 'teamspace' in found[0]['text'].lower()


@pytest.mark.parametrize('kind',['pdf','docx'])
def test_readable_pdf_and_docx_text_is_included_with_the_question(client,advisor,kind):
 from test_agent_knowledge import pdf,docx
 f=advisor;data=pdf('Attachment marker MAGIC-7781') if kind=='pdf' else docx();file=upload(client,f,'source.'+kind,data);c=send(client,f,chat(client,f),'Explain this attached document.',[file['id']]);ai=FakeAI();run(f,ai)
 payload=json.loads(ai.calls[0][-1]['content']);assert any(e['id']=='file:'+file['id'] and ('MAGIC-7781' in e['text'] if kind=='pdf' else 'five business days' in e['text']) for e in payload['evidence'])
 assert detail(client,f,c)['messages'][0]['attachments'][0]['name']=='source.'+kind


@pytest.mark.parametrize('encrypted',[False,True])
def test_scanned_or_encrypted_pdf_is_rejected_without_claiming_ocr(client,advisor,encrypted):
 from test_agent_knowledge import pdf
 r=client.post(advisor['base']+'/attachments',files={'file':('scanned.pdf',pdf('' if not encrypted else 'Encrypted text',encrypted=encrypted))},headers=advisor['h']);assert r.status_code==422


def test_oversized_file_and_six_file_message_are_rejected(client,advisor):
 f=advisor;r=client.post(f['base']+'/attachments',files={'file':('large.txt',b'x'*(10*1024*1024+1))},headers=f['h']);assert r.status_code==422
 c=chat(client,f);r=client.post(f['base']+'/conversations/'+c['id']+'/messages',json={'client_request_id':str(uuid.uuid4()),'expected_revision':1,'content':'Files','attachment_ids':[str(uuid.uuid4()) for _ in range(6)]},headers=f['h']);assert r.status_code==422


def test_current_whatsapp_setup_is_documented_in_advisor(client,advisor):
 f=advisor;c=chat(client,f);send(client,f,c,text='How do I connect WhatsApp?');assert run(f)
 answer=detail(client,f,c)['messages'][-1];assert 'WhatsApp' in answer['content'] and 'Lite' in answer['content'] and 'webhook' in answer['content'].lower()
 assert any(source['id']=='WHATSAPP' for source in answer['sources'])
