"""Real receiving workflows and durable same-window routing, without model calls."""
from copy import deepcopy
import json
import uuid
import pytest
from sqlalchemy import select
from app.models import AgentConversation, AgentMessage, AIInboundSession, AITransferConnection, AINetworkCredential
from app.ai_network import privacy
from test_workflows import workflow,node,intent,message,event,save,contents
from test_branch import variable
from test_ask_survey import survey,field


def create_receiver(client,path,headers,name='Complaints agent'):
    base=path.rsplit('/',1)[0]
    response=client.post(base,data={'name':name,'default_language':'en'},headers=headers)
    assert response.status_code==201,response.text
    agent=response.json();target=base+'/'+agent['id']
    enabled=client.post(target+'/network/credentials',json={'expected_revision':0,'enabled':True},headers=headers)
    assert enabled.status_code==200,enabled.text
    return agent,target,enabled.json()


def connect(client,path,headers,credentials):
    response=client.post(path+'/network/transfer-connections',json={'agent_id':credentials['agent_id'],'secret':credentials['secret'],'domain':credentials['domain']},headers=headers)
    assert response.status_code==200,response.text
    return response.json()


def handoff(connection):return node('action','ai_agent_handoff',config={'connection_id':connection['id']})


def configure_receiver(client,target,headers,actions,triggers=None):
    document=client.get(target+'/workflows').json();document['nodes'][0]['children']=actions
    document['nodes']+=(triggers or [])
    response=save(client,target,document,headers);assert response.status_code==200,response.text
    return response.json()


def setup(client,workflow,actions=None,triggers=None):
    path,document,headers=workflow
    agent,target,credentials=create_receiver(client,path,headers)
    connection=connect(client,path,headers,credentials)
    configure_receiver(client,target,headers,actions or [message('Hello {_transferred_context.customer_name}. I can handle your complaint.')],triggers)
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),variable('api_key','"source-secret"'),message('Connecting to complaints.'),handoff(connection),message('Must not run')]
    response=save(client,path,document,headers);assert response.status_code==200,response.text
    return path,target,credentials,connection,headers


def test_same_window_context_receiving_start_and_followup(client,workflow,app):
    path,target,credentials,connection,headers=setup(client,workflow,triggers=[intent('status',[message('I am checking your complaint, {_transferred_context.customer_name}.')])])
    response=event(client,path,headers);assert response.status_code==200,response.text
    data=response.json();assert data['ai_handoff']['status']=='active',data
    assert contents(response)==['Connecting to complaints.','Hello Chris. I can handle your complaint.']
    assert data['variables']['_transferred_context']=={'customer_name':'Chris'}
    assert not data['closed'] and data['ai_handoff']['name']=='Complaints agent'
    identifier=data['conversation_id']
    next_response=event(client,path,headers,'message_received',identifier,content='status',client_event_id=str(uuid.uuid4()))
    assert contents(next_response)==['I am checking your complaint, Chris.']
    restored=client.get(path+'/tester/conversations/'+identifier).json()
    assert len([m for m in restored['messages'] if m['content']=='status'])==1
    assert len([m for m in restored['messages'] if m['content']=='Connecting to complaints.'])==1
    assert restored['ai_handoff']['name']=='Complaints agent'
    with app.state.session_factory() as db:
        incoming=db.scalar(select(AIInboundSession));child=db.get(AgentConversation,incoming.conversation_id)
        assert child.agent_id==uuid.UUID(credentials['agent_id']) and child.workflow_state['variables']['_transferred_context']=={'customer_name':'Chris'}
        assert 'source-secret' not in json.dumps(child.workflow_state)
        row=db.get(AINetworkCredential,child.agent_id);assert credentials['secret'] not in row.secret_ciphertext


def test_receiving_survey_and_branch_uses_transferred_context(client,workflow):
    actions=[message('Welcome {_transferred_context.customer_name}.'),survey(field('email','email',question='What is your email?')),message('Thanks {email}.')]
    path,target,credentials,connection,headers=setup(client,workflow,actions=actions)
    response=event(client,path,headers);data=response.json();assert data.get('survey'),data
    reply=event(client,path,headers,'message_received',data['conversation_id'],content='chris@example.com',survey_answer_token=data['survey']['answer_token'])
    assert contents(reply)==['Thanks chris@example.com.']
    assert reply.json()['variables']['email']=='chris@example.com'


