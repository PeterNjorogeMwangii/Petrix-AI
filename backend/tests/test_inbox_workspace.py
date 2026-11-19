"""Unified Inbox behavior and privacy boundaries against PostgreSQL."""
import io,json,uuid
from datetime import timedelta
import pytest
from pydantic import SecretStr
from sqlalchemy import select,func
from app.core.security import utcnow
from app.models import (AgentConversation,AgentMessage,SupportTicket,SupportPerson,SupportTeam,SupportJob,User,Membership,
    InboxView,InboxAttachment,AgentAttachment,TicketEmail,TicketInboundEmail,WorkspaceBilling,CopilotKnowledge,UsageEvent)
from app.teamspace import jobs
from app.teamspace.ai import AIResult
from test_workflows import workflow
from test_live_agent_handoff import workers,available,support
from test_livechats import repository
from test_ticket_workspace import desk,create,reply
from test_teamspace_operations import ops,setting,tags,labels
from test_workspace_settings import custom_actor

@pytest.fixture
def inbox(client,repository,desk,app):
    root,chat,h,worker,wh,uid,team,path=repository
    ticket,_=create(client,desk,subject='Banking account help',description='EMAIL reference about savings')
    owner=client.get('/api/auth/me').json()['user']['id'];space=client.get(root+'/inbox/workspace-options').json()['teamspaces'][0]['id']
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(chat));row.workflow_state={**row.workflow_state,'channel':'webchat'}
        person=db.get(SupportPerson,row.person_id);person.name='Chat Customer'
        db.add(AgentMessage(conversation_id=row.id,role='user',content='What is KCB M-PESA?'))
        t=db.get(SupportTicket,uuid.UUID(ticket['ticket']['id']));t.assignee_type='user';t.assignee_id=uuid.UUID(owner);t.assignee_name='Owner'
        db.commit()
    return {'root':root,'chat':chat,'ticket':ticket['ticket']['id'],'h':h,'worker':worker,'wh':wh,'uid':uid,'owner':owner,'space':space,'team':team,'path':path}

def queue(client,f,**params):
    response=client.get(f['root']+'/inbox/conversations',params={'section':'conversations',**params});assert response.status_code==200,response.text;return response.json()

def detail(client,f,kind='chats',identifier=None):
    response=client.get(f['root']+'/inbox/conversations/'+kind+'/'+(identifier or f['chat']));assert response.status_code==200,response.text;return response.json()

def add_view(client,f,name='Private Banking',**extra):
    body={'name':name,'scope':'private','teamspace_id':f['space'],'filters':{},**extra}
    response=client.post(f['root']+'/inbox/views',json=body,headers=f['h']);assert response.status_code==201,response.text;return response.json()

def op(f):return f['root'],f['space'],f['chat'],f['h']

class AI:
    available=True
    def __init__(self):self.calls=[]
    def generate(self,system,content,model=None):self.calls.append((system,content,model));return AIResult(True,text='Reviewed suggestion based on supplied evidence.')


def test_unified_list_search_default_views_and_private_note_boundary(client,inbox):
    f=inbox;all_items=queue(client,f);assert {i['kind'] for i in all_items['items']}=={'chats','tickets'}
    assert queue(client,f,q='kcb m-pesa')['total']==1
    assert queue(client,f,q='EMAIL reference')['items'][0]['kind']=='tickets'
    assert queue(client,f,section='mine')['items'][0]['id']==f['ticket']
    assert queue(client,f,section='livechats')['items'][0]['id']==f['chat']
    assert queue(client,f,section='tickets')['items'][0]['id']==f['ticket']
    note=client.post(f['root']+'/inbox/conversations/chats/'+f['chat']+'/notes',json={'content':'STAFF ONLY secret'},headers=f['h']);assert note.status_code==200,note.text
    assert note.json()['notes'][0]['content']=='STAFF ONLY secret'
    assert queue(client,f,q='STAFF ONLY')['total']==0
    public=client.get(f['path']+'/tester/conversations/'+f['chat']);assert public.status_code==200 and 'STAFF ONLY' not in public.text
    assert client.get(f['root']+'/inbox/conversations',params={'q':'x'*257}).status_code==422
    assert client.get(f['root']+'/inbox/conversations',params={'filters':'{"unknown":true}'}).status_code==422


