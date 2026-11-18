"""End-to-end settings, policy enforcement, worker jobs, and scoped access."""
import csv,io,json,uuid
from datetime import datetime,timedelta,timezone
import pytest
from pydantic import SecretStr
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import Agent,AgentConversation,AgentMessage,SupportTicket,SupportJob,UsageEvent,WorkspaceBilling,Workspace,Teamspace,PersonBanEvent
from app.teamspace import jobs
from app.teamspace.ai import AIResult
from app.teamspace.worker import overdue,deadline
from test_workflows import workflow,event
from test_workspace_settings import custom_actor
from test_live_agent_handoff import workers,support,available,configure,handoff,queued

@pytest.fixture
def ops(client,workflow):
 path,_,headers=workflow;root=path.rsplit('/agents/',1)[0];space=client.get(root+'/teamspace-operations/options').json()['default_id'];started=event(client,path,headers);assert started.status_code==200,started.text
 return root,space,started.json()['conversation_id'],headers

def setting(client,op,kind,changes):
 root,space,_,headers=op;url=root+'/teamspace-operations/'+space+'/settings/'+kind;prior=client.get(url);assert prior.status_code==200,prior.text;d=prior.json();value={**d['config'],**changes,'expected_revision':d['revision']}
 if kind=='assignment':value={k:value[k] for k in ['expected_revision','section','mode','limit','priority_enabled','priority_type','tag_ids','group_ids','confirm_voluntary'] if k in value}
 if kind=='mandatory':value={**changes,'expected_revision':d['revision']}
 result=client.post(url,json=value,headers=headers);assert result.status_code==200,result.text;return client.get(url).json()

def item(client,op,kind,value):
 root,space,_,headers=op;result=client.post(root+'/teamspace-operations/'+space+'/items/'+kind,json=value,headers=headers);assert result.status_code==201,result.text;return result.json()

def details(client,op,kind='chats',identifier=None):
 root,_,chat,_=op;result=client.get(root+'/support/'+kind+'/'+(identifier or chat)+'/details');assert result.status_code==200,result.text;return result.json()

def enabled(client,op,kind='chats',**extra):return setting(client,op,'analysis',{kind:{'enabled':True,'manual':True,'auto':True,'threshold':0,**extra}})
class AI:
 available=True
 def __init__(self,text=None):self.calls=[];self.text=text or json.dumps({'summary':'Customer needs help.','sentiment':'negative','emotions':['frustration']})
 def generate(self,system,content):self.calls.append((system,content));return AIResult(True,text=self.text)
def process(app,ai=None,mailer=None):return jobs.run_one(app.state.session_factory,app.state.settings,ai or AI(),mailer)
def analyze(client,op,kind='chats',identifier=None,request_id=None):
 root,_,chat,headers=op;return client.post(root+'/support/'+kind+'/'+(identifier or chat)+'/analyze',json={'request_id':request_id or str(uuid.uuid4())},headers=headers)
def ticket(app,op):
 with app.state.session_factory() as db:
  c=db.get(AgentConversation,uuid.UUID(op[2]));row=SupportTicket(workspace_id=c.workspace_id,conversation_id=c.id,person_id=c.person_id,creator_id=c.user_id,action_node_id=uuid.uuid4(),execution_id=uuid.uuid4(),number=1,reference='PTX-000001',agent_name='Support',subject='Account help',full_name='Customer',email='customer@example.com');db.add(row);db.commit();return str(row.id)
def tags(client,op):
 root,_,_,headers=op
 def create(kind,body):
  response=client.post(root+'/ticket-settings/'+kind,json=body,headers=headers);assert response.status_code==201,response.text;return response.json()
 g1=create('groups',{'name':'Services'});g2=create('groups',{'name':'Priority'});direct=create('tags',{'name':'Direct','color':'#336699','group_ids':[]});grouped=create('tags',{'name':'Grouped','color':'#996633','group_ids':[g1['id'],g2['id']]});return direct,grouped,g1,g2
def labels(client,op,kind,identifier,tag_ids=None,group_ids=None,mode='replace'):
 result=client.post(op[0]+'/support/'+kind+'/'+identifier+'/labels',json={'tag_ids':tag_ids or [],'group_ids':group_ids or [],'mode':mode},headers=op[3]);assert result.status_code==200,result.text;return result.json()