def test_filtered_context_and_reserved_read_only_name():
    value=privacy.context({'name':'Chris','api_key':'secret','password':'private','_internal':'hidden','survey_file':{'id':'a','url':'/api/file','name':'private.pdf'},'nested':{'access_token':'private','answer':2},'numbers':[0,1], 'session_id':'private'})
    assert value['name']=='Chris' and value['nested']=={'answer':2} and 'api_key' not in value and '_internal' not in value
    assert 'url' not in value['survey_file'] and value['numbers']==[0,1]


def test_unverified_or_foreign_connection_cannot_be_saved(client,workflow):
    path,document,headers=workflow;document['nodes'][0]['children']=[handoff({'id':str(uuid.uuid4())})]
    assert save(client,path,document,headers).status_code==422


def test_credentials_do_not_leak_and_wrong_secret_cannot_connect(client,workflow):
    path,document,headers=workflow;agent,target,credentials=create_receiver(client,path,headers)
    assert 'secret' not in client.get(target+'/network/credentials').json()
    bad=client.post(path+'/network/transfer-connections',json={'agent_id':agent['id'],'domain':credentials['domain'],'secret':'x'*43},headers=headers)
    assert bad.status_code==422 and credentials['secret'] not in bad.text
    assert client.post(target+'/network/credentials/reveal',json={}).status_code==403
    revealed=client.post(target+'/network/credentials/reveal',json={},headers=headers);assert revealed.json()['secret']==credentials['secret']
    assert credentials['secret'] not in client.get(target+'/export').text


def test_client_request_id_does_not_duplicate_receiving_actions(client,workflow):
    path,target,credentials,connection,headers=setup(client,workflow,triggers=[intent('again',[message('One reply')])])
    first=event(client,path,headers).json();request_id=str(uuid.uuid4())
    a=event(client,path,headers,'message_received',first['conversation_id'],content='again',client_event_id=request_id)
    b=event(client,path,headers,'message_received',first['conversation_id'],content='again',client_event_id=request_id)
    assert a.status_code==b.status_code==200 and a.json()['messages']==b.json()['messages']
    state=client.get(path+'/tester/conversations/'+first['conversation_id']).json()
    assert sum(m['content']=='One reply' for m in state['messages'])==1 and sum(m['content']=='again' for m in state['messages'])==1


def test_mirrored_message_conflict_keeps_one_copy_after_stale_lookup(client,workflow,app,monkeypatch):
    from app.ai_network import sender
    from app.models import AIOutgoingSession
    path,target,credentials,connection,headers=setup(client,workflow)
    initial=event(client,path,headers).json();identifier=uuid.UUID(initial['conversation_id'])
    with app.state.session_factory() as db:
        incoming=db.scalar(select(AIInboundSession))
        original=db.scalar(select(AgentMessage).where(AgentMessage.conversation_id==incoming.conversation_id,AgentMessage.role=='assistant'))
        remote_message={'id':str(original.id),'role':original.role,'content':original.content,'created_at':original.created_at.isoformat(),**original.message_data}
        conversation=db.get(AgentConversation,identifier);outgoing=db.get(AIOutgoingSession,identifier)
        result={**outgoing.last_result['result'],'messages':[remote_message]}
        native_get=db.get
        # Model another response having committed after this response's lookup.
        def stale_get(model,key,*args,**kwargs):
            return None if model is AgentMessage else native_get(model,key,*args,**kwargs)
        monkeypatch.setattr(db,'get',stale_get)
        assert sender.apply_result(db,conversation,outgoing,result)==[]
        db.commit()
        matches=db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==identifier,AgentMessage.content==original.content)).all()
        assert len(matches)==1