def test_search_snapshot_excludes_new_conversations_until_search_clears(client,inbox,desk):
    f=inbox;first=queue(client,f,q='EMAIL reference');assert first['total']==1
    create(client,desk,subject='New match',description='EMAIL reference')
    assert queue(client,f,q='EMAIL reference',search_at=first['search_at'])['total']==1
    assert queue(client,f,q='EMAIL reference')['total']==2
    assert client.get(f['root']+'/inbox/conversations',params={'search_at':utcnow().replace(tzinfo=None).isoformat()}).status_code==422


def test_private_shared_views_stack_and_pins_are_personal_revisioned(client,inbox):
    f=inbox;a=add_view(client,f,filters={'kind':'tickets'});b=add_view(client,f,name='Open tickets',scope='teamspace',filters={'statuses':['open']})
    assert queue(client,f,views=[a['key'],b['key']])['items'][0]['kind']=='tickets'
    other=f['worker'].get(f['root']+'/inbox/workspace-options').json();assert a['id'] not in [v['id'] for v in other['views']] and b['id'] in [v['id'] for v in other['views']]
    assert f['worker'].get(f['root']+'/inbox/conversations',params={'views':[a['key']]}).status_code==404
    preferences=client.get(f['root']+'/inbox/workspace-options').json()['preferences']
    body={'expected_revision':preferences['revision'],'pinned':['mine',a['key']],'sort':'wait','direction':'asc'}
    response=client.post(f['root']+'/inbox/preferences',json=body,headers=f['h']);assert response.status_code==200,response.text
    assert client.get(f['root']+'/inbox/workspace-options').json()['preferences']['pinned']==['mine',a['key']]
    assert client.post(f['root']+'/inbox/preferences',json=body,headers=f['h']).status_code==409
    assert f['worker'].get(f['root']+'/inbox/workspace-options').json()['preferences']['pinned']==[]
    assert client.post(f['root']+'/inbox/views/'+a['id']+'/delete',json={'expected_revision':1},headers=f['h']).status_code==200
    assert client.get(f['root']+'/inbox/workspace-options').json()['preferences']['pinned']==['mine']
    assert queue(client,f)['total']==2


def test_views_cannot_cross_teamspace_or_workspace_or_elevate_grants(client,inbox,app):
    f=inbox;view=add_view(client,f,scope='teamspace')
    worker=f['worker'];wh=f['wh']
    assert worker.post(f['root']+'/inbox/views',json={'name':'Shared by member','scope':'teamspace','teamspace_id':f['space'],'filters':{}},headers=wh).status_code==403
    assert worker.post(f['root']+'/inbox/views/'+view['id']+'/delete',json={'expected_revision':1},headers=wh).status_code==403
    assert client.post(f['root']+'/inbox/views',json={'name':'Foreign','teamspace_id':str(uuid.uuid4()),'filters':{}},headers=f['h']).status_code==404
    assert client.post(f['root']+'/inbox/views',json={'name':'Bad ref','teamspace_id':f['space'],'filters':{'team_id':str(uuid.uuid4())}},headers=f['h']).status_code==422
    custom_actor(app,f['root'].split('/')[3],[])
    other=client
    assert other.get(f['root']+'/inbox/conversations',params={'section':'conversations','views':[view['key']]}).json()['total']==0
    assert other.get(f['root']+'/inbox/conversations/chats/'+f['chat']).status_code==404


@pytest.mark.parametrize('sort',['created','latest','wait','handoff'])
@pytest.mark.parametrize('direction',['asc','desc'])
def test_sorting_is_stable_and_priority_group_overrides_sort(client,inbox,app,sort,direction):
    f=inbox;tag,_,_,_=tags(client,op(f))
    with app.state.session_factory() as db:person=str(db.get(AgentConversation,uuid.UUID(f['chat'])).person_id)
    labels(client,op(f),'people',person,[tag['id']])
    setting(client,op(f),'assignment',{'section':'priority','priority_enabled':True,'tag_ids':[tag['id']]})
    response=queue(client,f,sort=sort,direction=direction);assert response['items'][0]['id']==f['chat'] and response['items'][0]['priority']
    labels(client,op(f),'people',person,[])
    assert not next(i for i in queue(client,f,sort=sort,direction=direction)['items'] if i['id']==f['chat'])['priority']


