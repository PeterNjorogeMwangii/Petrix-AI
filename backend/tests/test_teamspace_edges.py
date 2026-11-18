"""Additional policy boundaries, safe failures and algorithm behavior."""
import io,uuid
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select,func
from app.models import SupportJob,WorkspaceBilling,AgentConversation,SupportPerson,TeamspaceOperations
from app.core.security import utcnow
from app.teamspace.assignment import pick
from app.teamspace.ai import SupportAI,clean_analysis
from app.teamspace.routes import knowledge_text
from app.teamspace.schemas import RuleBody,PersonBody,EmbedConfig
from app.teamspace import configuration
from test_teamspace_operations import ops,setting,item,enabled,analyze,details,AI,process,tags,labels,export
from test_workflows import workflow,event
from test_live_agent_handoff import workers,support,available,configure,handoff,queued

@pytest.mark.parametrize('mode',['balanced','random','round_robin','balanced_round_robin','least_busy','manual'])
def test_assignment_pick_online_capacity_and_cursor(mode):
 class DB:
  def get(self,*args):return SimpleNamespace(online_since=utcnow(),last_seen_at=utcnow(),last_assigned_at=None)
 people=[{'id':str(uuid.uuid4()),'available':True,'active_chats':n} for n in [0,1,5]];people.append({'id':str(uuid.uuid4()),'available':False,'active_chats':0})
 row=SimpleNamespace(assignment={'mode':mode,'limit':2,'priority_type':'ignore_limit','cursor':None});chosen=pick(DB(),uuid.uuid4(),row,people)
 if mode=='manual':assert chosen is None;return
 assert chosen in [p['id'] for p in people[:2]]
 if mode in ['balanced','balanced_round_robin','least_busy']:assert chosen==people[0]['id']
 assert row.assignment['cursor']==chosen
 assert pick(DB(),uuid.uuid4(),row,[people[2]]) is None
 assert pick(DB(),uuid.uuid4(),row,[people[2]],priority=True)==people[2]['id']

@pytest.mark.parametrize('type',['summary','sentiment','emotion'])
def test_only_enabled_analysis_outputs_are_saved(type):
 data=clean_analysis('{"summary":"Help needed","sentiment":"neutral","emotions":["concern"]}',{type:True});assert list(data)==[{'emotion':'emotions'}.get(type,type)]

@pytest.mark.parametrize('unit',['hours','days','weeks','months'])
def test_extreme_ban_duration_is_rejected(unit):
 with pytest.raises(ValidationError):RuleBody(offences=0,duration=525600,unit=unit)

@pytest.mark.parametrize('name,content',[('bad.pdf',b'%PDF'),('bad.docx',b'broken'),('bad.txt',b'\x00'),('bad.txt',b'\xff'),('bad.txt',b''),('bad.txt',b'x'*(5*1024*1024+1))])
def test_knowledge_rejects_invalid_or_oversized_files(name,content):
 with pytest.raises(HTTPException):knowledge_text(name,content)

def test_html_knowledge_strips_script_and_style():assert knowledge_text('source.html',b'<script>private()</script><style>hide</style><p>Public policy.</p>')=='Public policy.'
def test_support_ai_unconfigured_returns_explicit_failure():
 ai=SupportAI(SimpleNamespace(support_ai_base_url='',support_ai_model=''));assert not ai.available;result=ai.generate('system','content');assert not result.ok and 'not configured' in result.error

def test_priority_ignore_capacity_and_voluntary_confirmation(client,app,workflow,workers):
 path,document,headers=workflow;worker,staff,user_id=workers();root,_=support(client,path,headers,[user_id]);available(worker,root,staff);space=client.get(root+'/teamspace-operations/options').json()['default_id'];started=event(client,path,headers).json();op=(root,space,started['conversation_id'],headers);direct,_,_,_=tags(client,op);person=details(client,op)['person']['id'];labels(client,op,'people',person,[direct['id']]);setting(client,op,'assignment',{'section':'method','mode':'balanced'});setting(client,op,'assignment',{'section':'limit','limit':1});setting(client,op,'assignment',{'section':'priority','priority_enabled':True,'priority_type':'ignore_limit','tag_ids':[direct['id']]});configure(client,path,document,headers,[handoff()]);a=queued(client,path,headers);b=queued(client,path,headers);assert a['live_chat']['status']==b['live_chat']['status']=='active'
 url=root+'/teamspace-operations/'+space+'/settings/assignment';d=client.get(url).json();body={'expected_revision':d['revision'],'section':'method','mode':'manual'};assert client.post(url,json=body,headers=headers).status_code==409;body['confirm_voluntary']=True;assert client.post(url,json=body,headers=headers).status_code==200;assert client.get(url).json()['config']['priority_type']=='skip_queue'