def export(client,op,kind='chats',ids=None,fields=None,**extra):
 body={'request_id':str(uuid.uuid4()),'kind':kind,'ids':ids or [op[2]],'fields':fields or ['id','tags'],'transcript':False,'timezone':'Africa/Nairobi',**extra};return client.post(op[0]+'/support/exports',json=body,headers=op[3]),body

@pytest.mark.parametrize('kind',['analysis','copilot','assignment','enforcement','mandatory','overdue','analytics','profile_iframe'])
def test_independent_setting_permissions(client,app,ops,kind):
 root,space,_,headers=ops;url=root+'/teamspace-operations/'+space+'/settings/'+kind;assert client.get(url).status_code==200
 custom_actor(app,root.split('/')[3],[]);assert client.get(url).status_code==403;assert client.post(url,json={'expected_revision':1},headers=headers).status_code==403

def test_revision_csrf_and_copilot_dependencies(client,ops):
 root,space,_,headers=ops;url=root+'/teamspace-operations/'+space+'/settings/copilot';d=client.get(url).json();body={'expected_revision':d['revision'],'all_conversations':True};result=client.post(url,json=body,headers=headers);assert result.status_code==200,result.text
 assert client.get(url).json()['config']['person_history'];assert client.post(url,json=body,headers=headers).status_code==409
 assert client.post(url,json={**body,'expected_revision':result.json()['revision']}).status_code==403
 assert client.post(url,json={**body,'expected_revision':result.json()['revision'],'current_conversation':False},headers=headers).status_code==422

@pytest.mark.parametrize('unit',['minutes','hours','days','weeks','months','permanent'])
def test_bans_block_existing_and_new_ai_conversations(client,app,ops,workflow,unit):
 root,_,chat,headers=ops;item(client,ops,'ban-rules',{'offences':0,'duration':2,'unit':unit});reason=item(client,ops,'ban-reasons',{'name':'Abuse'});d=details(client,ops);url=root+'/support/chats/'+chat
 result=client.post(url+'/ban',json={'reason_id':reason['id'],'expected_revision':d['person']['revision']},headers=headers);assert result.status_code==200,result.text
 person=details(client,ops)['person'];assert person['banned'] and person['offences']==1 and person['ban_permanent']==(unit=='permanent')
 assert event(client,workflow[0],headers,'message_received',chat,content='Help').status_code==403;assert event(client,workflow[0],headers).status_code==403
 assert client.post(url+'/unban',json={'expected_revision':person['revision']},headers=headers).status_code==200
 assert event(client,workflow[0],headers,'message_received',chat,content='Help').status_code==200
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(PersonBanEvent))==2

@pytest.mark.parametrize('kind,value',[('ban-rules',{'offences':0,'duration':10,'unit':'minutes'}),('ban-reasons',{'name':'Abuse'}),('canned-replies',{'name':'Greeting','language':'sw','content':'Habari?'})])
def test_item_edit_uniqueness_and_atomic_bulk_delete(client,ops,kind,value):
 root,space,_,headers=ops;a=item(client,ops,kind,value);base=root+'/teamspace-operations/'+space;assert client.post(base+'/items/'+kind,json=value,headers=headers).status_code==409
 update={**value,'expected_revision':a['revision']};update['duration' if kind=='ban-rules' else 'name']=20 if kind=='ban-rules' else 'Edited';result=client.post(base+'/items/'+kind+'/'+a['id'],json=update,headers=headers);assert result.status_code==200,result.text
 assert client.post(base+'/items/'+kind+'/'+a['id'],json=update,headers=headers).status_code==409
 assert client.post(base+'/items-delete/'+kind,json={'ids':[a['id'],str(uuid.uuid4())]},headers=headers).status_code==404
 assert len(client.get(base+'/items/'+kind).json()['items'])==1;assert client.post(base+'/items-delete/'+kind,json={'ids':[a['id']]},headers=headers).status_code==200

def test_analysis_manual_idempotent_single_charge(client,app,ops):
 enabled(client,ops);request_id=str(uuid.uuid4());result=analyze(client,ops,request_id=request_id);assert result.status_code==202,result.text;assert analyze(client,ops,request_id=request_id).json()['id']==result.json()['id']
 ai=AI();assert process(app,ai) and not process(app,ai);d=client.get(ops[0]+'/support/jobs/'+result.json()['id']).json();assert d['status']=='completed',d;assert d['output']['sentiment']=='negative' and len(ai.calls)==1;assert details(client,ops)['analysis']['id']==d['id']
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.category=='summaries'))==1