def test_queue_filters_permissions_before_paging_beyond_old_500_cap(client,inbox,app):
    f=inbox;custom_actor(app,f['root'].split('/')[3],['view_livechats_own']);actor=client
    actor_id=actor.get('/api/auth/me').json()['user']['id']
    with app.state.session_factory() as db:
        source=db.get(AgentConversation,uuid.UUID(f['chat']))
        for index in range(505):db.add(AgentConversation(workspace_id=source.workspace_id,agent_id=source.agent_id,user_id=source.user_id,agent_name='Other record',workflow_state={'channel':'webchat'},live_status='active',live_assigned_user_id=uuid.UUID(f['uid']),live_context={'routing':{'team_ids':[]}}))
        wanted=AgentConversation(workspace_id=source.workspace_id,agent_id=source.agent_id,user_id=source.user_id,agent_name='Visible older record',workflow_state={'channel':'webchat'},created_at=utcnow()-timedelta(days=30),live_status='active',live_assigned_user_id=uuid.UUID(actor_id),live_context={'routing':{'team_ids':[]}});db.add(wanted);db.commit();wanted_id=str(wanted.id)
    response=actor.get(f['root']+'/inbox/conversations',params={'section':'conversations','limit':1});assert response.status_code==200,response.text
    assert response.json()['total']==1 and response.json()['items'][0]['id']==wanted_id


def test_chat_takeover_send_atomic_alias_attachment_and_idempotent_budget(client,inbox,app):
    f=inbox;worker=f['worker'];wh=f['wh'];d=detail(worker,f)
    uploaded=worker.post(f['root']+'/inbox/chats/'+f['chat']+'/attachments',files={'files':('voice.webm',b'fake-audio','audio/webm')},headers=wh);assert uploaded.status_code==201,uploaded.text
    file=uploaded.json()['attachments'][0]
    assert client.get(file['url']).status_code==404
    body={'expected_revision':d['revision'],'content':'Human reply','client_message_id':str(uuid.uuid4()),'attachment_ids':[file['id']],'takeover':True}
    response=worker.post(f['root']+'/inbox/chats/'+f['chat']+'/reply',json=body,headers=wh);assert response.status_code==200,response.text
    assert response.json()['assigned_user_id']==f['uid'] and response.json()['status']=='active'
    delivered=response.json()['messages'][-1]['attachments'][0]
    assert delivered['id']==file['id'] and '/tester/attachments/' in delivered['url']
    assert worker.get(delivered['url']).content==b'fake-audio'
    with app.state.session_factory() as db:
        assert db.get(AgentAttachment,uuid.UUID(file['id'])).data==b'fake-audio'
        assert db.get(InboxAttachment,uuid.UUID(file['id'])) is None
    assert worker.get(file['url']).content==b'fake-audio'
    assert client.get(file['url']).status_code==200
    again=worker.post(f['root']+'/inbox/chats/'+f['chat']+'/reply',json=body,headers=wh);assert again.status_code==200
    assert len([m for m in again.json()['messages'] if m['content']=='Human reply'])==1
    assert worker.post(f['root']+'/inbox/chats/'+f['chat']+'/reply',json={**body,'content':'Changed'},headers=wh).status_code==409
    assert worker.post(f['root']+'/inbox/chats/'+f['chat']+'/attachments/'+file['id']+'/delete',json={},headers=wh).status_code==404


def exhaust(app,f):
    with app.state.session_factory() as db:
        from app.workspace_settings.billing import billing
        ledger=billing(db,uuid.UUID(f['root'].split('/')[3]),True);ledger.snapshot={**ledger.snapshot,'included':0};db.commit()