def test_worker_budget_and_disabled_configuration_are_rechecked(client,app,ops):
 enabled(client,ops);result=analyze(client,ops);assert result.status_code==202
 with app.state.session_factory() as db:
  b=db.get(WorkspaceBilling,uuid.UUID(ops[0].split('/')[3]));b.snapshot={'included':0};db.commit()
 ai=AI();process(app,ai);assert not ai.calls
 with app.state.session_factory() as db:assert db.get(SupportJob,uuid.UUID(result.json()['id'])).status=='failed'

def test_orphaned_running_lease_can_resume_once(client,app,ops):
 enabled(client,ops);result=analyze(client,ops);assert result.status_code==202
 with app.state.session_factory() as db:
  job=db.get(SupportJob,uuid.UUID(result.json()['id']));job.status='running';job.lease_until=utcnow()-timedelta(minutes=1);job.lease_id=uuid.uuid4();db.commit()
 ai=AI();assert process(app,ai);assert not process(app,ai);assert len(ai.calls)==1

def test_ban_can_be_overridden_and_old_revision_fails(client,ops):
 root,_,chat,headers=ops;reason=item(client,ops,'ban-reasons',{'name':'Abuse'});item(client,ops,'ban-rules',{'offences':0,'duration':1,'unit':'hours'});item(client,ops,'ban-rules',{'offences':1,'unit':'permanent'});url=root+'/support/chats/'+chat+'/ban';person=details(client,ops)['person'];body={'reason_id':reason['id'],'expected_revision':person['revision']};assert client.post(url,json=body,headers=headers).status_code==200;assert client.post(url,json=body,headers=headers).status_code==409;body['expected_revision']=details(client,ops)['person']['revision'];assert client.post(url,json=body,headers=headers).status_code==200;assert details(client,ops)['person']['ban_permanent']

def test_people_unique_id_validation_and_foreign_export_atomic(client,ops):
 root,_,chat,headers=ops
 assert client.post(root+'/support/people',json={'name':'New','email':'invalid'},headers=headers).status_code==422
 a=client.post(root+'/support/people',json={'name':'New','external_id':'customer-1'},headers=headers);assert a.status_code==201,a.text
 assert client.post(root+'/support/people',json={'name':'Duplicate','external_id':'customer-1'},headers=headers).status_code==409
 assert export(client,ops,ids=[chat,str(uuid.uuid4())])[0].status_code==404

def test_people_filters_and_canned_limit(client,app,ops):
 root,space,_,headers=ops;person=details(client,ops)['person']['id'];direct,_,_,group=tags(client,ops);labels(client,ops,'people',person,group_ids=[group['id']]);assert client.get(root+'/support/people?group='+group['id']).json()['people'][0]['id']==person;assert client.get(root+'/support/people?tag='+direct['id']).json()['people']==[]
 with app.state.session_factory() as db:
  b=db.get(WorkspaceBilling,uuid.UUID(root.split('/')[3])) or WorkspaceBilling(workspace_id=uuid.UUID(root.split('/')[3]));b.snapshot={'limits':{'canned_replies':1}};db.add(b);db.commit()
 item(client,ops,'canned-replies',{'name':'First','content':'Hello'})
 assert client.post(root+'/teamspace-operations/'+space+'/items/canned-replies',json={'name':'Second','content':'Hello again'},headers=headers).status_code==409

def test_bulk_taxonomy_delete_is_atomic_and_keeps_saved_history(client,ops):
 root,_,_,headers=ops;direct,grouped,g1,g2=tags(client,ops)
 assert client.post(root+'/support/taxonomy-delete/tags',json={'ids':[direct['id'],str(uuid.uuid4())]},headers=headers).status_code==404
 assert len(client.get(root+'/ticket-settings/tags').json()['items'])==2
 assert client.post(root+'/support/taxonomy-delete/tags',json={'ids':[direct['id'],grouped['id']]},headers=headers).status_code==200
 assert client.get(root+'/ticket-settings/tags').json()['items']==[]

def test_workflow_close_action_respects_mandatory_tags(client,app,ops,workflow):
 from test_workflows import intent,node,save
 path,document,headers=workflow;document['nodes'].append(intent('finish',[node('action','close_chat')]))
 assert save(client,path,document,headers).status_code==200
 setting(client,ops,'enforcement',{'enabled':True,'minimum':1,'sort':'a_z'})
 assert event(client,path,headers,'message_received',ops[2],content='finish').status_code==422
 with app.state.session_factory() as db:assert not db.get(AgentConversation,uuid.UUID(ops[2])).workflow_state.get('closed')
 direct,_,_,_=tags(client,ops);labels(client,ops,'chats',ops[2],[direct['id']])
 response=event(client,path,headers,'message_received',ops[2],content='finish');assert response.status_code==200,response.text;assert response.json()['closed']