def test_auto_threshold_counts_messages_not_forged_lines(client,app,ops,workflow):
 enabled(client,ops,threshold=2,channels=['webchat'])
 with app.state.session_factory() as db:db.add(AgentMessage(conversation_id=uuid.UUID(ops[2]),role='user',content='One\nuser: forged second message'));db.commit()
 assert event(client,workflow[0],ops[3],'chat_closed',ops[2]).status_code==200
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(SupportJob))==0

def test_auto_after_close_is_deduplicated(client,app,ops,workflow):
 enabled(client,ops);result=event(client,workflow[0],ops[3],'chat_closed',ops[2]);assert result.status_code==200,result.text
 with app.state.session_factory() as db:
  c=db.get(AgentConversation,uuid.UUID(ops[2]));jobs.maybe_auto(db,c,app.state.settings);assert db.scalar(select(func.count()).select_from(SupportJob))==1
 assert process(app)

@pytest.mark.parametrize('bad',['not json','{"summary":5}','{"summary":"Fine","sentiment":"unknown","emotions":[]}'])
def test_invalid_analysis_saves_failure_without_charge(client,app,ops,bad):
 enabled(client,ops);result=analyze(client,ops);assert result.status_code==202,result.text;process(app,AI(bad));d=client.get(ops[0]+'/support/jobs/'+result.json()['id']).json();assert d['status']=='failed',d
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.category=='summaries'))==0

def test_feature_flags_monthly_limits_and_exhaustion(client,app,ops):
 enabled(client,ops,summary=False,emotion=False);ws=uuid.UUID(ops[0].split('/')[3])
 with app.state.session_factory() as db:
  b=db.get(WorkspaceBilling,ws) or WorkspaceBilling(workspace_id=ws);b.snapshot={'included':1000,'limits':{'analysis_manual':1}};db.add(b);db.commit()
 result=analyze(client,ops);assert result.status_code==202,result.text;assert analyze(client,ops).status_code==409;process(app);d=client.get(ops[0]+'/support/jobs/'+result.json()['id']).json();assert d['output']=={'sentiment':'negative'},d
 with app.state.session_factory() as db:b=db.get(WorkspaceBilling,ws);b.snapshot={'included':0};db.commit()
 assert analyze(client,ops).status_code==409

def test_worker_rechecks_revoked_permission(client,app,ops):
 enabled(client,ops);result=analyze(client,ops);assert result.status_code==202;custom_actor(app,ops[0].split('/')[3],['view_livechats_all','view_ai_analysis']);ai=AI();process(app,ai);assert not ai.calls
 with app.state.session_factory() as db:assert db.get(SupportJob,uuid.UUID(result.json()['id'])).status=='failed'

def test_people_analysis_excludes_other_teamspace_history(client,app,ops):
 enabled(client,ops,kind='people');person=details(client,ops)['person']['id']
 with app.state.session_factory() as db:
  c=db.get(AgentConversation,uuid.UUID(ops[2]));private=Teamspace(workspace_id=c.workspace_id,name='Private',name_key='private',is_default=False);db.add(private);db.flush();a=Agent(workspace_id=c.workspace_id,name='Secret Agent',teamspace_id=private.id);db.add(a);db.flush();other=AgentConversation(workspace_id=c.workspace_id,agent_id=a.id,user_id=c.user_id,person_id=c.person_id,agent_name='Secret');db.add(other);db.flush();db.add(AgentMessage(conversation_id=other.id,role='user',content='PRIVATE TEAMSPACE SECRET'));db.commit()
 response=analyze(client,ops,'people',person);assert response.status_code==202,response.text;ai=AI();process(app,ai);assert ai.calls and 'PRIVATE TEAMSPACE SECRET' not in ai.calls[0][1]

