import uuid
from sqlalchemy import select, func
import pytest
from app.models import AgentConversation, AgentMessage, LivechatNote, SupportPerson, User
from test_live_agent_handoff import workers, support, available, configure, handoff, queued
from test_workflows import workflow, message, event

@pytest.fixture
def repository(client, workflow, workers):
    path, doc, headers = workflow
    worker, wh, uid = workers()
    base, team = support(client, path, headers, [uid])
    available(worker, base, wh)
    configure(client, path, doc, headers, [message('Welcome'), handoff(closed=[message('AI resumed')])])
    chat = queued(client, path, headers)['conversation_id']
    return base, chat, headers, worker, wh, uid, team, path

def test_livechats_only_handoffs_and_permission_scoped_list_details(client, repository):
    base, chat, h, worker, wh, uid, team, path = repository
    response = client.get(base+'/livechats')
    assert response.status_code==200, response.text
    listing=response.json();assert listing['total']==1 and listing['livechats'][0]['id']==chat
    assert listing['livechats'][0]['status']=='pending'
    options=client.get(base+'/livechats/options');assert options.status_code==200,options.text
    detail=client.get(base+'/livechats/'+chat);assert detail.status_code==200,detail.text
    assert detail.json()['notes']==[] and detail.json()['messages'][0]['content']=='Welcome'
    assert client.get(base+'/livechats',params={'q':'x'*257}).status_code==422
    assert client.get(base+'/livechats',params={'q':chat}).json()['total']==1
    assert client.get(base+'/livechats',params={'q':'missing'}).json()['total']==0
    assert client.get(base+'/livechats',params={'status':'closed'}).json()['total']==0
    assert client.get(base+'/livechats',params={'rating':'none'}).json()['total']==1
    assert client.get(base+'/livechats',params={'period':'today'}).json()['total']==1
    assert client.get(base+'/livechats',params={'timezone':'invalid-zone'}).status_code==422

def test_notes_are_revision_safe_and_never_enter_customer_transcript(client, repository):
    base, chat, h, worker, wh, uid, team, path=repository;url=base+'/livechats/'+chat
    note=client.post(url+'/notes',json={'content':'PRIVATE NOTE','color':'#abcd12'},headers=h)
    assert note.status_code==201,note.text
    n=note.json();assert n['revision']==1
    updated=client.post(url+'/notes/'+n['id'],json={'content':'Updated private note','color':'#123456','expected_revision':1},headers=h)
    assert updated.status_code==200,updated.text
    assert client.post(url+'/notes/'+n['id'],json={'content':'stale','expected_revision':1},headers=h).status_code==409
    transcript=client.get(path+'/tester/conversations/'+chat)
    assert transcript.status_code==200,transcript.text
    assert 'PRIVATE NOTE' not in transcript.text and 'Updated private note' not in transcript.text
    assert client.post(url+'/notes',json={'content':'no csrf'}).status_code==403
    assert client.post(url+'/notes',json={'content':'bad','color':'red'},headers=h).status_code==422
    assert client.post(url+'/notes/'+n['id']+'/delete',json={'expected_revision':2},headers=h).status_code==200
    assert client.get(url).json()['notes']==[]

def test_description_status_completion_reuses_workflow_and_transfer(client, repository):
    base,chat,h,worker,wh,uid,team,path=repository;url=base+'/livechats/'+chat
    d=client.get(url).json()
    transferred=client.post(url+'/transfer',json={'expected_revision':d['revision'],'user_id':uid,'team_ids':[team['id']]},headers=h)
    assert transferred.status_code==200,transferred.text
    d=worker.get(url).json();assert d['assigned_user_id']==uid and d['status']=='open'
    saved=worker.post(url+'/details',json={'expected_revision':d['revision'],'description':'Investigating issue','status':'overdue'},headers=wh)
    assert saved.status_code==200,saved.text
    assert saved.json()['status']=='overdue'
    result=worker.post(url+'/details',json={'expected_revision':saved.json()['revision'],'description':'Solved','status':'resolved'},headers=wh)
    assert result.status_code==200,result.text
    assert result.json()['status']=='resolved'
    messages=worker.get(url).json()['messages'];assert any(m['content']=='AI resumed' for m in messages)
    assert client.get(base+'/livechats',params={'status':'resolved'}).json()['total']==1
    assert worker.post(url+'/details',json={'expected_revision':result.json()['revision'],'description':'bad reopen','status':'open'},headers=wh).status_code==409

