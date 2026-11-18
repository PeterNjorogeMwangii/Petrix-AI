"""Knowledge ingestion, ownership, recovery, grounding and screening without live requests."""
import io,json,uuid,zipfile,gzip
from datetime import timedelta
from types import SimpleNamespace
import httpx,pytest
from sqlalchemy import select,func
from app.models import Agent,AgentKnowledgeSource as Source,AgentKnowledgePolicy,AgentKnowledge,AgentConversation,AgentMessage,Membership
from app.agent_knowledge.extract import extract,ImportFailure
from app.agent_knowledge.web import WebsiteReader,WebDocument
from app.agent_knowledge.service import retrieve
from app.agent_knowledge.moderation import Moderator,Verdict
from app.agent_knowledge import jobs
from app.core.security import utcnow
from test_workflows import workflow,event,save,message,contents,intent
from test_llm_processing import StubProvider,llm

@pytest.fixture
def knowledge(client,workflow):
 path,document,h=workflow;return path,path+'/knowledge',h

def snippet(client,k,name='Refund policy',content='Refunds take five business days.',**extra):
 path,base,h=k;result=client.post(base+'/snippets',json={'name':name,'content':content,**extra},headers=h);assert result.status_code==201,result.text;return result.json()

def listing(client,k,**params):return client.get(k[1]+'/sources',params=params).json()
def detail(client,k,source):return client.get(k[1]+'/sources/'+source['id']).json()
def edit(client,k,source,**extra):return client.post(k[1]+'/sources/'+source['id'],json={'expected_revision':source['revision'],'name':source['name'],'enabled':source['enabled'],**extra},headers=k[2])
def delete(client,k,rows):return client.post(k[1]+'/sources-delete',json={'ids':[r['id'] for r in rows],'revisions':{r['id']:r['revision'] for r in rows}},headers=k[2])

def pdf(text='Refund decisions take five business days.',encrypted=False):
 from pypdf import PdfWriter
 from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
 writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
 if text:
  font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')});page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})});stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 72 700 Td ('+text+') Tj ET').encode());page[NameObject('/Contents')]=writer._add_object(stream)
 if encrypted:writer.encrypt('password')
 out=io.BytesIO();writer.write(out);return out.getvalue()

def docx():
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w') as z:
  z.writestr('[Content_Types].xml','<Types/>');z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Refunds take five business days.</w:t></w:r></w:p></w:body></w:document>')
 return out.getvalue()

class Reader:
 def __init__(self,error=None,hook=None):self.calls=[];self.error=error;self.hook=hook
 def fetch(self,url):
  self.calls.append(url)
  if self.hook:self.hook()
  if self.error:raise ImportFailure(self.error)
  return WebDocument(b'<html><head><script>SECRET SCRIPT</script></head><body><nav>PRIVATE NAV</nav><h1>Customer help</h1><p>Refunds take five business days.</p></body></html>','page.html',url)

def url(client,k,app,reader=None):
 app.state.agent_knowledge_reader=reader or Reader();result=client.post(k[1]+'/urls',json={'name':'Website help','url':'https://help.example.com/policy'},headers=k[2]);assert result.status_code==201,result.text;return detail(client,k,result.json())

@pytest.mark.parametrize('filename,data',[('policy.txt',b'Refunds take five business days.'),('policy.md',b'# Refunds\nFive business days.'),('policy.csv',b'service,days\nrefund,5'),('policy.json',b'{"refund_days":5}'),('policy.html',b'<h1>Refunds</h1><script>NOT KNOWLEDGE</script><p>Five business days.</p>'),('policy.docx',None),('policy.pdf',None)])
def test_files_extract_index_and_keep_authorized_original(client,knowledge,app,filename,data):
 if data is None:data=docx() if filename.endswith('docx') else pdf()
 identifier=str(uuid.uuid4());response=client.post(knowledge[1]+'/files',data={'id':identifier},files={'file':(filename,data,'application/octet-stream')},headers=knowledge[2]);assert response.status_code==201,response.text
 source=detail(client,knowledge,response.json());assert source['status']=='ready',source;assert source['chunk_count']>=1 and source['text_bytes']>0 and 'NOT KNOWLEDGE' not in source['content']
 downloaded=client.get(knowledge[1]+'/sources/'+identifier+'/download');assert downloaded.content==data and 'attachment;' in downloaded.headers['content-disposition']
 with app.state.session_factory() as db:assert retrieve(db,uuid.UUID(knowledge[0].split('/')[-1]),'refund')
 duplicate=client.post(knowledge[1]+'/files',data={'id':identifier},files={'file':(filename,data,'application/octet-stream')},headers=knowledge[2]);assert duplicate.status_code==201 and listing(client,knowledge)['total']==1

