"""Inbox list cost stays bounded as channel and tester conversations accumulate."""
import json,time,uuid
from datetime import timedelta
from sqlalchemy import event,select
from app.models import Agent,AgentConversation,AgentMessage,SupportPerson
from app.core.security import utcnow
from test_inbox_workspace import inbox,queue,repository,desk,workflow,workers,ops


def test_channel_and_tester_queue_queries_are_batched(client,inbox,app):
 f=inbox;workspace=uuid.UUID(f['root'].split('/')[3]);actor=uuid.UUID(f['owner']);space=uuid.UUID(f['space'])
 with app.state.session_factory() as db:
  agent=db.scalar(select(Agent).where(Agent.workspace_id==workspace));stamp=utcnow()
  for i in range(234):
   person=SupportPerson(workspace_id=workspace,teamspace_id=space,identity_key='inbox-load-'+str(i),name='Channel customer '+str(i),email='',phone='')
   db.add(person);db.flush()
   status='active' if i%5==0 else 'pending' if i%7==0 else 'none'
   chat=AgentConversation(workspace_id=workspace,agent_id=agent.id,user_id=actor,person_id=person.id,agent_name=agent.name,
    live_status=status,live_assigned_user_id=actor if status=='active' else None,workflow_state={'channel':['webchat','whatsapp','tester'][i%3]},live_context={'routing':{'team_ids':[],'assignment_mode':'all','user_ids':[],'assign_by_language':False,'language':'en'},'eligible_ids':[str(actor)]})
   db.add(chat);db.flush()
   for role,text in [('assistant','Welcome'),('user','Channel question '+str(i)),('assistant','Reply '+str(i))]:db.add(AgentMessage(conversation_id=chat.id,role=role,content=text,created_at=stamp+timedelta(microseconds=i)))
  db.commit()
 statements=[]
 def capture(connection,cursor,statement,parameters,context,executemany):statements.append(statement)
 event.listen(app.state.engine,'before_cursor_execute',capture);started=time.perf_counter()
 try:result=queue(client,f,limit=40)
 finally:event.remove(app.state.engine,'before_cursor_execute',capture)
 elapsed=time.perf_counter()-started
 print('INBOX_BENCHMARK '+json.dumps({'extra_conversations':234,'returned':len(result['items']),'total':result['total'],'sql_statements':len(statements),'seconds':round(elapsed,3)}))
 assert len(result['items'])==40 and result['total']>=235
 assert len(statements)<150,'Inbox repeats record lookups instead of batching the queue: '+str(len(statements))
 for section in ['mine','livechats','tickets','ai','test']:
  statements.clear();event.listen(app.state.engine,'before_cursor_execute',capture);started=time.perf_counter()
  try:page=queue(client,f,section=section,limit=40)
  finally:event.remove(app.state.engine,'before_cursor_execute',capture)
  print('INBOX_VIEW_BENCHMARK '+json.dumps({'section':section,'total':page['total'],'sql_statements':len(statements),'seconds':round(time.perf_counter()-started,3)}))
  assert len(statements)<150,section


def test_all_inbox_views_keep_channel_and_tester_boundaries(client,inbox):
 f=inbox;all_items=queue(client,f,limit=100)
 for section in ['mine','livechats','tickets','ai','test']:
  result=queue(client,f,section=section,limit=100)
  if section=='mine':assert all(v['mine'] and v['status'] not in {'ai','external','closed','resolved'} for v in result['items'])
  if section=='livechats':assert all(v['kind']=='chats' and v['runtime_status'] in {'pending','active'} and v['channel']!='tester' for v in result['items'])
  if section=='tickets':assert all(v['kind']=='tickets' and v['status'] not in {'closed','resolved'} for v in result['items'])
  if section=='test':assert all(v['channel']=='tester' for v in result['items'])
 assert {v['kind'] for v in all_items['items']}=={'chats','tickets'}


def test_channel_icon_works_in_queue_and_conversation_detail(client,inbox,app):
 import io
 from PIL import Image
 from app.models import WebchatAsset
 f=inbox;buffer=io.BytesIO();Image.new('RGB',(10,10),'orange').save(buffer,'PNG')
 with app.state.session_factory() as db:
  chat=db.get(AgentConversation,uuid.UUID(f['chat']));asset=WebchatAsset(agent_id=chat.agent_id,data=buffer.getvalue());db.add(asset);db.flush()
  chat.workflow_state={**chat.workflow_state,'channel':'whatsapp','whatsapp_name':'Business WhatsApp','whatsapp_icon':str(asset.id)};db.commit()
 item=next(v for v in queue(client,f)['items'] if v['id']==f['chat'])
 assert item['channel']=='whatsapp' and item['channel_name']=='Business WhatsApp'
 assert client.get(item['channel_icon_url']).content==buffer.getvalue()
 detail=client.get(f['root']+'/inbox/conversations/chats/'+f['chat']);assert detail.status_code==200,detail.text
 assert detail.json()['channel_icon_url']==item['channel_icon_url']


def test_queue_uses_sequence_for_equal_clock_wait_time(client,inbox,app):
 f=inbox;stamp=utcnow()
 with app.state.session_factory() as db:
  id=uuid.UUID(f['chat']);db.add(AgentMessage(conversation_id=id,role='assistant',content='Earlier answer',created_at=stamp));db.flush()
  db.add(AgentMessage(conversation_id=id,role='user',content='Waiting now',created_at=stamp));db.commit()
 item=next(v for v in queue(client,f)['items'] if v['id']==f['chat'])
 assert item['preview']=='Waiting now' and item['wait_since'] is not None