def test_dropped_start_ack_recovers_one_receiving_session(client,workflow,app):
    from fastapi import HTTPException
    path,target,credentials,connection,headers=setup(client,workflow)
    original=app.state.ai_network_client.call;dropped=[]
    def call(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[2]=='start' and not dropped:dropped.append(True);raise HTTPException(503,'lost acknowledgement')
        return result
    app.state.ai_network_client.call=call
    first=event(client,path,headers);data=first.json();assert data['ai_handoff']['status']=='starting' and data['ai_handoff']['pending']
    restored=client.get(path+'/tester/conversations/'+data['conversation_id']).json();assert restored['ai_handoff']['pending']
    retry=client.post(path+'/tester/conversations/'+data['conversation_id']+'/ai-retry',json={},headers=headers)
    assert retry.status_code==200 and retry.json()['ai_handoff']['status']=='active',retry.text
    final=client.get(path+'/tester/conversations/'+data['conversation_id']).json()
    assert sum(m['content']=='Hello Chris. I can handle your complaint.' for m in final['messages'])==1
    with app.state.session_factory() as db:assert len(db.scalars(select(AIInboundSession)).all())==1


def test_dropped_event_ack_retries_same_request_without_running_twice(client,workflow,app):
    from fastapi import HTTPException
    path,target,credentials,connection,headers=setup(client,workflow,triggers=[intent('once',[variable('count','variables.get("count", 0) + 1'),message('Count {count}')])])
    data=event(client,path,headers).json();original=app.state.ai_network_client.call;dropped=[]
    def call(*args,**kwargs):
        result=original(*args,**kwargs)
        if args[2]=='event' and not dropped:dropped.append(True);raise HTTPException(503,'lost response')
        return result
    app.state.ai_network_client.call=call
    first=event(client,path,headers,'message_received',data['conversation_id'],content='once',client_event_id=str(uuid.uuid4()))
    assert first.status_code==200 and first.json()['ai_handoff']['pending']
    retry=client.post(path+'/tester/conversations/'+data['conversation_id']+'/ai-retry',json={},headers=headers)
    assert retry.status_code==200 and retry.json()['variables']['count']==1,retry.text
    state=client.get(path+'/tester/conversations/'+data['conversation_id']).json()
    assert sum(m['content']=='once' for m in state['messages'])==1 and sum(m['content']=='Count 1' for m in state['messages'])==1


def test_receiving_file_survey_upload_and_authorized_proxy_download(client,workflow):
    actions=[survey(field('document','attachment',question='Upload a document')),message('Document received.')]
    path,target,credentials,connection,headers=setup(client,workflow,actions=actions)
    data=event(client,path,headers).json();identifier=data['conversation_id']
    payload=b'%PDF-1.4\nnetwork test file\n%%EOF'
    upload=client.post(path+'/tester/attachments',data={'conversation_id':identifier,'survey_answer_token':data['survey']['answer_token'],'client_event_id':str(uuid.uuid4())},
        files={'files':('complaint.pdf',payload,'application/pdf')},headers=headers)
    assert upload.status_code==200,upload.text
    assert upload.json()['messages'][-1]['content']=='Document received.'
    file=upload.json()['attachments'][0];assert '/tester/attachments/' in file['url']
    downloaded=client.get(file['url']);assert downloaded.status_code==200 and downloaded.content==payload,downloaded.text
    assert client.get(file['url'].rsplit('/',1)[0]+'/'+str(uuid.uuid4())).status_code==404


def test_rotating_secret_revokes_existing_sessions_and_requires_reconnect(client,workflow,app):
    path,target,credentials,connection,headers=setup(client,workflow);data=event(client,path,headers).json()
    rotated=client.post(target+'/network/credentials',json={'expected_revision':credentials['revision'],'enabled':True,'rotate':True},headers=headers)
    assert rotated.status_code==200 and rotated.json()['secret']!=credentials['secret']
    state=client.get(path+'/tester/conversations/'+data['conversation_id']).json();assert state['ai_handoff']['error']
    new=event(client,path,headers);assert new.status_code==422 and 'credentials changed' in new.text
    updated=connect(client,path,headers,{**credentials,'secret':rotated.json()['secret']});assert updated['id']==connection['id']
    assert event(client,path,headers).json()['ai_handoff']['status']=='active'


def test_connection_reference_and_active_agent_deletion_are_protected(client,workflow):
    path,target,credentials,connection,headers=setup(client,workflow);data=event(client,path,headers).json()
    assert client.post(path+'/network/transfer-connections/'+connection['id']+'/delete',json={},headers=headers).status_code==409
    for endpoint in (path,target):
        agent=client.get(endpoint).json()
        assert client.post(endpoint+'/delete',json={'expected_revision':agent['revision'],'confirmation_name':agent['name']},headers=headers).status_code==409


def test_same_request_id_with_changed_message_is_rejected(client,workflow):
    path,target,credentials,connection,headers=setup(client,workflow,triggers=[intent('one',[message('First')])])
    first=event(client,path,headers).json();identifier=str(uuid.uuid4())
    assert event(client,path,headers,'message_received',first['conversation_id'],content='one',client_event_id=identifier).status_code==200
    assert event(client,path,headers,'message_received',first['conversation_id'],content='changed',client_event_id=identifier).status_code==409


def test_transferred_context_read_only_script_and_nested_api_data(client,workflow):
    actions=[variable('answer','_transferred_context.customer_name.upper()'),message('{answer}')]
    path,target,credentials,connection,headers=setup(client,workflow,actions=actions)
    response=event(client,path,headers);assert contents(response)[-1]=='CHRIS'
    assert privacy.context({'api_result':{'body':{'data':{'id':42,'balance':100}}}})=={'api_result':{'body':{'data':{'id':42,'balance':100}}}}
    assert privacy.redact_history('My password: abc and token=xyz',{})=='My [redacted] and [redacted]'


def test_start_history_filters_values_of_sensitive_source_variables(client,workflow,app):
    path,target,credentials,connection,headers=setup(client,workflow)
    document=client.get(path+'/workflows').json();document['nodes'][0]['children'].insert(3,message('The internal value is {api_key}'))
    assert save(client,path,document,headers).status_code==200
    assert event(client,path,headers).status_code==200
    with app.state.session_factory() as db:
        incoming=db.scalar(select(AIInboundSession))
        texts=[message.content for message in db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==incoming.conversation_id))]
        assert all('source-secret' not in value for value in texts) and any('[redacted]' in value for value in texts)