def test_budget_failure_does_not_take_over_or_append_and_legacy_ticket_send_is_blocked(client,inbox,app,desk):
    f=inbox;d=detail(f['worker'],f);exhaust(app,f)
    body={'expected_revision':d['revision'],'content':'Must not be sent','client_message_id':str(uuid.uuid4()),'takeover':True}
    result=f['worker'].post(f['root']+'/inbox/chats/'+f['chat']+'/reply',json=body,headers=f['wh']);assert result.status_code==409 and 'interactions' in result.text
    after=detail(client,f);assert after['assigned_user_id']==d['assigned_user_id'] and after['revision']==d['revision']
    assert all(m['content']!='Must not be sent' for m in after['messages'])
    ticket=client.get(f['root']+'/tickets/'+f['ticket']).json();assert reply(client,desk,ticket).status_code==409
    assert not client.get(f['root']+'/tickets/'+f['ticket']).json()['emails']


def test_ticket_attachment_cc_persistence_and_sent_delivery_metadata(client,inbox,app,desk):
    f=inbox
    class Mailer:
        def __init__(self):self.deliveries=[]
        def send_ticket(self,row):self.deliveries.append((row.body_text,row.cc_recipients,[(a.file_name,a.data) for a in row.attachments]))
    mailer=Mailer();app.state.ticket_mailer=mailer
    up=client.post(f['root']+'/inbox/tickets/'+f['ticket']+'/attachments',files={'files':('answer.txt',b'answer data','text/plain')},headers=f['h']);assert up.status_code==201,up.text
    asset=up.json()['attachments'][0];d=client.get(f['root']+'/tickets/'+f['ticket']).json()
    response=reply(client,desk,d,cc=['copy@example.com'],attachment_ids=[asset['id']]);assert response.status_code==200,response.text
    assert mailer.deliveries[-1][1:]==(['copy@example.com'],[('answer.txt',b'answer data')])
    response=reply(client,desk,response.json());assert response.status_code==200,response.text
    assert mailer.deliveries[-1][1]==['copy@example.com']
    data=detail(client,f,'tickets',f['ticket']);assert data['cc']==['copy@example.com'] and data['messages'][-1]['delivery_status']=='sent'
    assert all(m.get('source')!='internal_note' for m in data['messages'])


@pytest.mark.parametrize('mode',['draft','ask','mediate','enhance','translate'])
def test_private_assistance_chats_and_tickets_never_sends_and_counts_correct_category(client,inbox,app,mode):
    f=inbox;ai=AI()
    for kind,id in [('chats',f['chat']),('tickets',f['ticket'])]:
        before=detail(client,f,kind,id);body={'request_id':str(uuid.uuid4()),'mode':mode,'question':'Summarise this case','draft':'Please assist me','language':'sw','style':'formal'}
        response=client.post(f['root']+'/inbox/conversations/'+kind+'/'+id+'/assistant',json=body,headers=f['h']);assert response.status_code==202,response.text
        assert jobs.run_one(app.state.session_factory,app.state.settings,ai)
        job=client.get(f['root']+'/support/jobs/'+response.json()['id']);assert job.status_code==200,job.text
        assert job.json()['status']=='completed',job.text
        assert job.json()['output']['text']=='Reviewed suggestion based on supplied evidence.'
        after=detail(client,f,kind,id);assert before['messages']==after['messages']
        assert f['worker'].get(f['root']+'/support/jobs/'+response.json()['id']).status_code in {403,404}
    with app.state.session_factory() as db:
        categories=set(db.scalars(select(UsageEvent.category)))
        assert ('translations' if mode=='translate' else 'mediation' if mode=='mediate' else 'copilot') in categories


