"""Real multi-user support lifecycle, queue visibility and resumable AI workflows."""
from copy import deepcopy
from datetime import timedelta
import uuid
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import select
from app.core.security import utcnow
from app.models import AgentConversation, AgentMessage, AuthSession, Membership, SupportAgent, Workspace
from app.workflows.actions.live_agent_handoff.config import LiveAgentHandoffConfig
from test_ask_survey import field,survey
from test_auto_team_match import team_action
from test_branch import variable
from test_chat_closed import closed_root
from test_collect_feedback import form,submit
from test_jump import prepare,setup_tree
from test_teams import create_team,team_body
from test_workflows import workflow,node,message,intent,event,save,contents


def handoff(closed=None,unavailable=None,**settings):
    config={'availability_check':True,'assignment_mode':'all','user_ids':[],'team_ids':[],'team_variable':'','assign_by_language':False,
        'closed_path_id':str(uuid.uuid4()),'unavailable_path_id':str(uuid.uuid4()),**settings}
    action=node('action','live_agent_handoff',name='Speak to support',config=config)
    action['paths']=[{'id':config['closed_path_id'],'children':closed or []},{'id':config['unavailable_path_id'],'children':unavailable or []}]
    return action


@pytest.fixture
def workers(app,client,workflow):
    clients=[];path,_,owner_headers=workflow;base=path.rsplit('/agents/',1)[0]
    def create(index=1):
        worker=TestClient(app,base_url='http://localhost:4200');clients.append(worker)
        email='support{}@example.com'.format(index)
        assert worker.post('/api/auth/magic-link',json={'email':email}).status_code==202
        signed=worker.post('/api/auth/verify',json={'token':app.state.mailer.token}).json()
        headers={'X-Petrix-CSRF':worker.cookies.get('petrix_csrf')}
        added=client.post(base+'/members',json={'email':email},headers=owner_headers)
        assert added.status_code==201,added.text
        selected=worker.post(base+'/select',json={},headers=headers)
        assert selected.status_code==200,selected.text
        assert worker.get('/api/auth/me').json()['workspace']['id']==base.split('/')[3]
        return worker,headers,signed['user']['id']
    yield create
    for worker in clients:worker.close()


def support(client,path,headers,ids,name='Complaints',aliases=None):
    base,team=create_team(client,path,headers,{**team_body(name,aliases if aliases is not None else ['complaint support']),'member_ids':ids})
    return path.rsplit('/agents/',1)[0],team


def available(worker,base,headers,languages=None,value=True):
    profile=worker.get(base+'/inbox/profile').json()
    response=worker.post(base+'/inbox/profile',json={'expected_revision':profile['revision'],'available':value,'languages':languages or ['en']},headers=headers)
    assert response.status_code==200,response.text
    return response.json()


def configure(client,path,document,headers,actions,closing=None):
    document['nodes'][0]['children']=actions
    if closing is not None:closed_root(document)['children']=closing
    response=save(client,path,document,headers);assert response.status_code==200,response.text
    return response.json()


def queued(client,path,headers):
    response=event(client,path,headers);assert response.status_code==200,response.text
    assert response.json()['live_chat']['status'] in {'pending','active'}
    return response.json()


@pytest.mark.parametrize('settings',[{'assignment_mode':'any'},{'availability_check':'yes'},{'assign_by_language':1},
    {'assignment_mode':'specific'},{'team_ids':['bad']},{'user_ids':['bad']},{'team_variable':'{matched_team}'},
    {'team_variable':'system'},{'extra':True}])
def test_invalid_action_settings(settings):
    if 'team_variable' in settings:settings['assignment_mode']='specific'
    with pytest.raises(ValidationError):LiveAgentHandoffConfig.model_validate(handoff(**settings)['config'])


@pytest.mark.parametrize('change',['missing','duplicate','extra','ordinary_children','same_ids'])
def test_handoff_has_exactly_two_stable_paths(change):
    agent,document=setup_tree();action=handoff();document.nodes[0]['children']=[action]
    if change=='missing':action['paths'].pop()
    elif change=='duplicate':action['paths'][1]=deepcopy(action['paths'][0])
    elif change=='extra':action['paths'].append({'id':str(uuid.uuid4()),'children':[]})
    elif change=='same_ids':action['config']['closed_path_id']=action['config']['unavailable_path_id']
    else:action['children']=[message('Wrong container')]
    with pytest.raises(HTTPException):prepare(agent,document)