def test_people_fields_typed_and_type_category_immutable(client,app,ops):
 root,_,_,headers=ops;person=details(client,ops)['person']['id'];body={'scope':'person','type':'number','name':'Customer score','options':[],'default_value':0};result=client.post(root+'/custom-fields',json=body,headers=headers);assert result.status_code==201,result.text;f=result.json()
 assert client.get(root+'/custom-fields?scope=person').json()['fields'][0]['id']==f['id'];assert client.get(root+'/custom-fields').json()['fields']==[]
 assert client.post(root+'/custom-fields/'+f['id'],json={**body,'type':'text','default_value':None,'expected_revision':1},headers=headers).status_code==409
 d=details(client,ops,'people',person);url=root+'/support/people/'+person+'/fields';assert client.post(url,json={'values':{f['key']:'wrong'},'expected_revision':d['revision']},headers=headers).status_code==422
 assert client.post(url,json={'values':{f['key']:12},'expected_revision':d['revision']},headers=headers).status_code==200;assert details(client,ops,'people',person)['values'][f['key']]['value']==12;assert details(client,ops)['fields']==[]

def test_many_group_tags_bulk_preserve_remove_and_direct_only_export(client,app,ops):
 root,_,chat,headers=ops;direct,grouped,g1,g2=tags(client,ops);assert set(grouped['group_ids'])=={g1['id'],g2['id']};d=labels(client,ops,'chats',chat,group_ids=[g2['id']]);assert d['effective_tags'][0]['name']=='Grouped' and not d['effective_tags'][0]['direct']
 response=client.post(root+'/support/bulk-labels',json={'kind':'chats','ids':[chat],'tag_ids':[direct['id']],'mode':'add'},headers=headers);assert response.status_code==200,response.text;d=details(client,ops)['labels'];assert {t['name'] for t in d['effective_tags']}=={'Grouped','Direct'} and d['group_ids']==[g2['id']]
 assert [t['name'] for t in labels(client,ops,'chats',chat,tag_ids=[direct['id']])['effective_tags']]==['Direct']
 labels(client,ops,'chats',chat,tag_ids=[direct['id']],group_ids=[g1['id']]);response,_=export(client,ops);assert response.status_code==202,response.text;process(app);data=client.get(root+'/support/exports/'+response.json()['id']+'/download');assert data.status_code==200,data.text;assert list(csv.DictReader(io.StringIO(data.content.decode('utf-8-sig'))))[0]['tags']=='Direct'

def test_mandatory_tags_and_fields_block_ticket_close(client,app,ops):
 root,_,_,headers=ops;identifier=ticket(app,ops);setting(client,ops,'enforcement',{'enabled':True,'minimum':1,'sort':'a_z'});body={'status':'closed','assignee_type':'none','assignee_id':None,'expected_revision':1};assert client.post(root+'/tickets/'+identifier,json=body,headers=headers).status_code==422
 direct,_,_,_=tags(client,ops);labels(client,ops,'tickets',identifier,[direct['id']]);body['expected_revision']=details(client,ops,'tickets',identifier)['revision'];f=client.post(root+'/custom-fields',json={'scope':'conversation','type':'text','name':'Resolution code','options':[]},headers=headers).json();setting(client,ops,'mandatory',{'enabled':True,'field_ids':[f['id']]});assert client.post(root+'/tickets/'+identifier,json=body,headers=headers).status_code==422
 assert client.post(root+'/support/tickets/'+identifier+'/fields',json={'expected_revision':body['expected_revision'],'values':{f['key']:'Solved'}},headers=headers).status_code==200
 body['expected_revision']=details(client,ops,'tickets',identifier)['revision'];assert client.post(root+'/tickets/'+identifier,json=body,headers=headers).status_code==200

def test_export_idempotency_timezone_transcript_email_and_history(client,app,ops):
 root,_,chat,headers=ops
 with app.state.session_factory() as db:db.add(AgentMessage(conversation_id=uuid.UUID(chat),role='user',content='Customer message'));db.commit()
 response,body=export(client,ops,fields=['id','created_at'],transcript=True,email=True);assert response.status_code==202,response.text;assert client.post(root+'/support/exports',json=body,headers=headers).json()['id']==response.json()['id']
 class Mail:
  def __init__(self):self.calls=[]
  def send_export(self,*args):self.calls.append(args)
 mail=Mail();assert process(app,mailer=mail) and not process(app,mailer=mail);assert len(mail.calls)==1;d=client.get(root+'/support/jobs/'+response.json()['id']).json();assert d['output']['email_status']=='sent',d
 data=client.get(root+'/support/exports/'+d['id']+'/download');row=list(csv.DictReader(io.StringIO(data.content.decode('utf-8-sig'))))[0];assert '+03:00' in row['created_at'] and 'Customer message' in row['transcript'];assert client.get(root+'/support/exports?page_size=10').json()['total']==1