def test_copilot_current_access_model_personality_and_dedicated_knowledge(client,inbox,app):
    f=inbox;app.state.settings.support_ai_models='private-model';ai=AI()
    setting(client,op(f),'copilot',{'all_conversations':False,'person_history':False,'current_conversation':False,'model':'private-model','personality':'Use a calm support tone.'})
    source=client.post(f['root']+'/teamspace-operations/'+f['space']+'/knowledge',data={'name':'Dedicated FAQ','kind':'snippet','content':'KCB M-PESA is a loans and savings product.'},headers=f['h']);assert source.status_code==201,source.text
    url=f['root']+'/inbox/conversations/chats/'+f['chat']+'/assistant'
    assert client.post(url,json={'request_id':str(uuid.uuid4()),'mode':'mediate','question':'Help'},headers=f['h']).status_code==409
    result=client.post(url,json={'request_id':str(uuid.uuid4()),'mode':'ask','question':'What is KCB M-PESA?'},headers=f['h']);assert result.status_code==202,result.text
    jobs.run_one(app.state.session_factory,app.state.settings,ai)
    system,content,model=ai.calls[-1];assert model=='private-model' and 'calm support' in system
    assert 'Dedicated FAQ' in content and 'loans and savings' in content and 'What is KCB' in content
    assert 'Current conversation:' not in content and 'EMAIL reference' not in content


def test_assistant_limit_csrf_idempotency_and_permission_recheck(client,inbox,app):
    f=inbox;url=f['root']+'/inbox/conversations/chats/'+f['chat']+'/assistant';body={'request_id':str(uuid.uuid4()),'mode':'draft'}
    assert client.post(url,json=body).status_code==403
    first=client.post(url,json=body,headers=f['h']);assert first.status_code==202
    again=client.post(url,json=body,headers=f['h']);assert again.json()['id']==first.json()['id']
    assert client.post(url,json={**body,'mode':'ask','question':'changed'},headers=f['h']).status_code==409
    exhaust(app,f)
    assert client.post(url,json={'request_id':str(uuid.uuid4()),'mode':'translate','draft':'Hello','language':'sw'},headers=f['h']).status_code==409
    ai=AI();jobs.run_one(app.state.session_factory,app.state.settings,ai);assert not ai.calls
    assert client.get(f['root']+'/support/jobs/'+first.json()['id']).json()['status']=='failed'


def test_attachment_draft_privacy_reference_validation_size_and_sanitized_name(client,inbox):
    f=inbox;url=f['root']+'/inbox/chats/'+f['chat']+'/attachments'
    assert client.post(url,files={'files':('file.txt',b'body','text/plain')}).status_code==403
    upload=client.post(url,files={'files':('../private.txt',b'body','text/plain')},headers=f['h']);assert upload.status_code==201,upload.text
    asset=upload.json()['attachments'][0];assert asset['name']=='private.txt'
    assert f['worker'].get(asset['url']).status_code==404
    assert client.get(asset['url']).content==b'body'
    ticket=client.get(f['root']+'/tickets/'+f['ticket']).json()
    body={'client_request_id':str(uuid.uuid4()),'expected_revision':ticket['ticket']['revision'],'content':'Wrong record','attachment_ids':[asset['id']]}
    result=client.post(f['root']+'/tickets/'+f['ticket']+'/reply',json=body,headers=f['h']);assert result.status_code==422,result.text
    assert not client.get(f['root']+'/tickets/'+f['ticket']).json()['emails']
    assert client.post(url,files={'files':('huge.txt',b'x'*(10*1024*1024+1),'text/plain')},headers=f['h']).status_code==413
    assert client.post(url+'/'+asset['id']+'/delete',json={},headers=f['h']).status_code==200
    assert client.get(asset['url']).status_code==404


def test_pdf_copilot_knowledge_is_readable_and_stays_separate_from_agent_sources(client,inbox,app):
    from test_agent_knowledge import pdf
    f=inbox;definition='KCB M-PESA is a loans and savings product exclusively offered by KCB Bank Kenya Ltd to Safaricom M-PESA customers.'
    response=client.post(f['root']+'/teamspace-operations/'+f['space']+'/knowledge',data={'name':'Bank PDF','kind':'file'},files={'file':('bank.pdf',pdf(definition),'application/pdf')},headers=f['h']);assert response.status_code==201,response.text
    assert response.json()['status']=='success'
    ai=AI();r=client.post(f['root']+'/inbox/conversations/chats/'+f['chat']+'/assistant',json={'request_id':str(uuid.uuid4()),'mode':'ask','question':'What is KCB M-PESA?'},headers=f['h']);assert r.status_code==202,r.text
    jobs.run_one(app.state.session_factory,app.state.settings,ai)
    assert definition in ai.calls[-1][1]
    with app.state.session_factory() as db:
        from app.models import AgentKnowledgeSource
        assert not db.scalar(select(func.count()).select_from(AgentKnowledgeSource))


