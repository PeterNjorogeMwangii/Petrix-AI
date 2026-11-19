"""Report arithmetic, permissions, shared filters and measured timelines."""
import uuid,json
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,delete,func
import pytest
from app.models import (AgentConversation,AgentMessage,SupportTicket,TicketEmail,TicketInboundEmail,
    AnalyticsRecordEvent,AnalyticsActivity,WorkspaceSettings,LivechatNote,SupportPerson,Teamspace,Agent)
from app.analytics.activity import merge
from app.core.security import utcnow
from test_inbox_workspace import inbox
from test_livechats import repository
from test_live_agent_handoff import workers
from test_workflows import workflow
from test_ticket_workspace import desk
from test_teamspace_operations import tags,labels
from test_workspace_settings import custom_actor

@pytest.fixture
def analytics(client,inbox,app):
 f=inbox;at=datetime(2026,11,10,tzinfo=timezone.utc);ws=uuid.UUID(f['root'].split('/')[3]);chat_id=uuid.UUID(f['chat']);uid=uuid.UUID(f['uid']);owner=uuid.UUID(f['owner'])
 with app.state.session_factory() as db:
  policy=db.get(WorkspaceSettings,ws) or WorkspaceSettings(workspace_id=ws);db.add(policy);policy.analytics_started_at=at-timedelta(days=10)
  chat=db.get(AgentConversation,chat_id);chat.created_at=at;chat.live_status='closed';chat.live_assigned_user_id=uid;chat.live_display_status='';chat.live_context={**chat.live_context,'handoff_at':(at+timedelta(seconds=10)).isoformat()};chat.workflow_state={'channel':'webchat','closed':True,'closed_at':(at+timedelta(seconds=120)).isoformat(),'csat_rating':5,'feedback_submitted':True,'feedback_submitted_at':(at+timedelta(seconds=200)).isoformat()}
  db.execute(delete(AgentMessage).where(AgentMessage.conversation_id==chat.id))
  for seconds,role,source,author in [(1,'user','person',None),(3,'assistant','send_message',None),(12,'user','person',None),(22,'assistant','live_agent',uid),(42,'user','person',None),(62,'assistant','live_agent',owner)]:db.add(AgentMessage(conversation_id=chat.id,role=role,content='Customer-visible text',author_id=author,created_at=at+timedelta(seconds=seconds),message_data={'source':source}))
  db.add(LivechatNote(conversation_id=chat.id,author_id=owner,author_name='Staff',content='Private staff note',color='#ffddaa'))
  base={'workspace_id':ws,'agent_id':chat.agent_id,'user_id':owner,'agent_name':'Test agent','person_id':chat.person_id}
  ai=AgentConversation(**base,created_at=at+timedelta(minutes=10),live_status='none',workflow_state={'channel':'webchat','closed':True,'closed_at':(at+timedelta(minutes=12)).isoformat(),'csat_rating':4,'feedback_submitted':True,'feedback_submitted_at':(at+timedelta(days=2)).isoformat()});db.add(ai);db.flush()
  db.add_all([AgentMessage(conversation_id=ai.id,role='user',content='Question',created_at=ai.created_at+timedelta(seconds=1)),AgentMessage(conversation_id=ai.id,role='assistant',content='Answer',created_at=ai.created_at+timedelta(seconds=2))])
  unrated=AgentConversation(**base,created_at=at+timedelta(minutes=20),live_status='none',workflow_state={'channel':'webchat','closed':True,'closed_at':(at+timedelta(minutes=21)).isoformat()});db.add(unrated);db.flush();db.add(AgentMessage(conversation_id=unrated.id,role='assistant',content='Greeting',created_at=unrated.created_at))
  for routed in [True,False]:db.add(AgentConversation(**base,created_at=at+timedelta(minutes=30),live_status='pending',workflow_state={'channel':'webchat'},live_context={'routing':{'team_ids':[f['team']['id']] if routed else []}}))
  db.add(AgentConversation(**base,created_at=at-timedelta(days=1),live_status='none',workflow_state={'channel':'webchat'}))
  ticket=db.get(SupportTicket,uuid.UUID(f['ticket']));ticket.created_at=at;ticket.status='resolved';ticket.origin='manual'
  values={c.name:getattr(ticket,c.name) for c in SupportTicket.__table__.columns if c.name not in {'id','number','reference','client_request_id','execution_id','action_node_id','status','origin'}}
  other=SupportTicket(**values,number=998,reference='PTX-998',client_request_id=None,execution_id=uuid.uuid4(),action_node_id=uuid.uuid4(),status='open',origin='workflow');db.add(other)
  for kind,recipient,status,seconds in [('initial','customer','sent',1),('reply','customer','sent',30),('initial','assignee','sent',2),('reply','customer','failed',32)]:db.add(TicketEmail(ticket_id=ticket.id,kind=kind,recipient_type=recipient,recipient='test@example.com',sender='support@example.com',sender_name='Support',subject='Example',body_html='<p>Reply</p>',body_text='Reply',message_id=str(uuid.uuid4()),status=status,sent_at=at+timedelta(seconds=seconds) if status=='sent' else None,created_at=at+timedelta(seconds=seconds),actor_id=owner))
  db.add(TicketInboundEmail(ticket_id=ticket.id,message_id='incoming-test',sender='person@example.com',recipient='support@example.com',subject='Question',content='Customer email',created_at=at+timedelta(seconds=20)))
  db.flush();db.execute(delete(AnalyticsRecordEvent).where(AnalyticsRecordEvent.workspace_id==ws))
  for seconds,event,status,assigned in [(10,'assignment','open',uid),(40,'assignment','open',owner),(120,'status','closed',owner)]:db.add(AnalyticsRecordEvent(workspace_id=ws,conversation_id=chat.id,event=event,status=status,assigned_user_id=assigned,team_ids=[f['team']['id']],created_at=at+timedelta(seconds=seconds)))
  db.add(AnalyticsRecordEvent(workspace_id=ws,ticket_id=ticket.id,event='status',status='resolved',created_at=at+timedelta(seconds=120)))
  db.commit();f={**f,'at':at,'ai':str(ai.id),'ws':ws}
 return f