def test_handoff_cycles_stop_before_reentering_origin(client,workflow,app):
    path,document,headers=workflow;agent,target,credentials=create_receiver(client,path,headers);connection=connect(client,path,headers,credentials)
    original=client.post(path+'/network/credentials',json={'expected_revision':0,'enabled':True},headers=headers).json()
    reverse=connect(client,target,headers,original);configure_receiver(client,target,headers,[handoff(reverse)])
    document['nodes'][0]['children']=[handoff(connection)];assert save(client,path,document,headers).status_code==200
    result=event(client,path,headers).json();assert result['ai_handoff']['status']=='active' and 'starting workflow' in result['notice']
    with app.state.session_factory() as db:assert len(db.scalars(select(AIInboundSession)).all())==1


def test_receiving_uploaded_attachment_is_proxied_and_cached(client,workflow):
    from test_send_attachment import uploaded,attachment,translation,PDF
    path,target,credentials,connection,headers=setup(client,workflow)
    asset=uploaded(client,target,headers)
    configure_receiver(client,target,headers,[attachment({**translation('upload'),'asset_id':asset['id']})])
    response=event(client,path,headers);assert response.status_code==200,response.text
    file=response.json()['messages'][-1]['attachments'][0]
    assert '/ai-files/' in file['url']
    download=client.get(file['url']);assert download.status_code==200 and download.content==PDF,download.text
    assert client.get(file['url'],headers={'Range':'bytes=0-4'}).content==b'%PDF-'
    assert client.get(file['url'].rsplit('/',1)[0]+'/'+str(uuid.uuid4())).status_code==404
    assert client.post(target+'/network/credentials',json={'expected_revision':credentials['revision'],'enabled':False},headers=headers).status_code==200
    assert client.get(file['url']).content==PDF


@pytest.mark.parametrize('change',[{'agent_id':'wrong'},{'domain':'http://public.example'},{'domain':'https://user:pass@remote.example'},
    {'domain':'https://remote.example/path'},{'domain':'https://remote.example?secret=x'},{'secret':'short'},{'extra':True}])
def test_connection_validation(client,workflow,change):
    path,document,headers=workflow;agent,target,credentials=create_receiver(client,path,headers)
    body={'agent_id':agent['id'],'secret':credentials['secret'],'domain':credentials['domain'],**change}
    assert client.post(path+'/network/transfer-connections',json=body,headers=headers).status_code==422