def test_queue_does_not_load_full_workflow_or_message_bodies(client,inbox,app):
 f=inbox;secret='LARGE PRIVATE RUNTIME STATE '*10000
 with app.state.session_factory() as db:
  chat=db.get(AgentConversation,uuid.UUID(f['chat']));chat.workflow_state={**chat.workflow_state,'variables':{'large_private':secret}};db.commit()
 statements=[]
 def capture(connection,cursor,statement,parameters,context,executemany):statements.append(statement)
 event.listen(app.state.engine,'before_cursor_execute',capture)
 try:result=queue(client,f)
 finally:event.remove(app.state.engine,'before_cursor_execute',capture)
 assert secret not in json.dumps(result)
 candidate=next(v for v in statements if 'json_build_object' in v)
 assert 'agent_conversations.workflow_state,' not in candidate and 'agent_conversations.live_context,' not in candidate


def test_people_name_permission_and_grant_revocation_are_fresh(client,inbox,app):
 from test_workspace_settings import custom_actor
 from app.models import WorkspaceRole
 f=inbox;workspace=f['root'].split('/')[3]
 uid,role_id=custom_actor(app,workspace,['view_livechats_all','view_people_all'])
 first=queue(client,f);item=next(v for v in first['items'] if v['id']==f['chat'])
 assert item['person_id'] and item['name']!='Chat Customer'
 with app.state.session_factory() as db:db.get(WorkspaceRole,role_id).permissions=['view_livechats_all','view_people_all','view_people_name'];db.commit()
 assert next(v for v in queue(client,f)['items'] if v['id']==f['chat'])['name']=='Chat Customer'
 with app.state.session_factory() as db:db.get(WorkspaceRole,role_id).permissions=[];db.commit()
 assert queue(client,f)['items']==[]


def test_queue_scope_matches_current_record_permissions(client,inbox,app):
 from fastapi import HTTPException
 from sqlalchemy import or_,and_
 from app.models import Membership,WorkspaceRole,SupportTeam
 from app.livechats.service import visibility
 from app.teamspace.records import record
 from app.inbox.queue_reader import QueueReader
 f=inbox;workspace=uuid.UUID(f['root'].split('/')[3]);user=uuid.UUID(f['uid'])
 with app.state.session_factory() as db:
  source=db.get(AgentConversation,uuid.UUID(f['chat']))
  for live in ['none','active','pending','closed']:
   for mine in [True,False]:
    row=AgentConversation(workspace_id=workspace,agent_id=source.agent_id,user_id=user if mine else uuid.UUID(f['owner']),person_id=source.person_id,agent_name='Permission case',
     live_status=live,live_assigned_user_id=user if mine else uuid.UUID(f['owner']),live_context=source.live_context,workflow_state={'channel':'whatsapp'});db.add(row)
  db.commit()
  member=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==user))
  role=WorkspaceRole(workspace_id=workspace,name='Queue contract',name_key='queue-contract',kind='workspace',permissions=[]);db.add(role);db.flush();role_id=role.id;db.commit()
  for grants in [None,['view_livechats_own','manage_livechats_own'],['view_livechats_teams','manage_livechats_teams'],['view_livechats_all','view_ai_agents']]:
   member.custom_role_id=role_id if grants is not None else None;role.permissions=grants or [];db.commit();db.info['actor_id']=user
   candidates=list(db.scalars(select(AgentConversation).outerjoin(Agent,Agent.id==AgentConversation.agent_id).where(AgentConversation.workspace_id==workspace,
    or_(visibility(db,workspace,user),and_(AgentConversation.user_id==user,AgentConversation.live_status=='none')))))
   expected={};
   for row in candidates:
    try:record(db,workspace,'chats',row.id,user)
    except HTTPException:continue
    try:record(db,workspace,'chats',row.id,user,'manage');manage=True
    except HTTPException:manage=False
    expected[row.id]=manage
   reader=QueueReader(db,workspace,user);actual={row.id:reader.scope.chat_allowed(row,'manage') for kind,row in reader.candidates('conversations','',utcnow(),None) if kind=='chats'}
   assert actual==expected,grants


def test_content_search_keeps_unicode_and_literal_wildcards(client,inbox,app):
 f=inbox
 with app.state.session_factory() as db:db.add(AgentMessage(conversation_id=uuid.UUID(f['chat']),role='user',content='Straße offer 100%_savings'));db.commit()
 assert queue(client,f,q='Straße')['total']==1
 assert queue(client,f,q='100%_savings')['total']==1
 assert queue(client,f,q='100_savings')['total']==0


def test_large_active_ticket_queue_uses_batched_queries(client,inbox,app):
 from copy import deepcopy
 from app.models import SupportTicket
 f=inbox
 with app.state.session_factory() as db:
  source=db.get(SupportTicket,uuid.UUID(f['ticket']))
  values={c.name:deepcopy(getattr(source,c.name)) for c in source.__table__.columns if c.name not in {'id','client_request_id','execution_id','number','reference'}}
  for i in range(234):db.add(SupportTicket(**values,id=uuid.uuid4(),client_request_id=uuid.uuid4(),execution_id=uuid.uuid4(),number=10000+i,reference='PTX-LOAD-'+str(i)))
  db.commit()
 statements=[]
 def capture(connection,cursor,statement,parameters,context,executemany):statements.append(statement)
 event.listen(app.state.engine,'before_cursor_execute',capture);started=time.perf_counter()
 try:result=queue(client,f,section='tickets',limit=40)
 finally:event.remove(app.state.engine,'before_cursor_execute',capture)
 print('TICKET_QUEUE_BENCHMARK '+json.dumps({'extra_tickets':234,'total':result['total'],'returned':len(result['items']),'sql_statements':len(statements),'seconds':round(time.perf_counter()-started,3)}))
 assert result['total']==235 and len(result['items'])==40 and all(v['kind']=='tickets' for v in result['items'])
 assert len(statements)<150