def test_url_crawl_training_and_scheduled_retraining_are_durable_and_bounded(client,inbox,app,monkeypatch):
    from app.agent_knowledge.web import WebDocument
    from app.inbox.knowledge import schedule
    class Reader:
        def __init__(self):self.urls=[]
        def fetch(self,url):
            self.urls.append(url)
            raw=b'<p>Root banking page</p><a href="/faq">FAQ</a><a href="https://other.example/private">Outside</a>' if url.endswith('/start') else b'<p>FAQ savings content</p>'
            return WebDocument(raw,'page.html',url)
    reader=Reader();monkeypatch.setattr('app.inbox.knowledge.WebsiteReader',lambda:reader)
    f=inbox;response=client.post(f['root']+'/teamspace-operations/'+f['space']+'/knowledge',data={'name':'Public banking','kind':'url','url':'https://example.com/start','scope':'all','auto_retrain':'true','retrain_days':'7'},headers=f['h']);assert response.status_code==201,response.text
    assert response.json()['status']=='pending'
    ai=AI();assert jobs.run_one(app.state.session_factory,app.state.settings,ai)
    with app.state.session_factory() as db:
        source=db.get(CopilotKnowledge,uuid.UUID(response.json()['id']));assert source.status=='success' and 'FAQ savings content' in source.content
        assert source.options['pages']==2 and source.next_refresh_at and reader.urls==['https://example.com/start','https://example.com/faq']
        assert not ai.calls and not db.scalar(select(func.count()).select_from(UsageEvent))
        source.next_refresh_at=utcnow()-timedelta(days=1);db.commit();schedule(db);schedule(db)
        pending=db.scalar(select(func.count()).select_from(SupportJob).where(SupportJob.kind=='knowledge',SupportJob.status=='pending'));assert pending==1
    assert jobs.run_one(app.state.session_factory,app.state.settings,ai)
    assert client.get(f['root']+'/teamspace-operations/'+f['space']+'/knowledge').json()['items'][0]['status']=='success'


def test_copilot_configuration_false_current_blocks_mediation_and_unknown_model_rejected(client,inbox):
    f=inbox;url=f['root']+'/teamspace-operations/'+f['space']+'/settings/copilot';data=client.get(url).json()
    response=client.post(url,json={'expected_revision':data['revision'],'current_conversation':False},headers=f['h']);assert response.status_code==200,response.text
    bad=client.post(url,json={'expected_revision':response.json()['revision'],'model':'unapproved-model'},headers=f['h']);assert bad.status_code==422
    assert client.post(f['root']+'/inbox/conversations/chats/'+f['chat']+'/assistant',json={'request_id':str(uuid.uuid4()),'mode':'draft','mediate':True},headers=f['h']).status_code==409


def test_plan_panels_and_transfers_are_enforced_for_starter(client,inbox,app):
    from app.models import Workspace
    f=inbox;app.state.settings.stripe_secret_key=SecretStr('qa-stripe-key')
    with app.state.session_factory() as db:db.get(Workspace,uuid.UUID(f['root'].split('/')[3])).plan_name='Starter';db.commit()
    options=client.get(f['root']+'/inbox/workspace-options');assert options.status_code==200 and options.json()['max_panels']==1
    d=detail(client,f)
    assert client.post(f['root']+'/livechats/'+f['chat']+'/transfer',json={'expected_revision':d['revision'],'user_id':f['uid'],'team_ids':[]},headers=f['h']).status_code==403