def test_people_export_and_download_enforce_field_redaction(client,app,ops):
 root,_,_,_=ops;person=details(client,ops)['person']['id'];response,_=export(client,ops,'people',[person],['id','email','custom_fields','tags']);assert response.status_code==202,response.text;process(app)
 custom_actor(app,root.split('/')[3],['view_people_all','view_people_name','export_people','download_people_all']);d=details(client,ops,'people',person);assert 'email' not in d['person'] and d['labels'] is None and d['fields']==[];assert client.get(root+'/support/exports/'+response.json()['id']+'/download').status_code==403;assert export(client,ops,'people',[person],['email'])[0].status_code==403;assert client.get(root+'/support/people?q=chris@gmail.com').json()['people']==[]

def test_custom_person_export_and_csv_formula_sanitization(client,app,ops):
 root,_,_,headers=ops;person=details(client,ops)['person'];f=client.post(root+'/custom-fields',json={'scope':'person','type':'text','name':'Note','options':[]},headers=headers).json();result=client.post(root+'/support/people/'+person['id'],json={'name':'=HYPERLINK("https://bad")','expected_revision':person['revision']},headers=headers);assert result.status_code==200,result.text
 result=client.post(root+'/support/people/'+person['id']+'/fields',json={'values':{f['key']:'@SUM(1)'},'expected_revision':result.json()['revision']},headers=headers);assert result.status_code==200,result.text
 response,_=export(client,ops,'people',[person['id']],['name','custom:'+f['key']]);assert response.status_code==202,response.text;process(app);data=client.get(root+'/support/exports/'+response.json()['id']+'/download');rows=list(csv.reader(io.StringIO(data.content.decode('utf-8-sig'))));assert rows[1][0].startswith("'=") and rows[1][1].startswith("'@"),rows

def test_copilot_knowledge_produces_reviewable_draft_without_sending(client,app,ops):
 root,space,chat,headers=ops;result=client.post(root+'/teamspace-operations/'+space+'/knowledge',data={'name':'Policy','kind':'snippet','content':'Refunds take three business days.'},headers=headers);assert result.status_code==201,result.text
 result=client.post(root+'/support/chats/'+chat+'/copilot',json={'request_id':str(uuid.uuid4())},headers=headers);assert result.status_code==202,result.text;ai=AI('I can check your refund.');process(app,ai);assert 'Refunds take three business days.' in ai.calls[0][1];d=client.get(root+'/support/jobs/'+result.json()['id']).json();assert d['output']['draft']=='I can check your refund.'
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.content==d['output']['draft']))==0

@pytest.mark.parametrize('url',['javascript:alert(1)','data:text/html,bad','https://user:secret@example.com','//example.com','not a URL'])
def test_embed_url_security(client,ops,url):
 root,space,_,headers=ops;path=root+'/teamspace-operations/'+space+'/settings/analytics';d=client.get(path).json();assert client.post(path,json={'expected_revision':d['revision'],'enabled':True,'url':url},headers=headers).status_code==422

def test_embed_persistence_and_enterprise_gate(client,app,ops):
 setting(client,ops,'analytics',{'enabled':True,'url':'https://example.com/dashboard'});assert client.get(ops[0]+'/support/embeds/analytics').json()['url']=='https://example.com/dashboard';app.state.settings.stripe_secret_key=SecretStr('sk_test_no_network')
 with app.state.session_factory() as db:db.get(Workspace,uuid.UUID(ops[0].split('/')[3])).plan_name='Lite';db.commit()
 assert client.get(ops[0]+'/support/embeds/analytics').status_code==403

def test_overdue_inactivity_weekends_and_reassignment(client,app,ops):
 root,_,_,headers=ops;identifier=ticket(app,ops);owner=client.get('/api/auth/me').json()['user']['id'];team=client.post(root+'/teams',json={'name':'Escalation','member_ids':[owner]},headers=headers);assert team.status_code==201,team.text;setting(client,ops,'overdue',{'enabled':True,'days':1,'exclude_weekends':True,'user_id':owner,'team_ids':[team.json()['id']]})
 with app.state.session_factory() as db:
  row=db.get(SupportTicket,uuid.UUID(identifier));row.updated_at=utcnow()-timedelta(days=10);db.commit();overdue(db,app.state.settings);overdue(db,app.state.settings);row=db.get(SupportTicket,row.id);assert row.status=='overdue' and row.revision==2 and row.assignee_id==uuid.UUID(owner) and row.audience_team_ids==[team.json()['id']]
 friday=datetime(2026,11,13,9,tzinfo=timezone.utc);assert deadline(friday,1,True).weekday()==0 and deadline(friday,1,False).weekday()==5;assert client.get(root+'/tickets?status=overdue').json()['tickets'][0]['id']==identifier