def test_delete_validates_every_revision_and_keeps_consumed_interactions(client, repository, app):
    from app.workspace_settings.usage import source_query
    base,chat,h,worker,wh,uid,team,path=repository;url=base+'/livechats/'+chat
    client.post(url+'/notes',json={'content':'Sensitive note'},headers=h)
    revision=client.get(url).json()['revision']
    before=client.get(base+'/livechats').json()['total']
    with app.state.session_factory() as db:
        count_before=db.scalar(select(func.sum(source_query(uuid.UUID(base.split('/')[3])).c.units)))
    bad={'ids':[chat],'revisions':{chat:revision+1},'confirmation':'DELETE'}
    assert client.post(base+'/livechats/delete',json=bad,headers=h).status_code==409
    assert client.get(base+'/livechats').json()['total']==before
    bad['revisions'][chat]=revision
    removed=client.post(base+'/livechats/delete',json=bad,headers=h)
    assert removed.status_code==200,removed.text
    assert client.get(url).status_code==404
    with app.state.session_factory() as db:
        assert db.get(AgentConversation,uuid.UUID(chat)) is None
        assert db.scalar(select(func.count()).select_from(LivechatNote))==0
        assert db.scalar(select(func.sum(source_query(uuid.UUID(base.split('/')[3])).c.units)))==count_before

@pytest.mark.parametrize('grants,total', [([],None),(['view_livechats_all'],1),(['view_livechats_own'],0)])
def test_repository_permissions_do_not_fall_back_to_tester_ownership(client, repository, app, grants, total):
    from test_workspace_settings import custom_actor
    base,chat,h,worker,wh,uid,team,path=repository
    # Assign to the worker so the tester creator must not obtain own-record access.
    d=client.get(base+'/livechats/'+chat).json()
    assert client.post(base+'/livechats/'+chat+'/transfer',json={'expected_revision':d['revision'],'user_id':uid},headers=h).status_code==200
    custom_actor(app,base.split('/')[3],grants)
    response=client.get(base+'/livechats')
    if total is None:assert response.status_code==403
    else:assert response.status_code==200 and response.json()['total']==total,response.text
    if total==1:
        assert response.json()['livechats'][0]['can_manage'] is False
        assert client.get(base+'/livechats/'+chat).status_code==200
        assert client.post(base+'/livechats/'+chat+'/notes',json={'content':'forbidden'},headers=h).status_code==404
        assert client.post(base+'/support/chats/'+chat+'/labels',json={'tag_ids':[],'group_ids':[],'mode':'replace'},headers=h).status_code==404
    else:assert client.get(base+'/livechats/'+chat).status_code==404


def test_team_and_teamspace_scopes_match_list_detail_notes_and_filters(client, repository, app):
    from test_workspace_settings import custom_actor
    from app.models import Membership, SupportTeam
    base,chat,h,worker,wh,uid,team,path=repository
    actor,_=custom_actor(app,base.split('/')[3],['view_livechats_teams','manage_livechats_teams'])
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat));row.live_context={**row.live_context,'routing':{**row.live_context['routing'],'team_ids':[team['id']]}}
        t=db.get(SupportTeam,uuid.UUID(team['id']));t.member_ids=[*t.member_ids,str(actor)];db.commit()
    assert client.get(base+'/livechats').json()['total']==1
    assert client.get(base+'/livechats',params={'team':team['id']}).json()['total']==1
    assert client.post(base+'/livechats/'+chat+'/notes',json={'content':'Team note'},headers=h).status_code==201
    with app.state.session_factory() as db:
        m=db.scalar(select(Membership).where(Membership.workspace_id==uuid.UUID(base.split('/')[3]),Membership.user_id==actor));m.teamspace_ids=[];db.commit()
    assert client.get(base+'/livechats').json()['total']==0
    assert client.get(base+'/livechats/'+chat).status_code==404