@pytest.mark.parametrize('filename,data,error',[('empty.pdf',b'%PDF-invalid','document_invalid'),('scan.pdf',None,'no_text'),('encrypted.pdf',None,'encrypted_pdf'),('bad.docx',b'bad zip','document_invalid'),('bad.json',b'{broken','document_invalid'),('bad.txt',b'\x80\x81','text_encoding')])
def test_bad_documents_fail_visibly_and_do_not_enter_context(client,knowledge,app,filename,data,error):
 if data is None:data=pdf('',False) if filename=='scan.pdf' else pdf(encrypted=True)
 response=client.post(knowledge[1]+'/files',data={'id':str(uuid.uuid4())},files={'file':(filename,data)},headers=knowledge[2]);assert response.status_code==201,response.text;source=detail(client,knowledge,response.json());assert source['status']=='failed' and source['error_code']==error,source
 with app.state.session_factory() as db:assert retrieve(db,uuid.UUID(knowledge[0].split('/')[-1]),'refund')==[]

@pytest.mark.parametrize('filename,data,expected',[('script.exe',b'not executable',422),('empty.txt',b'',413),('big.txt',b'x'*(10*1024*1024+1),413)])
def test_reject_invalid_upload_before_persisting(client,knowledge,filename,data,expected):
 response=client.post(knowledge[1]+'/files',data={'id':str(uuid.uuid4())},files={'file':(filename,data)},headers=knowledge[2]);assert response.status_code==expected;assert listing(client,knowledge)['total']==0


def test_search_type_status_snippet_edits_and_atomic_deletion(client,knowledge):
 a=snippet(client,knowledge,name='Refund 100%');b=snippet(client,knowledge,name='Account policy',content='Account balance verification.')
 assert listing(client,knowledge,q='100%')['total']==1 and listing(client,knowledge,kind='url')['total']==0
 changed=edit(client,knowledge,a,enabled=False,content='Updated refund rules.');assert changed.status_code==200;updated=changed.json()
 assert listing(client,knowledge,status='disabled')['total']==1 and listing(client,knowledge,status='ready')['total']==1
 assert edit(client,knowledge,a,name='Stale').status_code==409
 assert delete(client,knowledge,[a,b]).status_code==409 and listing(client,knowledge)['total']==2
 assert delete(client,knowledge,[updated,b]).status_code==200 and listing(client,knowledge)['total']==0


def test_sources_and_downloads_are_agent_workspace_and_permission_scoped(client,knowledge,app):
 a=snippet(client,knowledge);other=client.post(knowledge[0].rsplit('/',1)[0],data={'name':'Other'},headers=knowledge[2]).json();foreign=knowledge[1].replace(knowledge[0].split('/')[-1],other['id'])
 assert client.get(foreign+'/sources/'+a['id']).status_code==404
 assert client.post(knowledge[1]+'/snippets',json={'name':'No CSRF','content':'x'}).status_code==403
 from test_workspace_settings import custom_actor
 custom_actor(app,knowledge[0].split('/')[3],['view_ai_agents'])
 assert client.get(knowledge[1]+'/sources').status_code==200
 assert edit(client,knowledge,a,name='No access').status_code==403
 assert client.post(knowledge[1]+'/urls',json={'name':'Forbidden','url':'https://example.com'},headers=knowledge[2]).status_code==403
 client.cookies.clear();assert client.get(knowledge[1]+'/sources').status_code==401