@pytest.mark.parametrize('mode,rank',[('balanced',0),('random',1),('round_robin',2),('balanced_round_robin',2),('manual',3)])
def test_assignment_plan_gates_and_independent_saves(client,app,ops,mode,rank):
 root,space,_,headers=ops;app.state.settings.stripe_secret_key=SecretStr('sk_test_no_network')
 with app.state.session_factory() as db:db.get(Workspace,uuid.UUID(root.split('/')[3])).plan_name=['Starter','Lite','Pro','Total'][max(0,rank-1)];db.commit()
 path=root+'/teamspace-operations/'+space+'/settings/assignment';d=client.get(path).json();body={'section':'method','mode':mode,'expected_revision':d['revision']};result=client.post(path,json=body,headers=headers);assert result.status_code==(403 if rank else 200),result.text
 if rank:
  with app.state.session_factory() as db:db.get(Workspace,uuid.UUID(root.split('/')[3])).plan_name=['Starter','Lite','Pro','Total'][rank];db.commit()
  assert client.post(path,json=body,headers=headers).status_code==200
 d=setting(client,ops,'assignment',{'section':'limit','limit':7});assert d['config']['mode']==mode and d['config']['limit']==7

def test_capacity_queue_drain_and_voluntary_claim(client,app,workflow,workers):
 path,document,headers=workflow;worker,staff,user_id=workers();root,_=support(client,path,headers,[user_id]);available(worker,root,staff);space=client.get(root+'/teamspace-operations/options').json()['default_id'];op=(root,space,'',headers);setting(client,op,'assignment',{'section':'method','mode':'balanced'});setting(client,op,'assignment',{'section':'limit','limit':1});configure(client,path,document,headers,[handoff()]);first=queued(client,path,headers);second=queued(client,path,headers);assert first['live_chat']['status']=='active' and second['live_chat']['status']=='pending'
 result=worker.post(root+'/inbox/chats/'+first['conversation_id']+'/close',json={'expected_revision':first['live_chat']['revision']},headers=staff);assert result.status_code==200,result.text;assert worker.get(root+'/inbox/chats/'+second['conversation_id']).json()['status']=='active'
 setting(client,op,'assignment',{'section':'method','mode':'manual'});third=queued(client,path,headers);assert third['live_chat']['status']=='pending';result=worker.post(root+'/inbox/chats/'+third['conversation_id']+'/claim',json={'expected_revision':third['live_chat']['revision']},headers=staff);assert result.status_code==200,result.text

def test_priority_freezes_at_handoff_and_disables_on_delete(client,app,workflow,workers):
 path,document,headers=workflow;worker,staff,user_id=workers();root,_=support(client,path,headers,[user_id]);available(worker,root,staff);space=client.get(root+'/teamspace-operations/options').json()['default_id'];started=event(client,path,headers).json();op=(root,space,started['conversation_id'],headers);direct,_,_,_=tags(client,op);person=details(client,op)['person']['id'];labels(client,op,'people',person,[direct['id']]);setting(client,op,'assignment',{'section':'method','mode':'manual'});setting(client,op,'assignment',{'section':'priority','priority_enabled':True,'priority_type':'skip_queue','tag_ids':[direct['id']]});configure(client,path,document,headers,[handoff()]);first=queued(client,path,headers);assert worker.get(root+'/inbox/chats/'+first['conversation_id']).json()['priority'];labels(client,op,'people',person,[]);assert worker.get(root+'/inbox/chats/'+first['conversation_id']).json()['priority']
 result=client.post(root+'/ticket-settings/tags/'+direct['id']+'/delete',json={'expected_revision':direct['revision']},headers=headers);assert result.status_code==204,result.text;assert client.get(root+'/teamspace-operations/'+space+'/settings/assignment').json()['config']['priority_enabled'] is False