def test_search_dates_language_csat_literal_characters_and_pagination(client, repository, app):
    from datetime import timedelta
    from app.core.security import utcnow
    base,chat,h,worker,wh,uid,team,path=repository
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat));person=db.get(SupportPerson,row.person_id)
        person.name='Client 100%_case';person.email='demo@example.invalid';person.phone='+254700000001'
        row.workflow_state={**row.workflow_state,'csat_rating':5,'language':'sw'}
        row.live_context={**row.live_context,'primary_language':'en','languages':['en','sw']}
        db.add(AgentConversation(workspace_id=row.workspace_id,agent_id=row.agent_id,user_id=row.user_id,agent_name=row.agent_name,workflow_state={'language':'en'},live_context=dict(row.live_context),live_status='closed',created_at=utcnow()-timedelta(days=10)))
        db.commit()
    for q in ['100%_case','DEMO@example.invalid','700000001']:
        result=client.get(base+'/livechats',params={'q':q});assert result.status_code==200,result.text;assert result.json()['total']==1
    assert client.get(base+'/livechats',params={'q':'100%_wrong'}).json()['total']==0
    assert client.get(base+'/livechats',params={'rating':'5'}).json()['total']==1
    assert client.get(base+'/livechats',params={'rating':'none'}).json()['total']==1
    assert client.get(base+'/livechats',params={'primary_language':'en','language':'sw'}).json()['total']==2
    assert client.get(base+'/livechats',params={'primary_language':'sw'}).json()['total']==0
    assert client.get(base+'/livechats',params={'period':'today'}).json()['total']==1
    assert client.get(base+'/livechats',params={'limit':1,'offset':1}).json()['total']==2
    assert len(client.get(base+'/livechats',params={'limit':1,'offset':1}).json()['livechats'])==1
    assert client.get(base+'/livechats',params={'period':'custom','created_from':'2026-11-20','created_to':'2026-11-01'}).status_code==422
    assert client.get(base+'/livechats',params={'custom_key':'unknown'}).status_code==422


def test_bulk_deletion_is_atomic_and_invalidates_prior_exports(client, repository, app):
    from app.models import SupportJob
    from app.teamspace.jobs import run_one
    from app.teamspace.ai import SupportAI
    base,chat,h,worker,wh,uid,team,path=repository
    result=client.post(base+'/support/exports',json={'request_id':str(uuid.uuid4()),'kind':'chats','ids':[chat],'fields':['id','status','description','teams','live_agent','rating'],'transcript':True,'timezone':'Africa/Nairobi'},headers=h)
    assert result.status_code==202,result.text
    job=result.json()['id'];assert run_one(app.state.session_factory,app.state.settings,SupportAI(app.state.settings))
    download=client.get(base+'/support/exports/'+job+'/download');assert download.status_code==200,download.text
    assert b'Welcome' in download.content and b'pending' in download.content
    d=client.get(base+'/livechats/'+chat).json();foreign=str(uuid.uuid4())
    body={'ids':[chat,foreign],'revisions':{chat:d['revision'],foreign:1},'confirmation':'DELETE'}
    assert client.post(base+'/livechats/delete',json=body,headers=h).status_code==404
    assert client.get(base+'/livechats/'+chat).status_code==200
    body={'ids':[chat],'revisions':{chat:d['revision']},'confirmation':'DELETE'}
    assert client.post(base+'/livechats/delete',json=body,headers=h).status_code==200
    assert client.get(base+'/support/exports/'+job+'/download').status_code==409
    with app.state.session_factory() as db:
        record=db.get(SupportJob,uuid.UUID(job));assert record.file_data is None and record.output=={}


def test_deleting_deduplicated_message_copies_does_not_change_usage(client, repository, app):
    from app.workspace_settings.usage import source_query
    base,chat,h,worker,wh,uid,team,path=repository;ws=uuid.UUID(base.split('/')[3]);request_id=str(uuid.uuid4())
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat))
        other=AgentConversation(workspace_id=ws,agent_id=row.agent_id,user_id=row.user_id,agent_name=row.agent_name,workflow_state={'language':'en'},live_context=dict(row.live_context),live_status='closed');db.add(other);db.flush();other_id=str(other.id)
        for identifier in [row.id,other.id]:db.add(AgentMessage(conversation_id=identifier,role='user',content='Meter once',message_data={'source_request_id':request_id}))
        db.commit();before=db.scalar(select(func.sum(source_query(ws).c.units)))
    for identifier in [chat,other_id]:
        d=client.get(base+'/livechats/'+identifier).json()
        result=client.post(base+'/livechats/delete',json={'ids':[identifier],'revisions':{identifier:d['revision']},'confirmation':'DELETE'},headers=h)
        assert result.status_code==200,result.text
        with app.state.session_factory() as db:assert db.scalar(select(func.sum(source_query(ws).c.units)))==before