def test_manager_takes_over_another_assignees_chat_with_send_in_one_transaction(client,inbox,app):
    f=inbox;worker=f['worker'];wh=f['wh'];d=detail(worker,f)
    assert worker.post(f['root']+'/inbox/chats/'+f['chat']+'/claim',json={'expected_revision':d['revision']},headers=wh).status_code==200
    with app.state.session_factory() as db:
        team=db.get(SupportTeam,uuid.UUID(f['team']['id']));team.member_ids=[f['uid'],f['owner']];db.commit()
    available(client,f['root'],f['h'])
    d=detail(client,f);assert not d['mine'] and d['can_transfer']
    body={'expected_revision':d['revision'],'client_message_id':str(uuid.uuid4()),'content':'Atomic manager reply','takeover':True}
    response=client.post(f['root']+'/inbox/chats/'+f['chat']+'/reply',json=body,headers=f['h']);assert response.status_code==200,response.text
    latest=detail(client,f);assert latest['mine'] and latest['assigned_user_id']==f['owner']
    assert latest['messages'][-1]['content']=='Atomic manager reply'


def test_human_reply_cannot_force_an_ai_owned_conversation_into_handoff(client,ops):
    root,_,chat,h=ops;data=client.get(root+'/inbox/conversations/chats/'+chat).json();assert data['read_only']
    response=client.post(root+'/inbox/chats/'+chat+'/reply',json={'expected_revision':data['revision'],'client_message_id':str(uuid.uuid4()),'content':'Not a configured handoff','takeover':True},headers=h)
    assert response.status_code==409


def test_default_views_stack_with_custom_views_using_all_conditions(client,inbox):
    f=inbox;custom=add_view(client,f,filters={'statuses':['open']})
    selected=queue(client,f,views=['mine','tickets',custom['key']]);assert selected['total']==1 and selected['items'][0]['id']==f['ticket']
    assert queue(client,f,views=['livechats','tickets'])['total']==0


def test_empty_ticket_reply_requires_an_attachment(client,inbox,desk):
    f=inbox;d=client.get(f['root']+'/tickets/'+f['ticket']).json()
    assert reply(client,desk,d,content=' ').status_code==422
    up=client.post(f['root']+'/inbox/tickets/'+f['ticket']+'/attachments',files={'files':('document.txt',b'File only','text/plain')},headers=f['h']);assert up.status_code==201
    response=reply(client,desk,d,content='',attachment_ids=[up.json()['attachments'][0]['id']]);assert response.status_code==200,response.text
    assert response.json()['emails'][-1]['status']=='sent'


def test_ticket_history_pages_latest_and_earlier_without_dropping_messages(client,inbox,app):
    f=inbox
    with app.state.session_factory() as db:
        for index in range(110):
            db.add(TicketInboundEmail(ticket_id=uuid.UUID(f['ticket']),message_id='page-'+str(index),sender='person@example.com',recipient='support@example.com',subject='History',content='Historical email '+str(index),created_at=utcnow()-timedelta(minutes=110-index)))
        db.commit()
    latest=detail(client,f,'tickets',f['ticket'])
    assert latest['has_more'] and len(latest['messages'])==100
    assert latest['messages'][0]['content']=='Historical email 10' and latest['messages'][-1]['content']=='Historical email 109'
    url=f['root']+'/inbox/conversations/tickets/'+f['ticket']
    earlier=client.get(url,params={'before':latest['messages'][0]['id']})
    assert earlier.status_code==200,earlier.text
    earlier=earlier.json();assert not earlier['has_more'] and len(earlier['messages'])==10
    assert earlier['messages'][0]['content']=='Historical email 0'
    assert len({m['id'] for m in earlier['messages']+latest['messages']})==110
    assert client.get(url,params={'before':str(uuid.uuid4())}).status_code==404


def test_copilot_retrieves_matching_late_pdf_text_instead_of_only_source_prefix():
    from app.inbox.copilot import knowledge_context
    source=CopilotKnowledge(id=uuid.uuid4(),name='Banking guide',content=('Unrelated branch opening details.\n'*900)+'\nKCB M-PESA is a loans and savings product exclusively offered by KCB Bank Kenya Ltd to Safaricom M-PESA customers.')
    result=knowledge_context([source],'What is KCB M-PESA?')
    assert 'exclusively offered by KCB Bank Kenya Ltd' in result and len(result)<=16000