def test_claim_human_reply_customer_reply_close_and_ai_resume(client,workflow,workers,app):
    path,document,headers=workflow;worker,staff_headers,user_id=workers()
    base,team=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    configure(client,path,document,headers,[message('Connecting you.'),handoff(closed=[message('Welcome back.')]),message('After support.')],
        closing=[variable('closed_count','variables.get("closed_count", 0) + 1')])
    result=queued(client,path,headers);conversation=result['conversation_id']
    assert [m['content'] for m in result['messages']]==['Connecting you.']
    assert 'nodes' not in result['live_chat']
    inbox=worker.get(base+'/inbox/chats').json()['chats'];assert len(inbox)==1
    claimed=worker.post(base+'/inbox/chats/'+conversation+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers)
    assert claimed.status_code==200,claimed.text
    chat=claimed.json();assert chat['status']=='active' and chat['assigned_user_id']==user_id
    assert worker.post(base+'/inbox/chats/'+conversation+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers).status_code==409
    reply={'expected_revision':chat['revision'],'content':'Hello, I can help.','client_message_id':str(uuid.uuid4())}
    assert worker.post(base+'/inbox/chats/'+conversation+'/reply',json=reply,headers=staff_headers).status_code==200
    assert worker.post(base+'/inbox/chats/'+conversation+'/reply',json=reply,headers=staff_headers).status_code==200
    customer=event(client,path,headers,'message_received',conversation,content='My transaction failed.')
    assert customer.status_code==200 and customer.json()['messages']==[] and customer.json()['trace']==[]
    polled=client.get(path+'/tester/conversations/'+conversation).json()
    assert [m['content'] for m in polled['messages']]==['Connecting you.','Hello, I can help.','My transaction failed.']
    assert polled['messages'][1]['source']=='live_agent'
    assert event(client,path,headers,'chat_closed',conversation).status_code==409
    closed=worker.post(base+'/inbox/chats/'+conversation+'/close',json={'expected_revision':chat['revision']},headers=staff_headers)
    assert closed.status_code==200,closed.text
    current=client.get(path+'/tester/conversations/'+conversation).json()
    assert not current['closed'] and current['live_chat']['status']=='closed' and current['variables']['closed_count']==1
    assert [m['content'] for m in current['messages']][-2:]==['Welcome back.','After support.']
    assert worker.post(base+'/inbox/chats/'+conversation+'/close',json={'expected_revision':chat['revision']},headers=staff_headers).status_code==409
    with app.state.session_factory() as db:
        stored=db.get(AgentConversation,uuid.UUID(conversation));assert not stored.workflow_state['closed']
        assert 'nodes' not in stored.live_context
        assert len(db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==stored.id,AgentMessage.message_data['source'].as_string()=='live_agent')).all())==1


def test_feedback_precedes_return_and_original_survey_continuation_survives_edits(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    document=configure(client,path,document,headers,[handoff(closed=[message('Original follow-up'),survey(field('more_help'))]),message('Original after support')],
        closing=[variable('closed_count','variables.get("closed_count",0)+1'),form(survey=False),variable('feedback_done','True')])
    result=queued(client,path,headers);conversation=result['conversation_id'];chat=worker.post(base+'/inbox/chats/'+conversation+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers).json()
    # The current Chat Closed workflow runs; the original handoff continuation is frozen.
    document['nodes'][0]['children']=[message('Replacement start')]
    document['nodes'].append(intent('new revision',[message('New revision response')]))
    document=configure(client,path,document,headers,document['nodes'][0]['children'],closing=closed_root(document)['children'])
    response=worker.post(base+'/inbox/chats/'+conversation+'/close',json={'expected_revision':chat['revision']},headers=staff_headers);assert response.status_code==200,response.text
    waiting=client.get(path+'/tester/conversations/'+conversation).json()
    assert waiting['closed'] and waiting['feedback'] and waiting['live_chat']['return_status']=='waiting_feedback'
    assert not any(m['content']=='Original follow-up' for m in waiting['messages'])
    submitted=submit(client,path,headers,waiting,rating=5);assert submitted.status_code==200,submitted.text
    returned=submitted.json();assert not returned['closed'] and returned['variables']['feedback_done']
    assert returned['variables']['feedback_rating']==5 and returned['variables']['closed_count']==1
    assert returned['messages'][0]['content']=='Original follow-up' and returned['survey']
    answered=event(client,path,headers,'message_received',conversation,content='No thank you',survey_answer_token=returned['survey']['answer_token'])
    assert contents(answered)==['Original after support']
    assert contents(event(client,path,headers,'message_received',conversation,content='new revision'))==['New revision response']


@pytest.mark.parametrize('check',[True,False])
def test_unavailable_check_vs_offline_queue(client,workflow,workers,check):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id])
    configure(client,path,document,headers,[handoff(availability_check=check,unavailable=[message('No support right now.')]),message('Next')])
    response=event(client,path,headers);assert response.status_code==200,response.text
    if check:assert contents(response)==['No support right now.','Next'] and response.json()['live_chat']['status']=='none'
    else:
        assert response.json()['messages']==[] and response.json()['live_chat']['status']=='pending'
        chat=response.json()['live_chat'];identifier=response.json()['conversation_id']
        assert worker.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':chat['revision']},headers=staff_headers).status_code==409
        available(worker,base,staff_headers)
        assert worker.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':chat['revision']},headers=staff_headers).status_code==200


