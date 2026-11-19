"""CRM permission boundaries, real history, private notes and safe deletion."""
import csv,io,uuid
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import SupportPerson,PersonNote,AgentConversation,AgentMessage,SupportTicket,SupportJob,Membership,WorkspaceRole,SupportTeam,Teamspace,RecordLabels
from app.teamspace import jobs
from test_workflows import workflow,event
from test_teamspace_operations import ops,details,tags,labels,process,export
from test_workspace_settings import custom_actor

@pytest.fixture
def crm(client,workflow):
    path,_,h=workflow;root=path.rsplit('/agents/',1)[0];o=client.get(root+'/people/options');assert o.status_code==200,o.text
    return root,h,o.json()

def add(client,crm,**extra):
    root,h,_=crm;body={'client_request_id':str(uuid.uuid4()),'name':'Customer Test','email':'customer@example.com','phone':'+254700000001',**extra}
    result=client.post(root+'/people',json=body,headers=h);assert result.status_code==201,result.text
    return result.json(),body

def detail(client,crm,id):
    response=client.get(crm[0]+'/people/'+id);assert response.status_code==200,response.text;return response.json()


def test_registration_idempotent_edit_revision_country_and_legacy_routes(client,crm):
    root,h,o=crm;person,body=add(client,crm,country='ke',location='Nairobi')
    assert person['country']=='KE' and person['phone']=='+254700000001'
    again=client.post(root+'/people',json=body,headers=h);assert again.status_code==201 and again.json()['id']==person['id']
    assert client.post(root+'/people',json={**body,'name':'Different'},headers=h).status_code==409
    assert client.get(root+'/people').json()['total']==1
    assert client.get(root+'/support/people').json()['people'][0]['id']==person['id']
    saved=client.post(root+'/people/'+person['id'],json={'name':'Changed','expected_revision':person['revision']},headers=h);assert saved.status_code==200,saved.text
    assert saved.json()['name']=='Changed' and saved.json()['email']=='customer@example.com'
    assert client.post(root+'/people/'+person['id'],json={'name':'stale','expected_revision':1},headers=h).status_code==409
    assert 'create_payload_hash' not in person and 'client_request_id' not in person

@pytest.mark.parametrize('change',[{'email':'bad email'},{'country':'XX'},{'phone':'not-a-number'},{'name':'bad\r\nname'},{'location':'bad\nlocation'},{'external_id':'bad\x00id'},{'teamspace_id':str(uuid.uuid4())}])
def test_invalid_registration_references_and_contact_fields_rejected(client,crm,change):
    root,h,_=crm;result=client.post(root+'/people',json={'name':'Person','email':'person@example.com',**change},headers=h)
    assert result.status_code in {403,404,422},result.text
    assert client.get(root+'/people').json()['total']==0


def test_blank_registration_csrf_and_external_id_collision(client,crm):
    root,h,_=crm
    assert client.post(root+'/people',json={'name':'  '},headers=h).status_code==422
    assert client.post(root+'/people',json={'name':'No CSRF'}).status_code==403
    add(client,crm,external_id='CRM-1')
    assert client.post(root+'/people',json={'name':'Duplicate','external_id':'CRM-1'},headers=h).status_code==409


def test_search_256_literal_hidden_fields_country_dates_and_paging(client,crm,app):
    root,h,_=crm;p,_=add(client,crm,name='100% _ literal',country='KE',location='Nairobi');q,_=add(client,crm,name='Another',email='other@example.com',country='TZ')
    assert client.get(root+'/people',params={'q':p['id']}).json()['total']==1
    assert client.get(root+'/people',params={'q':'% _'}).json()['total']==1
    assert client.get(root+'/people',params={'q':'+254700000001'}).json()['total']==2
    assert client.get(root+'/people',params={'q':'x'*257}).status_code==422
    assert client.get(root+'/support/people',params={'q':'x'*257}).status_code==422
    with app.state.session_factory() as db:
        row=db.get(SupportPerson,uuid.UUID(p['id']));row.country='ke';db.commit()
    country=client.get(root+'/people',params={'country':'ke'}).json();assert country['total']==1 and country['people'][0]['country']=='KE'
    assert client.get(root+'/people',params={'period':'today'}).json()['total']==2
    assert client.get(root+'/people',params={'created_from':'2026-12-01','created_to':'2026-01-01','period':'custom'}).status_code==422
    assert client.get(root+'/people',params={'timezone':'not/a-zone'}).status_code==422
    first=client.get(root+'/people',params={'limit':1}).json();second=client.get(root+'/people',params={'limit':1,'offset':1}).json()
    assert first['total']==2 and first['has_more'] and not second['has_more'] and first['people'][0]['id']!=second['people'][0]['id']