def test_session_tokens_are_scoped_and_not_exposed_to_customer(client,workflow,app):
    path,target,credentials,connection,headers=setup(client,workflow);data=event(client,path,headers).json()
    with app.state.session_factory() as db:
        incoming=db.scalar(select(AIInboundSession));session_id=str(incoming.id)
        assert incoming.token_ciphertext not in json.dumps(data)
    assert client.get('/api/ai-network/sessions/'+session_id+'/snapshot').status_code==401
    assert client.get('/api/ai-network/sessions/'+session_id+'/snapshot',headers={'Authorization':'Bearer invalid'}).status_code==401
    assert client.post('/api/ai-network/agents/'+credentials['agent_id']+'/verify',json={},headers={'X-Petrix-Agent-Secret':'invalid'}).status_code==401


def test_receiving_close_chat_and_feedback_stay_on_receiver(client,workflow):
    from test_chat_closed import closed_root
    from test_collect_feedback import form,submit
    path,target,credentials,connection,headers=setup(client,workflow,actions=[message('Finished.'),node('action','close_chat')])
    document=client.get(target+'/workflows').json();closed_root(document)['children']=[form()]
    assert save(client,target,document,headers).status_code==200
    first=event(client,path,headers).json();assert first['closed'] and first['feedback'] and first['ai_handoff']['status']=='active'
    source=client.get(path).json();assert client.post(path+'/delete',json={'expected_revision':source['revision'],'confirmation_name':source['name']},headers=headers).status_code==409
    response=submit(client,path,headers,first,rating=5);assert response.status_code==200,response.text
    assert response.json()['feedback_submitted'] and response.json()['ai_handoff']['status']=='closed'


def test_multiple_hops_forward_to_current_agent_and_merge_public_context(client,workflow):
    path,document,headers=workflow
    b,bpath,bcred=create_receiver(client,path,headers,'Agent B');c,cpath,ccred=create_receiver(client,path,headers,'Agent C')
    ab=connect(client,path,headers,bcred);bc=connect(client,bpath,headers,ccred)
    configure_receiver(client,bpath,headers,[variable('department','"complaints"'),handoff(bc)])
    configure_receiver(client,cpath,headers,[message('Welcome {_transferred_context.customer_name} to {_transferred_context.department}.')],[intent('continue',[message('Agent C replied')])])
    document['nodes'][0]['children']=[variable('customer_name','"Chris"'),handoff(ab)];assert save(client,path,document,headers).status_code==200
    initial=event(client,path,headers);assert initial.status_code==200,initial.text
    assert initial.json()['ai_handoff']['name']=='Agent C' and contents(initial)==['Welcome Chris to complaints.']
    assert contents(event(client,path,headers,'message_received',initial.json()['conversation_id'],content='continue'))==['Agent C replied']


def test_retrying_start_request_restores_same_conversation(client,workflow,app):
    path,target,credentials,connection,headers=setup(client,workflow);identifier=str(uuid.uuid4())
    first=event(client,path,headers,client_event_id=identifier).json()
    replay=event(client,path,headers,client_event_id=identifier)
    assert replay.status_code==200 and replay.json()['conversation_id']==first['conversation_id'],replay.text
    with app.state.session_factory() as db:assert len(db.scalars(select(AIInboundSession)).all())==1


def test_originating_handoff_message_is_not_forwarded_again_after_ack_loss(client,workflow):
    path,document,headers=workflow;agent,target,credentials=create_receiver(client,path,headers);connection=connect(client,path,headers,credentials)
    configure_receiver(client,target,headers,[survey(field('email','email'))])
    document['nodes'].append(intent('human',[handoff(connection)]));assert save(client,path,document,headers).status_code==200
    initial=event(client,path,headers).json();request_id=str(uuid.uuid4())
    first=event(client,path,headers,'message_received',initial['conversation_id'],content='human',client_event_id=request_id)
    assert first.status_code==200 and first.json()['survey'],first.text
    replay=event(client,path,headers,'message_received',initial['conversation_id'],content='human',client_event_id=request_id)
    assert replay.status_code==200 and replay.json()['survey']['answer_token']==first.json()['survey']['answer_token']
    assert replay.json()['messages']==[]
    state=client.get(path+'/tester/conversations/'+initial['conversation_id']).json();assert sum(m['content']=='human' for m in state['messages'])==1


def test_network_validation_errors_do_not_echo_secret_input(client,workflow):
    path,document,headers=workflow
    response=client.post(path+'/network/transfer-connections',json={'agent_id':str(uuid.uuid4()),'secret':'sensitive-short','domain':'https://receiver.example'},headers=headers)
    assert response.status_code==422 and 'sensitive-short' not in response.text