def test_specific_targets_language_and_dynamic_team_match(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();other,other_headers,other_id=workers(2)
    base,team=support(client,path,headers,[user_id]);support(client,path,headers,[other_id],'General',['general support'])
    available(worker,base,staff_headers,['fr']);available(other,base,other_headers,['en'])
    document=configure(client,path,document,headers,[handoff(assignment_mode='specific',team_ids=[team['id']],assign_by_language=True,unavailable=[message('Language mismatch')])])
    assert contents(event(client,path,headers))==['Language mismatch']
    available(worker,base,staff_headers,['en','fr'])
    document=configure(client,path,document,headers,[team_action(input='Complaints',found=[handoff(assignment_mode='specific',team_variable='matched_team')])])
    result=queued(client,path,headers);identifier=result['conversation_id']
    assert worker.get(base+'/inbox/chats/'+identifier).status_code==200
    assert other.get(base+'/inbox/chats/'+identifier).status_code==404
    assert other.get(base+'/inbox/chats').json()['chats']==[]
    assert client.get(base+'/inbox/chats/'+identifier).status_code==404
    assert other.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=other_headers).status_code==404


@pytest.mark.parametrize('mode',['round_robin','least_busy'])
def test_automatic_assignment_capacity_and_fairness(client,workflow,workers,mode):
    path,document,headers=workflow;first,h1,id1=workers();second,h2,id2=workers(2)
    base,_=support(client,path,headers,[id1,id2]);available(first,base,h1);available(second,base,h2)
    policy=client.get(base+'/chat-assignment').json()
    response=client.post(base+'/chat-assignment',json={'expected_revision':policy['revision'],'mode':mode,'max_active_chats':1},headers=headers)
    assert response.status_code==200,response.text
    document=configure(client,path,document,headers,[handoff(unavailable=[message('At capacity')])])
    a=queued(client,path,headers);b=queued(client,path,headers)
    assert a['live_chat']['status']==b['live_chat']['status']=='active'
    assert {a['live_chat']['assigned_user_id'],b['live_chat']['assigned_user_id']}=={id1,id2}
    assert contents(event(client,path,headers))==['At capacity']
    document=configure(client,path,document,headers,[handoff(availability_check=False)])
    assert queued(client,path,headers)['live_chat']['status']=='pending'


@pytest.mark.parametrize('reason',['stale','signed_out','expired'])
def test_online_status_requires_fresh_presence_and_a_valid_session(client,workflow,workers,app,reason):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    with app.state.session_factory() as db:
        profile=db.get(SupportAgent,(uuid.UUID(base.split('/')[3]),uuid.UUID(user_id)))
        if reason=='stale':profile.last_seen_at=utcnow()-timedelta(minutes=2)
        elif reason=='signed_out':db.get(AuthSession,profile.presence_session_id).revoked_at=utcnow()
        else:db.get(AuthSession,profile.presence_session_id).expires_at=utcnow()-timedelta(seconds=1)
        db.commit()
    configure(client,path,document,headers,[handoff(unavailable=[message('Unavailable')])])
    assert contents(event(client,path,headers))==['Unavailable']