def test_url_import_failure_refresh_and_disabled_retrieval(client,knowledge,app):
 reader=Reader('website_blocked');source=url(client,knowledge,app,reader);assert source['status']=='failed' and source['error_code']=='website_blocked'
 app.state.agent_knowledge_reader=Reader();response=client.post(knowledge[1]+'/sources/'+source['id']+'/refresh',json={'expected_revision':source['revision']},headers=knowledge[2]);assert response.status_code==200,response.text
 source=detail(client,knowledge,source);assert source['status']=='ready' and 'five business days' in source['content'] and 'SECRET SCRIPT' not in source['content'] and 'PRIVATE NAV' not in source['content']
 assert edit(client,knowledge,source,enabled=False).status_code==200
 with app.state.session_factory() as db:assert retrieve(db,uuid.UUID(knowledge[0].split('/')[-1]),'refund')==[]

@pytest.mark.parametrize('value',['http://example.com','file:///etc/passwd','https://u:p@example.com','https://example.com:444/a','https://example.com/#fragment'])
def test_url_validation_before_queue(client,knowledge,value):
 assert client.post(knowledge[1]+'/urls',json={'name':'Invalid','url':value},headers=knowledge[2]).status_code==422


def test_deleted_import_cannot_resurrect_after_inflight_fetch(client,knowledge,app):
 def remove():
  rows=listing(client,knowledge)['items'];assert delete(client,knowledge,rows).status_code==200
 source=url(client,knowledge,app,Reader(hook=remove));assert source.get('detail')=='Knowledge source not found.' and listing(client,knowledge)['total']==0


def test_permission_revoked_during_import_prevents_publish(client,knowledge,app):
 def revoke():
  with app.state.session_factory() as db:db.scalar(select(Membership)).role='member';db.commit()
 source=url(client,knowledge,app,Reader(hook=revoke));assert source['status']=='failed' and source['error_code']=='access_revoked'


def test_queued_job_survives_restart_and_claimed_job_recovers_after_lease(client,knowledge,app,monkeypatch):
 run=jobs.run_one;monkeypatch.setattr(jobs,'run_one',lambda *a:False);source=url(client,knowledge,app);assert source['status']=='queued'
 assert run(app.state.session_factory,Reader());source=detail(client,knowledge,source);assert source['status']=='ready'
 with app.state.session_factory() as db:
  row=db.get(Source,(uuid.UUID(knowledge[0].split('/')[-1]),uuid.UUID(source['id'])));row.status='processing';row.started_at=utcnow()-timedelta(minutes=3);row.lease_token=uuid.uuid4();db.commit()
 assert run(app.state.session_factory,Reader());assert detail(client,knowledge,source)['status']=='ready'


def test_bounded_retrieval_prefers_relevant_fragments_and_hides_other_agent(client,knowledge,app):
 snippet(client,knowledge,name='Refund',content=('Unrelated policy. '*110)+'\nZanzibar refund investigation deadline is seven days.\n'+('Other info. '*100))
 snippet(client,knowledge,name='Transfers',content='International transfer fees are five dollars.')
 hidden=snippet(client,knowledge,name='Disabled',content='SECRET SECRET ZANZIBAR',enabled=False)
 with app.state.session_factory() as db:
  sources=retrieve(db,uuid.UUID(knowledge[0].split('/')[-1]),'Zanzibar refund investigation deadline');assert 'seven days' in sources[0]['content'];assert all(v['id']!=hidden['id'] for v in sources);assert len(json.dumps(sources).encode())<25000

class ReplyModel:
 available=True
 def __init__(self,text='A grounded answer.'):self.calls=[];self.text=text
 def reply(self,agent,messages,language=None):self.calls.append((agent.persona,messages));return self.text


