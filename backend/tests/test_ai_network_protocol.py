"""Two isolated Petrix databases/apps exercise the authenticated HTTP contract."""
from pathlib import Path
import json
import os
import uuid
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine,event as sql_event,text
from sqlalchemy.orm import sessionmaker
from app.main import create_app
from app.core.config import Settings
from app.workflows.actions.send_api.client import APIResult
from test_workflows import workflow,event,save,message,intent
from test_branch import variable
from test_ask_survey import field,survey
from test_ai_agent_handoff import connect,handoff
from conftest import CaptureMailer


@pytest.fixture
def remote(app,database_engine,monkeypatch):
    schema='petrix_remote_'+uuid.uuid4().hex
    url=os.environ.get('SQL_QUERY_TEST_DATABASE_URL') or database_engine.url
    remote_admin=create_engine(url,pool_size=1,max_overflow=0)
    with remote_admin.begin() as db:db.execute(text('CREATE SCHEMA "{}"'.format(schema)))
    engine=create_engine(url,pool_size=1,max_overflow=0)
    @sql_event.listens_for(engine,'connect')
    def search_path(connection,_):
        with connection.cursor() as cursor:cursor.execute('SET search_path TO "{}"'.format(schema))
        connection.commit()
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with engine.begin() as db:config.attributes['connection']=db;command.upgrade(config,'head')
    import app.main as module
    with monkeypatch.context() as patch:
        patch.setattr(module,'create_database',lambda _:(engine,sessionmaker(engine,expire_on_commit=False)))
        settings=Settings(_env_file=None,app_env='test',database_url=str(engine.url),
            session_signing_secret='different-remote-secret-over-thirty-two-characters',api_base_url='https://receiver.example',frontend_origin='https://receiver.example',intent_semantic_enabled=False)
        target_app=create_app(settings,mailer=CaptureMailer())
    owner=TestClient(target_app,base_url='https://receiver.example');owner.post('/api/auth/magic-link',json={'email':'remote@example.com'})
    signed=owner.post('/api/auth/verify',json={'token':target_app.state.mailer.token}).json();headers={'X-Petrix-CSRF':owner.cookies.get('petrix_csrf')}
    workspace=signed['workspace']['id'];assert owner.post('/api/workspaces/'+workspace+'/setup',data={'name':'Receiving workspace','country_code':'KE'},headers=headers).status_code==200
    target=owner.get('/api/workspaces/'+workspace+'/agents').json()['agents'][0];path='/api/workspaces/'+workspace+'/agents/'+target['id']
    credentials=owner.post(path+'/network/credentials',json={'expected_revision':0,'enabled':True},headers=headers).json()
    wire=TestClient(target_app,base_url='https://receiver.example')
    class HTTPTransport:
        def __init__(self):self.calls=[]
        def send(self,method,url,request_headers,body,timeout):
            from urllib.parse import urlsplit
            self.calls.append((method,urlsplit(url).path))
            response=wire.request(method,urlsplit(url).path,headers=request_headers,content=body)
            return APIResult('success' if response.is_success else 'error',response.status_code,response.json())
    transport=HTTPTransport();app.state.ai_network_client.json_transport=transport;app.state.ai_network_client.file_transport=transport
    yield {'client':owner,'wire':wire,'app':target_app,'path':path,'agent':target,'credentials':credentials,'headers':headers,'calls':transport.calls}
    wire.close();owner.close();engine.dispose()
    with remote_admin.begin() as db:
        db.execute(text('SET search_path TO public'))
        db.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema)))
    remote_admin.dispose()


def test_separate_platforms_keep_chat_context_and_surveys(client,workflow,remote):
    path,document,headers=workflow;receiver=remote['client'];target=remote['path'];creds=remote['credentials']
    target_doc=receiver.get(target+'/workflows').json();target_doc['nodes'][0]['children']=[message('Welcome {_transferred_context.name}.'),survey(field('topic',question='What happens next?')),message('Recorded {topic}.')]
    assert save(receiver,target,target_doc,remote['headers']).status_code==200
    connection=connect(client,path,headers,creds)
    document['nodes'][0]['children']=[variable('name','"Chris"'),variable('access_token','"not-for-the-receiver"'),handoff(connection)]
    assert save(client,path,document,headers).status_code==200
    initial=event(client,path,headers);assert initial.status_code==200,initial.text
    data=initial.json();assert data['ai_handoff']['domain']=='https://receiver.example' and data['messages'][0]['content']=='Welcome Chris.'
    assert data['variables']['_transferred_context']=={'name':'Chris'}
    identifier=data['conversation_id'];answered=event(client,path,headers,'message_received',identifier,content='Check complaint',survey_answer_token=data['survey']['answer_token'])
    assert answered.status_code==200 and answered.json()['messages'][0]['content']=='Recorded Check complaint.',answered.text
    restored=client.get(path+'/tester/conversations/'+identifier).json();assert restored['conversation_id']==identifier
    assert sum(m['content']=='Check complaint' for m in restored['messages'])==1
    assert all('/api/ai-network/' in request_path for _,request_path in remote['calls'])
    assert creds['secret'] not in json.dumps(restored)


def test_separate_platform_file_delivery_and_no_browser_session_required(client,workflow,remote):
    from test_send_attachment import uploaded,attachment,translation,PDF
    path,document,headers=workflow;receiver=remote['client'];target=remote['path']
    asset=uploaded(receiver,target,remote['headers']);target_doc=receiver.get(target+'/workflows').json()
    target_doc['nodes'][0]['children']=[attachment({**translation('upload'),'asset_id':asset['id']})]
    assert save(receiver,target,target_doc,remote['headers']).status_code==200
    connection=connect(client,path,headers,remote['credentials']);document['nodes'][0]['children']=[handoff(connection)]
    assert save(client,path,document,headers).status_code==200
    response=event(client,path,headers);assert response.status_code==200,response.text
    url=response.json()['messages'][0]['attachments'][0]['url'];download=client.get(url)
    assert download.status_code==200 and download.content==PDF,download.text
    assert list(remote['wire'].cookies)==[]