def report(client,f,name='chats',**params):
 r=client.get(f['root']+'/analytics/'+name,params={'start':'2026-11-10','end':'2026-11-12',**params});assert r.status_code==200,r.text;return r.json()
def values(data):return {v['key']:v['value'] for v in data['summary']}
def sections(data):return {v['key']:v['value'] for s in data['sections'] for v in s['metrics']}

def test_chat_counts_medians_means_assignment_epochs_and_private_note_exclusion(client,analytics):
 data=report(client,analytics);v=values(data);s=sections(data)
 assert v['total_chats']==5 and v['ai_chats']==2 and v['livechats']==3
 assert s['ai_first']==2 and s['ai_response']==1.5 and s['live_first']==17 and s['live_response']==15
 assert s['closed_without_feedback']==1 and s['closed_without_replies']==1
 assert sum(v['y'] for v in data['charts'][1]['data'])==5
 assert 'Private staff note' not in json.dumps(data)

def test_messaging_counts_individual_customer_messages_not_internal_notes_or_unsent_emails(client,analytics):
 data=report(client,analytics,'messaging',segment_by='message_type');assert values(data)['messages']==12
 assert sum(v['y'] for v in data['charts'][0]['data'])==12
 assert 'Private staff note' not in json.dumps(data)

def test_ticket_response_resolution_and_customer_email_counts(client,analytics):
 data=report(client,analytics,'tickets');v=values(data)
 assert v=={'new_tickets':2,'emails_per_ticket':1.5,'first_reply':30,'resolution':120}
 manual=report(client,analytics,'tickets',filters=json.dumps({'creator_type':'live'}));assert values(manual)['new_tickets']==1

def test_csat_summary_chart_feedback_share_filters_and_submission_dates(client,analytics):
 f=analytics;data=report(client,f,'csat',group_by='rating');v=values(data)
 assert v=={'rated':2,'average':4.5,'csat_pct':100,'positive_pct':50}
 assert data['table']['total']==2 and sum(x['y'] for x in data['charts'][1]['data'])==2
 one=report(client,f,'csat',end='2026-11-10');assert values(one)['rated']==1 and one['table']['items'][0]['rating']==5
 filtered=report(client,f,'csat',filters=json.dumps({'ratings':[4]}),group_by='rating')
 assert values(filtered)['rated']==1 and filtered['table']['items'][0]['rating']==4 and sum(x['y'] for x in filtered['charts'][1]['data'])==1