def test_master_prompt_knowledge_and_normal_reply_integration(client,knowledge,app):
 source=snippet(client,knowledge);model=ReplyModel();app.state.llm_tester=model
 saved=client.post(knowledge[1]+'/guidance',json={'expected_revision':1,'master_prompt':'You are {_chatbot.name}. Language: {language}. Date: {current_date}.','use_for_replies':True},headers=knowledge[2]);assert saved.status_code==200,saved.text
 response=client.post(knowledge[0]+'/tester/messages',json={'content':'What is the refund policy?'},headers=knowledge[2]);assert response.status_code==200,response.text;assert 'five business days' in json.dumps(model.calls[0][1]) and '{_chatbot.name}' not in model.calls[0][0]
 assert client.post(knowledge[1]+'/guidance',json={'expected_revision':1},headers=knowledge[2]).status_code==409
 p=saved.json();assert client.post(knowledge[1]+'/guidance',json={'expected_revision':p['revision'],'use_for_replies':False},headers=knowledge[2]).status_code==200
 client.post(knowledge[0]+'/tester/messages',json={'content':'Refund?'},headers=knowledge[2]);assert 'five business days' not in json.dumps(model.calls[-1][1])

class Screen:
 configured=True;required=True
 def __init__(self,states):self.states=list(states);self.calls=[]
 def check(self,text):self.calls.append(text);return Verdict(self.states.pop(0) if self.states else 'allowed')

@pytest.mark.parametrize('states,expected,calls',[(['blocked'],200,0),(['allowed','blocked'],200,1),(['unavailable'],503,0),(['allowed','unavailable'],503,1)])
def test_input_output_screening_blocks_raw_model_text(client,knowledge,app,states,expected,calls):
 model=ReplyModel('REJECTED PRIVATE MODEL TEXT');app.state.llm_tester=model;app.state.agent_moderator=Screen(states)
 response=client.post(knowledge[0]+'/tester/messages',json={'content':'Customer question'},headers=knowledge[2]);assert response.status_code==expected,response.text;assert len(model.calls)==calls;assert 'REJECTED PRIVATE MODEL TEXT' not in response.text
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.content=='REJECTED PRIVATE MODEL TEXT'))==0

@pytest.mark.parametrize('states,error',[(['blocked'],'content_filtered_input'),(['allowed','blocked'],'content_filtered_output'),(['unavailable'],'moderation_unavailable')])
def test_processing_screening_uses_error_path_and_clears_variable(client,workflow,app,states,error):
 path,document,h=workflow;provider=StubProvider();app.state.llm_processing_client=provider;app.state.agent_moderator=Screen(states);document['nodes'][0]['children']=[llm([message('Success')],[message('Configured fallback')],input='Task text')];assert save(client,path,document,h).status_code==200
 response=event(client,path,h);assert contents(response)==['Configured fallback'];assert any(v.get('error')==error for v in response.json()['trace']);assert len(provider.calls)==(1 if len(states)>1 else 0)

@pytest.mark.parametrize('body,state',[({'results':[{'flagged':True,'categories':{'hate':True}}]},'blocked'),({'results':[{'flagged':False,'categories':{}}]},'allowed'),({'results':[{'flagged':'false'}]},'unavailable'),({'bad':'provider body'},'unavailable')])
def test_moderation_provider_contract_and_secret_screen(app,body,state):
 app.state.settings.moderation_base_url='https://moderator.example/v1';app.state.settings.moderation_model='moderation-model'
 def handle(req):assert req.url.path=='/v1/moderations';assert json.loads(req.content)['model']=='moderation-model';return httpx.Response(200,json=body)
 moderator=Moderator(app.state.settings,httpx.MockTransport(handle));assert moderator.check('Normal customer complaint').state==state
 assert moderator.check('-----BEGIN PRIVATE KEY----- PRIVATE CONTENT').state=='blocked'