def test_permissions_csrf_foreign_references_and_team_protection(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,team=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    assert worker.post(base+'/chat-assignment',json={'expected_revision':1,'mode':'manual','max_active_chats':2},headers=staff_headers).status_code==403
    assert worker.post(base+'/inbox/profile',json={'expected_revision':2,'available':True,'languages':['en']}).status_code==403
    document['nodes'][0]['children']=[handoff(assignment_mode='specific',team_ids=[str(uuid.uuid4())])]
    assert save(client,path,document,headers).status_code==422
    document=configure(client,path,document,headers,[handoff(assignment_mode='specific',team_ids=[team['id']])])
    assert client.post(base+'/teams/'+team['id']+'/delete',json={'expected_revision':team['revision']},headers=headers).status_code==409
    result=queued(client,path,headers)
    update={**team_body(),'name':team['name'],'member_ids':[],'expected_revision':team['revision']}
    assert client.post(base+'/teams/'+team['id'],json=update,headers=headers).status_code==409
    assert worker.get(path+'/tester/conversations/'+result['conversation_id']).status_code==404
    foreign='/api/workspaces/'+str(uuid.uuid4())
    assert worker.get(foreign+'/inbox/chats').status_code==404
    assert worker.post(foreign+'/select',json={},headers=staff_headers).status_code==404


def test_failed_return_is_not_replayed_and_does_not_undo_human_closure(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    configure(client,path,document,headers,[handoff(closed=[variable('bad','missing_variable')])])
    result=queued(client,path,headers);identifier=result['conversation_id']
    chat=worker.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers).json()
    response=worker.post(base+'/inbox/chats/'+identifier+'/close',json={'expected_revision':chat['revision']},headers=staff_headers)
    assert response.status_code==200,response.text
    state=client.get(path+'/tester/conversations/'+identifier).json()
    assert state['closed'] and state['automation_error'] and state['live_chat']['return_status']=='failed'
    assert worker.post(base+'/inbox/chats/'+identifier+'/close',json={'expected_revision':chat['revision']},headers=staff_headers).status_code==409


def test_failed_chat_closed_automation_clears_pending_return(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    configure(client,path,document,headers,[handoff(closed=[message('Must not run')])],closing=[variable('bad','missing_variable')])
    result=queued(client,path,headers);identifier=result['conversation_id']
    chat=worker.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers).json()
    closed=worker.post(base+'/inbox/chats/'+identifier+'/close',json={'expected_revision':chat['revision']},headers=staff_headers)
    assert closed.status_code==200,closed.text
    state=client.get(path+'/tester/conversations/'+identifier).json()
    assert state['closed'] and state['live_chat']['return_status']=='failed' and state['automation_error']
    assert not any(m['content']=='Must not run' for m in state['messages'])


@pytest.mark.parametrize('change',['none','foreign','archived'])
def test_invalid_matched_team_never_queues_to_all_agents(client,workflow,workers,app,change):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,team=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    identifier=team['id']
    if change=='foreign':identifier=str(uuid.uuid4())
    if change=='archived':
        from app.models import SupportTeam
        with app.state.session_factory() as db:db.get(SupportTeam,uuid.UUID(team['id'])).archived=True;db.commit()
    expression='None' if change=='none' else '{"id":"'+identifier+'","name":"Do not trust this name"}'
    configure(client,path,document,headers,[variable('matched_team',expression),handoff(assignment_mode='specific',team_variable='matched_team',unavailable=[message('Cannot route')])])
    assert contents(event(client,path,headers))==['Cannot route']
    assert worker.get(base+'/inbox/chats').json()['chats']==[]


def test_specific_user_and_manual_capacity_recheck(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();other,h2,id2=workers(2)
    base,_=support(client,path,headers,[user_id,id2]);available(worker,base,staff_headers);available(other,base,h2)
    assert client.post(base+'/chat-assignment',json={'expected_revision':1,'mode':'manual','max_active_chats':1},headers=headers).status_code==200
    configure(client,path,document,headers,[handoff(availability_check=False,assignment_mode='specific',user_ids=[user_id])])
    first=queued(client,path,headers);second=queued(client,path,headers)
    assert other.get(base+'/inbox/chats').json()['chats']==[]
    for index,result in enumerate([first,second]):
        claimed=worker.post(base+'/inbox/chats/'+result['conversation_id']+'/claim',json={'expected_revision':result['live_chat']['revision']},headers=staff_headers)
        assert claimed.status_code==(200 if index==0 else 409)


def test_no_team_availability_profile_revision_and_membership_setup(client,workflow,workers,app):
    path,_,headers=workflow;base=path.rsplit('/agents/',1)[0]
    profile=client.get(base+'/inbox/profile').json()
    assert client.post(base+'/inbox/profile',json={'expected_revision':profile['revision'],'available':True,'languages':['en']},headers=headers).status_code==422
    worker,staff_headers,user_id=workers()
    assert client.post(base+'/members',json={'email':'support1@example.com'},headers=headers).status_code==409
    count=len(app.state.mailer.messages)
    assert client.post(base+'/members',json={'email':'not-registered@example.com'},headers=headers).status_code==422
    assert len(app.state.mailer.messages)==count
    base,_=support(client,path,headers,[user_id]);initial=worker.get(base+'/inbox/profile').json()
    saved=available(worker,base,staff_headers)
    assert worker.post(base+'/inbox/profile',json={'expected_revision':initial['revision'],'available':False,'languages':['fr']},headers=staff_headers).status_code==409
    assert worker.post(base+'/inbox/profile',json={'expected_revision':saved['revision'],'available':True,'languages':['xx']},headers=staff_headers).status_code==422


def test_message_cursor_and_claim_loser_cannot_read_or_reply(client,workflow,workers):
    path,document,headers=workflow;first,h1,id1=workers();second,h2,id2=workers(2)
    base,_=support(client,path,headers,[id1,id2]);available(first,base,h1);available(second,base,h2)
    configure(client,path,document,headers,[handoff()])
    result=queued(client,path,headers);identifier=result['conversation_id'];revision=result['live_chat']['revision']
    chat=first.post(base+'/inbox/chats/'+identifier+'/claim',json={'expected_revision':revision},headers=h1).json()
    assert second.get(base+'/inbox/chats/'+identifier).status_code==404
    assert second.post(base+'/inbox/chats/'+identifier+'/reply',json={'expected_revision':chat['revision'],'content':'Not mine','client_message_id':str(uuid.uuid4())},headers=h2).status_code==404
    assert first.get(base+'/inbox/chats/'+identifier+'?before='+str(uuid.uuid4())).status_code==404
    assert first.post(base+'/inbox/chats/'+identifier+'/close',json={'expected_revision':revision},headers=h1).status_code==409


def test_live_chat_blocks_ai_agent_deletion_and_private_actor_parameters(client,workflow,workers):
    path,document,headers=workflow;worker,staff_headers,user_id=workers();base,_=support(client,path,headers,[user_id]);available(worker,base,staff_headers)
    configure(client,path,document,headers,[handoff()]);result=queued(client,path,headers)
    agent=client.get(path).json()
    response=client.post(path+'/delete',json={'expected_revision':agent['revision'],'confirmation_name':agent['name']},headers=headers)
    assert response.status_code==409
    assert client.post(path+'/tester/events',json={'event':'chat_closed','conversation_id':result['conversation_id'],'inbox_actor':user_id},headers=headers).status_code==422


def test_workspace_admin_member_management_does_not_grant_ai_management(client,workflow,workers,app):
    from app.models import RolePermission
    path,_,_=workflow;base=path.rsplit('/agents/',1)[0]
    admin,admin_headers,identifier=workers();workers(2)
    workspace_id=uuid.UUID(base.split('/')[3])
    with app.state.session_factory() as db:
        db.scalar(select(Membership).where(Membership.workspace_id==workspace_id,Membership.user_id==uuid.UUID(identifier))).role='admin'
        db.scalar(select(RolePermission).where(RolePermission.workspace_id==workspace_id,RolePermission.role=='admin')).manage_ai_agents=False
        db.commit()
    assert admin.get(base+'/members').json()['can_manage']
    # The admin reaches membership validation, even without Manage AI Agents.
    assert admin.post(base+'/members',json={'email':'support2@example.com'},headers=admin_headers).status_code==409
    assert admin.post(base+'/chat-assignment',json={'expected_revision':1,'mode':'manual','max_active_chats':2},headers=admin_headers).status_code==403