def test_live_counters_exclude_unrouted_pending_chats(client,analytics):
 data=report(client,analytics,'live');v=values(data)
 assert v['unassigned_livechats']==1 and v['open_livechats']==0 and v['open_ai']==1 and v['open_tickets']==1
 assert data['timezone']=='Africa/Nairobi'

def test_timezone_boundaries_and_workspace_setting_preservation(client,analytics,app):
 f=analytics
 with app.state.session_factory() as db:
  old=db.get(AgentConversation,uuid.UUID(f['chat']));db.add(AgentConversation(workspace_id=f['ws'],agent_id=old.agent_id,user_id=old.user_id,agent_name='Boundary',live_status='none',created_at=f['at']-timedelta(hours=2),workflow_state={'channel':'webchat'}));db.commit()
 assert values(report(client,f,end='2026-11-10'))['total_chats']==6
 url=f['root']+'/workspace-settings';settings=client.get(url).json()
 response=client.post(url+'/general',data={'name':settings['name'],'expected_revision':settings['revision'],'timezone':'UTC'},headers=f['h']);assert response.status_code==200,response.text
 assert values(report(client,f,end='2026-11-10'))['total_chats']==5
 bad=client.post(url+'/general',data={'name':settings['name'],'expected_revision':response.json()['revision'],'timezone':'Bad/Timezone'},headers=f['h']);assert bad.status_code==422
 assert client.get(url).json()['timezone']=='UTC'

def test_report_grants_are_independent_and_feedback_names_stay_redacted(client,analytics,app):
 f=analytics;custom_actor(app,str(f['ws']),['analytics_chats','analytics_csat'])
 options=client.get(f['root']+'/analytics/options');assert options.status_code==200
 assert {v['key'] for v in options.json()['reports']}=={'live','chats','csat'}
 assert client.get(f['root']+'/analytics/messaging').status_code==403
 assert client.get(f['root']+'/analytics/tickets').status_code==403
 assert values(report(client,f))['total_chats']==5
 feedback=report(client,f,'csat')['table']['items'];assert all(not r['can_open'] and r['person_name']=='Person' for r in feedback)
 assert {v['key'] for v in report(client,f,'live')['summary']}=={'unassigned_livechats','open_livechats','open_ai'}

@pytest.mark.parametrize('params',[{'start':'2026-11-12','end':'2026-11-10'},{'start':'2020-01-01','end':'2026-11-10'},{'filters':'{"ratings":[6]}'},{'filters':'{"unknown":true}'},{'space_id':str(uuid.uuid4())},{'limit':101}])
def test_invalid_filters_dates_and_scope_are_rejected(client,analytics,params):
 assert client.get(analytics['root']+'/analytics/chats',params=params).status_code in {404,422}

def test_overlapping_sessions_do_not_double_count_active_hours_or_invent_earlier_history():
 at=datetime(2026,11,10,tzinfo=timezone.utc);v=lambda minutes:at+timedelta(minutes=minutes)
 raw=[(v(10),v(30),'online',10,v(29)),(v(15),v(25),'online',10,v(24)),(v(30),v(40),'away',0,v(39)),(v(40),v(45),'offline',0,v(44))]
 timeline,d=merge(raw,at,v(60),v(5),v(60));assert d['online']==1200 and d['away']==600 and d['offline']==300 and d['unknown']==300 and d['logged_out']==1200
 assert sum(d.values())==3600