@pytest.mark.parametrize('case',['private','mixed','redirect_private','robots','challenge','compressed_limit'])
def test_website_reader_pins_public_dns_and_respects_limits(case):
 calls=[]
 def resolver(host,port):
  if host=='private.example':return ['127.0.0.1']
  if case=='private':return ['169.254.169.254']
  if case=='mixed':return ['93.184.216.34','10.1.2.3']
  return ['93.184.216.34']
 def handle(req):
  calls.append(req);assert 'cookie' not in req.headers and req.headers['host']=='example.com'
  if req.url.path=='/robots.txt':return httpx.Response(200,stream=httpx.ByteStream(b'User-agent: *\nDisallow: /' if case=='robots' else b'User-agent: *\nAllow: /'))
  if case=='redirect_private':return httpx.Response(302,headers={'location':'https://private.example/admin'},stream=httpx.ByteStream(b''))
  if case=='compressed_limit':return httpx.Response(200,headers={'content-type':'text/html','content-encoding':'gzip'},stream=httpx.ByteStream(gzip.compress(b'x'*(10*1024*1024+1))))
  return httpx.Response(200,headers={'content-type':'text/html'},stream=httpx.ByteStream(b'<title>Just a moment...</title><script>cf_chl_opt</script>' if case=='challenge' else b'<p>Allowed text.</p>'))
 reader=WebsiteReader(transport_factory=lambda:httpx.MockTransport(handle),resolver=resolver)
 with pytest.raises(ImportFailure) as error:
  document=reader.fetch('https://example.com/help');extract(document.data,document.filename)
 expected={'private':'blocked_endpoint','mixed':'blocked_endpoint','redirect_private':'blocked_endpoint','robots':'robots_disallowed','challenge':'website_challenge','compressed_limit':'response_too_large'}[case]
 assert error.value.code==expected
 if case in {'private','mixed'}:assert not calls

@pytest.mark.parametrize('redirect',[301,307,308])
def test_public_website_redirects_keep_tls_host_and_extract_reference_text(redirect):
 calls=[]
 def handle(req):
  calls.append(req);assert req.url.host=='93.184.216.34' and req.headers['host']=='example.com'
  if req.url.path=='/robots.txt':return httpx.Response(200,stream=httpx.ByteStream(b'User-agent: *\nAllow: /'))
  if req.url.path=='/help':return httpx.Response(redirect,headers={'location':'/actual'},stream=httpx.ByteStream(b''))
  return httpx.Response(200,headers={'content-type':'text/html'},stream=httpx.ByteStream(b'<h1>Refund facts</h1><p>Five days.</p><script>SECRET_SCRIPT</script>'))
 reader=WebsiteReader(transport_factory=lambda:httpx.MockTransport(handle),resolver=lambda host,port:['93.184.216.34']);document=reader.fetch('https://example.com/help');assert document.final_url=='https://example.com/actual'
 assert 'Five days.' in extract(document.data,document.filename) and 'SECRET_SCRIPT' not in extract(document.data,document.filename);assert len(calls)==4


def test_json_complaint_fields_are_available_to_following_workflow_actions(client,workflow,app):
 from app.workflows.actions.llm_processing.client import ProcessingResult
 path,document,h=workflow;provider=StubProvider(ProcessingResult('success',text='{"financial_institution":"Sample Bank","follow_up_question":"none"}'));app.state.llm_processing_client=provider;document['nodes'].append(intent('complaint',[llm([message('Institution: {complaint_details.financial_institution}')],[message('Invalid JSON')],output_format='json',input='{last_message}')]))
 assert save(client,path,document,h).status_code==200
 conversation=event(client,path,h).json()['conversation_id'];response=event(client,path,h,'message_received',conversation,content='complaint');assert contents(response)==['Institution: Sample Bank'];assert response.json()['variables']['complaint_details']['follow_up_question']=='none';assert provider.calls[-1][1][-1]['content']=='complaint'


def test_invalid_snippet_edit_is_rejected_without_changing_saved_content(client,knowledge):
 source=snippet(client,knowledge);assert edit(client,knowledge,source,content='\x00').status_code==422
 assert detail(client,knowledge,source)['content']==source['content']