def test_grouped_fields_effective_tags_and_group_filters(client, repository):
    from test_custom_fields import body as field_body
    base,chat,h,worker,wh,uid,team,path=repository
    field=client.post(base+'/custom-fields',json=field_body(),headers=h).json()
    d=client.get(base+'/livechats/'+chat).json()
    result=client.post(base+'/support/chats/'+chat+'/fields',json={'expected_revision':d['revision'],'values':{field['key']:'mobile_banking'}},headers=h)
    assert result.status_code==200,result.text
    group=client.post(base+'/ticket-settings/groups',json={'name':'Products'},headers=h).json()
    tag=client.post(base+'/ticket-settings/tags',json={'name':'Banking','color':'#123456','group_ids':[group['id']]},headers=h).json()
    applied=client.post(base+'/support/chats/'+chat+'/labels',json={'tag_ids':[tag['id']],'group_ids':[],'mode':'replace'},headers=h)
    assert applied.status_code==200,applied.text
    for filters in [{'custom_key':field['key'],'custom_value':'mobile_banking'},{'tag':tag['id']},{'group':group['id']}]:
        response=client.get(base+'/livechats',params=filters);assert response.status_code==200,response.text
        assert response.json()['total']==1
    assert client.get(base+'/livechats',params={'custom_key':field['key'],'custom_value':'accounts'}).json()['total']==0


def test_foreign_note_and_unauthenticated_requests_are_rejected(client, repository, app):
    base,chat,h,worker,wh,uid,team,path=repository
    other_id=uuid.uuid4()
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat))
        other=AgentConversation(id=other_id,workspace_id=row.workspace_id,agent_id=row.agent_id,user_id=row.user_id,agent_name=row.agent_name,live_status='closed',workflow_state={},live_context={});db.add(other);db.flush()
        note=LivechatNote(conversation_id=other.id,author_name='Agent',content='Foreign note',color='#ffffff');db.add(note);db.commit();note_id=str(note.id)
    result=client.post(base+'/livechats/'+chat+'/notes/'+note_id,json={'content':'Cannot change','expected_revision':1},headers=h)
    assert result.status_code==404
    client.cookies.clear()
    assert client.get(base+'/livechats').status_code==401
    assert client.get(base+'/livechats/'+chat).status_code==401


def test_assignment_displays_current_alias_and_preserves_priority(client, repository, app):
    from test_my_profile import alias
    base,chat,h,worker,wh,uid,team,path=repository
    chosen=alias(worker,wh,is_default='true')
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat));row.live_context={**row.live_context,'priority':True,'priority_type':'ignore_limit'};db.commit()
    d=client.get(base+'/livechats/'+chat).json()
    transferred=client.post(base+'/livechats/'+chat+'/transfer',json={'expected_revision':d['revision'],'user_id':uid,'team_ids':[team['id']]},headers=h)
    assert transferred.status_code==200,transferred.text
    d=client.get(base+'/livechats/'+chat).json()
    assert d['alias_name']==chosen['name'] and d['priority'] and d['priority_type']=='ignore_limit'


def test_team_transfer_honors_legacy_automatic_assignment(client, repository):
    base,chat,h,worker,wh,uid,team,path=repository
    assignment=client.get(base+'/chat-assignment').json()
    assert client.post(base+'/chat-assignment',json={'expected_revision':assignment['revision'],'mode':'round_robin','max_active_chats':3},headers=h).status_code==200
    d=client.get(base+'/livechats/'+chat).json()
    result=client.post(base+'/livechats/'+chat+'/transfer',json={'expected_revision':d['revision'],'team_ids':[team['id']]},headers=h)
    assert result.status_code==200,result.text
    d=client.get(base+'/livechats/'+chat).json();assert d['assigned_user_id']==uid and d['status']=='open'