def test_activity_heartbeat_is_csrf_protected_and_logout_closes_lease(client,analytics,app):
 f=analytics;url=f['root']+'/analytics/activity'
 assert client.post(url,json={}).status_code==403
 first=client.post(url,json={},headers=f['h']);assert first.status_code==200,first.text
 assert first.json()['state']=='online'
 second=client.post(url,json={},headers=f['h']);assert second.status_code==200
 with app.state.session_factory() as db:
  rows=list(db.scalars(select(AnalyticsActivity).where(AnalyticsActivity.workspace_id==f['ws'],AnalyticsActivity.user_id==uuid.UUID(f['owner']))));assert len(rows)==1 and rows[0].lease_until>rows[0].last_seen_at
 assert client.post('/api/auth/logout',json={},headers=f['h']).status_code==204
 with app.state.session_factory() as db:assert db.scalar(select(AnalyticsActivity.ended_at).where(AnalyticsActivity.workspace_id==f['ws'],AnalyticsActivity.user_id==uuid.UUID(f['owner']))) is not None

def test_agent_reports_include_measured_timelines_and_assignment_heatmap(client,analytics):
 f=analytics;data=report(client,f,'agents',view='heatmap');assert data['heatmap']['maximum']>=1
 assert sum(sum(v['values']) for v in data['heatmap']['rows'])>=2
 assert data['table']['total']>=2


def test_messaging_deduplicates_network_requests_and_excludes_transferred_history(client,analytics,app):
 f=analytics;before=values(report(client,f,'messaging'))['messages']
 with app.state.session_factory() as db:
  request=str(uuid.uuid4())
  for index in range(2):db.add(AgentMessage(conversation_id=uuid.UUID(f['chat']),role='user',content='One received request',created_at=f['at']+timedelta(minutes=2),message_data={'source_request_id':request}))
  db.add(AgentMessage(conversation_id=uuid.UUID(f['chat']),role='assistant',content='Copied previous context',created_at=f['at']+timedelta(minutes=3),message_data={'transferred_history':True}))
  db.commit()
 assert values(report(client,f,'messaging'))['messages']==before+1


def test_workspace_day_boundaries_follow_dst(client,analytics,app):
 from app.analytics.data import bounds
 with app.state.session_factory() as db:
  db.get(WorkspaceSettings,analytics['ws']).timezone='America/New_York';db.commit()
  date=__import__('datetime').date(2026,3,8)
  _,_,start,end,_=bounds(db,analytics['ws'],date,date)
  assert (end-start).total_seconds()==23*3600


def test_report_pagination_is_after_filtering(client,analytics):
 f=analytics;full=report(client,f,'csat')['table'];one=report(client,f,'csat',limit=1,offset=1)['table']
 assert full['total']==one['total']==2 and len(one['items'])==1 and one['items'][0]['id']==full['items'][1]['id']


def test_tag_group_and_typed_custom_field_filters_apply_to_all_report_sections(client,analytics,app):
 f=analytics;_,tag,group,_=tags(client,(f['root'],f['space'],f['chat'],f['h']))
 labels(client,(f['root'],f['space'],f['chat'],f['h']),'chats',f['chat'],[],[group['id']])
 selected=report(client,f,filters=json.dumps({'tags':[tag['id']]}));assert values(selected)['total_chats']==1
 assert values(report(client,f,filters=json.dumps({'groups':[group['id']]})))['total_chats']==1
 created=client.post(f['root']+'/custom-fields',json={'scope':'conversation','type':'single_select_grouped','name':'Analytics product','options':[{'name':'Products','options':[{'value':'loan','label':'Loan'},{'value':'savings','label':'Savings'}]}],'default_value':'loan'},headers=f['h']);assert created.status_code==201,created.text
 field=created.json()
 with app.state.session_factory() as db:
  db.get(AgentConversation,uuid.UUID(f['chat'])).custom_fields={field['key']:{'value':'savings'}};db.commit()
 loan=report(client,f,filters=json.dumps({'custom_fields':[{'key':field['key'],'value':'loan'}]}));assert values(loan)['total_chats']==4
 assert client.get(f['root']+'/analytics/chats',params={'filters':json.dumps({'custom_fields':[{'key':field['key'],'value':'unknown'}]})}).status_code==422