def test_private_notes_colours_revision_and_scope_never_enter_customer_history_or_analysis(client,crm,app):
    root,h,_=crm;person,_=add(client,crm);url=root+'/people/'+person['id']
    created=client.post(url+'/notes',json={'content':'PRIVATE CRM NOTE','color':'#123456'},headers=h);assert created.status_code==201,created.text
    n=created.json()['notes'][0];body={'content':'PRIVATE EDIT','color':'#abcdef','expected_revision':n['revision']}
    saved=client.post(url+'/notes/'+n['id'],json=body,headers=h);assert saved.status_code==200,saved.text
    assert saved.json()['notes'][0]['color']=='#abcdef'
    assert client.post(url+'/notes/'+n['id'],json=body,headers=h).status_code==409
    assert client.post(url+'/notes',json={'content':'bad colour','color':'red'},headers=h).status_code==422
    assert client.post(url+'/notes',json={'content':'  '},headers=h).status_code==422
    assert client.get(url+'/conversations').json()['total']==0
    with app.state.session_factory() as db:
        row=db.get(SupportPerson,uuid.UUID(person['id']));assert 'PRIVATE' not in jobs.transcript(db,'people',row)
    assert client.post(url+'/notes/'+n['id']+'/delete',json={'expected_revision':1},headers=h).status_code==409
    assert client.post(url+'/notes/'+n['id']+'/delete',json={'expected_revision':2},headers=h).status_code==200
    assert detail(client,crm,person['id'])['notes']==[]


def test_view_manage_and_contact_field_permissions_are_independent(client,crm,app):
    root,h,_=crm;p,_=add(client,crm,country='KE');user,role=custom_actor(app,root.split('/')[3],['view_people_all','view_people_name'])
    row=detail(client,crm,p['id'])['person'];assert row['name']=='Customer Test' and 'email' not in row and 'phone' not in row and not row['can_manage']
    assert client.get(root+'/people',params={'q':'customer@example.com'}).json()['total']==0
    assert client.get(root+'/people',params={'country':'KE'}).json()['total']==0
    assert client.post(root+'/people/'+p['id']+'/notes',json={'content':'Denied'},headers=h).status_code==404
    assert client.post(root+'/people/delete',json={'ids':[p['id']],'revisions':{p['id']:p['revision']}},headers=h).status_code==404
    with app.state.session_factory() as db:
        r=db.get(WorkspaceRole,role);r.permissions=[*r.permissions,'manage_people_all','manage_people_name'];db.commit()
    saved=client.post(root+'/people/'+p['id'],json={'name':'Allowed','expected_revision':p['revision']},headers=h);assert saved.status_code==200,saved.text
    assert client.post(root+'/people/'+p['id'],json={'email':'hidden@example.com','expected_revision':saved.json()['revision']},headers=h).status_code==403


def test_filters_use_people_fields_default_values_and_effective_tag_groups(client,crm,app):
    root,h,_=crm;p,_=add(client,crm)
    f=client.post(root+'/custom-fields',json={'scope':'person','type':'number','name':'Score','options':[],'default_value':12},headers=h);assert f.status_code==201,f.text;f=f.json()
    assert client.get(root+'/people',params={'custom_key':f['key'],'custom_value':'12'}).json()['total']==1
    assert client.get(root+'/people',params={'custom_key':'missing','custom_value':'x'}).status_code==422
    group=client.post(root+'/ticket-settings/groups',json={'name':'CRM group'},headers=h).json()
    tag=client.post(root+'/ticket-settings/tags',json={'name':'CRM tag','color':'#123456','group_ids':[group['id']]},headers=h).json()
    assert client.post(root+'/support/people/'+p['id']+'/labels',json={'tag_ids':[tag['id']],'group_ids':[],'mode':'replace'},headers=h).status_code==200
    filtered=client.get(root+'/people',params={'group':group['id']}).json();assert filtered['total']==1 and filtered['people'][0]['groups'][0]['id']==group['id']
    assert client.get(root+'/people',params={'tag':tag['id']}).json()['total']==1