def test_teamspace_permissions_scope_aggregates_and_filter_options(client,analytics,app):
 f=analytics;custom_actor(app,str(f['ws']),['analytics_chats'])
 with app.state.session_factory() as db:
  from app.models import Membership
  member=db.scalar(select(Membership).where(Membership.workspace_id==f['ws'],Membership.user_id==uuid.UUID(f['owner'])));member.teamspace_ids=[f['space']]
  space=Teamspace(workspace_id=f['ws'],name='Restricted report',name_key='restricted report',is_default=False);db.add(space);db.flush()
  agent=Agent(workspace_id=f['ws'],teamspace_id=space.id,name='HIDDEN REPORT AGENT');db.add(agent);db.flush()
  db.add(AgentConversation(workspace_id=f['ws'],agent_id=agent.id,user_id=uuid.UUID(f['owner']),agent_name=agent.name,created_at=f['at'],workflow_state={'channel':'webchat'}));db.commit();space_id=str(space.id)
 assert values(report(client,f))['total_chats']==5
 assert 'HIDDEN REPORT AGENT' not in client.get(f['root']+'/analytics/options').text
 assert client.get(f['root']+'/analytics/chats',params={'space_id':space_id}).status_code==404


def test_heartbeat_gap_does_not_extend_presence_over_unobserved_time(client,analytics,app,monkeypatch):
 from app.analytics import tracking
 f=analytics;clock=[utcnow()];monkeypatch.setattr(tracking,'utcnow',lambda:clock[0]);url=f['root']+'/analytics/activity'
 first=client.post(url,json={},headers=f['h']);assert first.status_code==200
 clock[0]+=timedelta(seconds=121)
 assert client.post(url,json={},headers=f['h']).status_code==200
 with app.state.session_factory() as db:
  rows=list(db.scalars(select(AnalyticsActivity).where(AnalyticsActivity.workspace_id==f['ws'],AnalyticsActivity.user_id==uuid.UUID(f['owner'])).order_by(AnalyticsActivity.started_at)))
  assert len(rows)==2 and rows[0].ended_at==rows[0].lease_until and rows[1].started_at>rows[0].ended_at


def test_resolution_history_is_not_changed_by_later_notes(client,analytics,app):
 f=analytics
 with app.state.session_factory() as db:
  row=db.get(SupportTicket,uuid.UUID(f['ticket']));row.status='open';db.commit();row.status='resolved';db.commit()
  count=db.scalar(select(func.count()).select_from(AnalyticsRecordEvent).where(AnalyticsRecordEvent.ticket_id==row.id,AnalyticsRecordEvent.event=='status'))
 result=client.post(f['root']+'/tickets/'+f['ticket']+'/notes',json={'content':'Later internal note'},headers=f['h']);assert result.status_code==201,result.text
 with app.state.session_factory() as db:
  after=db.scalar(select(func.count()).select_from(AnalyticsRecordEvent).where(AnalyticsRecordEvent.ticket_id==uuid.UUID(f['ticket']),AnalyticsRecordEvent.event=='status'))
 assert after==count and values(report(client,f,'tickets'))['resolution']==120


def test_capacity_observation_does_not_initialize_or_change_assignment_policy(client,workflow,app):
 from app.models import TeamspaceOperations,ChatAssignment
 root=workflow[0].rsplit('/agents/',1)[0];h=workflow[2];ws=uuid.UUID(root.split('/')[3])
 with app.state.session_factory() as db:assert db.scalar(select(func.count()).select_from(TeamspaceOperations).where(TeamspaceOperations.workspace_id==ws))==0
 assert client.post(root+'/analytics/activity',json={},headers=h).status_code==200
 with app.state.session_factory() as db:
  assert db.scalar(select(func.count()).select_from(TeamspaceOperations).where(TeamspaceOperations.workspace_id==ws))==0
  assert db.get(ChatAssignment,ws).max_active_chats==5


def test_live_people_counts_include_legacy_default_teamspace_profiles(client,analytics,app):
 f=analytics
 with app.state.session_factory() as db:
  chat=db.get(AgentConversation,uuid.UUID(f['chat']));person=db.get(SupportPerson,chat.person_id);person.teamspace_id=None;person.created_at=utcnow()
  db.add(AgentMessage(conversation_id=chat.id,role='user',content='Current day activity',created_at=utcnow()));db.commit()
 result=sections(report(client,f,'live'));assert result['new_people']>=1 and result['active_people']>=1