def test_history_and_channel_filters_check_conversation_scope_and_person_binding(client,crm,workflow,app):
    root,h,_=crm;started=event(client,workflow[0],h).json();chat=started['conversation_id']
    with app.state.session_factory() as db:
        c=db.get(AgentConversation,uuid.UUID(chat));pid=str(c.person_id);c.workflow_state={**c.workflow_state,'channel':'whatsapp'}
        db.add(AgentMessage(conversation_id=c.id,role='user',content='Customer conversation message'));db.commit()
    assert client.get(root+'/people',params={'channel':'whatsapp'}).json()['total']==1
    assert client.get(root+'/people',params={'channel':'ticket'}).json()['total']==0
    history=client.get(root+'/people/'+pid+'/conversations');assert history.status_code==200,history.text
    assert history.json()['items'][0]['id']==chat
    transcript=client.get(root+'/people/'+pid+'/conversations/chats/'+chat);assert transcript.status_code==200 and 'Customer conversation message' in transcript.text
    other,_=add(client,crm,name='Unrelated')
    assert client.get(root+'/people/'+other['id']+'/conversations/chats/'+chat).status_code==404
    custom_actor(app,root.split('/')[3],['view_people_all','view_people_name'])
    assert client.get(root+'/people/'+pid+'/conversations').json()['total']==0
    assert client.get(root+'/people/'+pid+'/conversations/chats/'+chat).status_code==404
    assert client.get(root+'/people',params={'channel':'whatsapp'}).json()['total']==0


def test_ticket_email_history_excludes_internal_notes_and_unsent_messages(client,crm,app):
    from test_ticket_workspace import create as ticket_create,reply,CaptureTickets
    root,h,_=crm;app.state.ticket_mailer=CaptureTickets();desk=(root,h,{})
    d,_=ticket_create(client,desk);pid=d['ticket']['person_id'];tid=d['ticket']['id']
    assert client.post(root+'/tickets/'+tid+'/private-notes',json={'content':'PRIVATE TICKET NOTE'},headers=h).status_code==201
    d=client.get(root+'/tickets/'+tid).json();sent=reply(client,desk,d);assert sent.status_code==200,sent.text
    assert client.get(root+'/people',params={'channel':'ticket'}).json()['total']==1
    history=client.get(root+'/people/'+pid+'/conversations/tickets/'+tid);assert history.status_code==200 and 'support reply' in history.text and 'PRIVATE TICKET' not in history.text


def test_delete_is_atomic_revision_safe_retains_chats_tickets_and_does_not_recreate_on_read(client,crm,workflow,app):
    from app.workspace_settings.usage import source_query
    root,h,_=crm;started=event(client,workflow[0],h).json();chat=started['conversation_id']
    with app.state.session_factory() as db:
        c=db.get(AgentConversation,uuid.UUID(chat));pid=str(c.person_id)
        db.add(AgentMessage(conversation_id=c.id,role='user',content='Historical customer message'))
        db.add(SupportTicket(workspace_id=c.workspace_id,person_id=c.person_id,conversation_id=c.id,creator_id=c.user_id,action_node_id=uuid.uuid4(),execution_id=uuid.uuid4(),number=1,reference='PTX-000001',agent_name='Support',subject='History',full_name='Customer',email='customer@example.com'));db.commit()
        count=db.scalar(select(func.sum(source_query(c.workspace_id).c.units)))
    saved=client.post(root+'/people/'+pid+'/notes',json={'content':'PRIVATE DELETE'},headers=h).json();revision=saved['person']['revision']
    unknown=str(uuid.uuid4());body={'ids':[pid,unknown],'revisions':{pid:revision,unknown:1}}
    assert client.post(root+'/people/delete',json=body,headers=h).status_code==404
    assert client.get(root+'/people/'+pid).status_code==200
    assert client.post(root+'/people/delete',json={'ids':[pid],'revisions':{pid:1}},headers=h).status_code==409
    result=client.post(root+'/people/delete',json={'ids':[pid],'revisions':{pid:revision}},headers=h);assert result.status_code==200,result.text
    assert client.get(root+'/people/'+pid).status_code==404
    from app.teamspace.assignment import priority_for
    with app.state.session_factory() as db:
        c=db.get(AgentConversation,uuid.UUID(chat));assert not priority_for(db,c.workspace_id,c,{'priority_enabled':True,'tag_ids':[],'group_ids':[]})
        assert db.scalar(select(func.count()).select_from(SupportPerson))==0
    # Staff reads and exports must not re-register deleted CRM entries.
    read=client.get(root+'/support/chats/'+chat+'/details');assert read.status_code==200 and read.json()['person'] is None
    assert client.get(root+'/people').json()['total']==0
    with app.state.session_factory() as db:
        assert db.get(AgentConversation,uuid.UUID(chat)).person_id is None
        assert db.scalar(select(SupportTicket)).person_id is None
        assert db.scalar(select(func.count()).select_from(PersonNote))==0
        assert db.scalar(select(func.sum(source_query(uuid.UUID(root.split('/')[3])).c.units)))==count
    exported=client.post(root+'/support/exports',json={'request_id':str(uuid.uuid4()),'kind':'chats','ids':[chat],'fields':['id','person'],'transcript':True,'timezone':'Africa/Nairobi'},headers=h);assert exported.status_code==202,exported.text
    process(app)
    result=client.get(root+'/support/exports/'+exported.json()['id']+'/download');assert result.status_code==200 and 'Historical customer message' in result.text
    assert client.get(root+'/people').json()['total']==0
    # A later real incoming event may register the person again, with a fresh ID.
    from test_workflows import event as send_event
    response=send_event(client,workflow[0],h,'message_received',chat,content='A new message');assert response.status_code==200,response.text
    with app.state.session_factory() as db:assert str(db.get(AgentConversation,uuid.UUID(chat)).person_id)!=pid


def test_people_exports_include_typed_custom_fields_defaults_and_no_private_notes(client,crm,app):
    root,h,_=crm;p,_=add(client,crm)
    field=client.post(root+'/custom-fields',json={'scope':'person','name':'CRM score','type':'number','options':[],'default_value':7},headers=h).json()
    assert client.post(root+'/people/'+p['id']+'/notes',json={'content':'PRIVATE EXPORT NOTE'},headers=h).status_code==201
    body={'request_id':str(uuid.uuid4()),'kind':'people','ids':[p['id']],'fields':['id','name','custom_fields','custom:'+field['key']],'transcript':False,'timezone':'Africa/Nairobi'}
    response=client.post(root+'/support/exports',json=body,headers=h);assert response.status_code==202,response.text
    process(app)
    download=client.get(root+'/support/exports/'+response.json()['id']+'/download');assert download.status_code==200,download.text
    rows=list(csv.reader(io.StringIO(download.content.decode('utf-8-sig'))));assert rows[1][-1]=='7' and 'PRIVATE EXPORT' not in download.text
    assert field['key'] in rows[1][2]
    person=detail(client,crm,p['id'])['person']
    assert client.post(root+'/people/delete',json={'ids':[p['id']],'revisions':{p['id']:person['revision']}},headers=h).status_code==200
    assert client.get(root+'/support/exports/'+response.json()['id']+'/download').status_code==409


def test_team_people_roles_include_linked_tickets_but_respect_teamspace_and_record_grants(client,crm,app):
    from test_ticket_workspace import create as ticket_create
    root,h,_=crm;workspace=uuid.UUID(root.split('/')[3]);owner=client.get('/api/auth/me').json()['user']['id']
    team=client.post(root+'/teams',json={'name':'CRM team','description':'','alternative_names':[],'member_ids':[owner]},headers=h).json()
    d,_=ticket_create(client,(root,h,{}),team_ids=[team['id']]);pid=d['ticket']['person_id']
    with app.state.session_factory() as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==uuid.UUID(owner)));member.role='member'
        base=WorkspaceRole(workspace_id=workspace,name='Base access',name_key='base-access',kind='workspace',permissions=[]);role=WorkspaceRole(workspace_id=workspace,name='CRM team access',name_key='crm-team',kind='team',permissions=['view_people_all','manage_people_all','view_people_name','view_people_email']);db.add_all([base,role]);db.flush();member.custom_role_id=base.id;db.get(SupportTeam,uuid.UUID(team['id'])).role_id=role.id;db.commit()
    listing=client.get(root+'/people');assert listing.status_code==200 and listing.json()['total']==1,listing.text
    assert detail(client,crm,pid)['person']['can_manage']
    # People access does not grant the underlying ticket's history.
    assert client.get(root+'/people/'+pid+'/conversations').json()['total']==0
    with app.state.session_factory() as db:
        member=db.scalar(select(Membership).where(Membership.workspace_id==workspace,Membership.user_id==uuid.UUID(owner)));member.teamspace_ids=[];db.commit()
    assert client.get(root+'/people').status_code==403
    assert client.get(root+'/people/'+pid).status_code==404


def test_ai_handoff_channel_uses_real_transfer_metadata(client,crm,workflow,app):
    from app.models import AIOutgoingSession
    root,h,_=crm;started=event(client,workflow[0],h).json();chat=started['conversation_id']
    with app.state.session_factory() as db:
        c=db.get(AgentConversation,uuid.UUID(chat));pid=str(c.person_id)
        db.add(AIOutgoingSession(conversation_id=c.id,transfer_id=uuid.uuid4(),target_id=uuid.uuid4(),target_name='Receiving AI',domain='https://receiver.example',status='starting',token_ciphertext='',start_payload={},pending={},last_result={},error='',updated_at=utcnow()));db.commit()
    result=client.get(root+'/people',params={'channel':'ai_agent_handoff'});assert result.status_code==200 and result.json()['total']==1,result.text
    assert 'ai_agent_handoff' in {v['key'] for v in detail(client,crm,pid)['person']['channels']}
