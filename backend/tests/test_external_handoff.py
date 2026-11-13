"""Native provider contracts, scoped configuration, durable one-way ownership."""
from copy import deepcopy
from datetime import timedelta
import json
import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from app.core.security import utcnow
from app.external_handoff.client import ProviderClient
from app.external_handoff.schemas import IntegrationUpdate
from app.models import AgentConversation, AgentMessage, ExternalConversation, ExternalIntegration, Membership
from app.workflows.actions.external_handoff.config import ExternalHandoffConfig
from app.workflows.actions.send_api.client import APIResult
from test_workflows import workflow, event, save, message, node, intent, contents
from test_live_agent_handoff import workers


class Transport:
    def __init__(self):self.calls=[];self.result=None;self.valid=True;self.during=None
    def send(self,method,url,headers,body,timeout):
        payload=json.loads(body) if body else None;self.calls.append((method,url,headers,payload,timeout))
        if url.endswith('/passControl') or 'session.transfer' in url:
            if self.during:self.during()
            return self.result or APIResult('success',200,{} if '/passControl' in url else {'result':True})
        if not self.valid:return APIResult('error',401,{'error':'denied-secret-must-not-leak'})
        if 'switchboardIntegrations' in url:return APIResult('success',200,{'switchboardIntegrations':[{'id':'target','integrationType':'zd:agentWorkspace'}]})
        if '/scope.json' in url:return APIResult('success',200,{'result':['imopenlines','imbot']})
        if 'imopenlines.dialog.get' in url:return APIResult('success',200,{'result':{'id':payload['CHAT_ID']}})
        return APIResult('success',200,{'conversation':{'id':url.rsplit('/',1)[-1]}})


@pytest.fixture
def transport(app):
    value=Transport();app.state.external_handoff_client=ProviderClient(value);return value


def body(platform='zendesk',**changes):
    return {'expected_revision':0,'enabled':True,'origin':'https://company.'+('zendesk.com' if platform=='zendesk' else 'bitrix24.com'),
        'app_id':'app' if platform=='zendesk' else '', 'switchboard_id':'board' if platform=='zendesk' else '',
        'target_id':'target' if platform=='zendesk' else '12','key_id':'key' if platform=='zendesk' else '1','secret':'provider-secret',
        **({'bot_client_id':'bot-secret'} if platform=='bitrix24' else {}),**changes}


def connect(client,path,headers,platform='zendesk'):
    base=path.rsplit('/agents/',1)[0]+'/external-integrations';r=client.post(base+'/'+platform,json=body(platform),headers=headers)
    assert r.status_code==200,r.text
    r=client.post(base+'/'+platform+'/verify',json={'expected_revision':r.json()['revision']},headers=headers)
    assert r.status_code==200 and r.json()['active'],r.text
    return base,r.json()


def action(platform='zendesk',**changes):return node('action','external_handoff',config={'platform':platform},**changes)


def prepared(client,workflow,transport,platform='zendesk'):
    path,document,headers=workflow;connect(client,path,headers,platform)
    document['nodes'][0]['children']=[message('Before handoff')]
    assert save(client,path,document,headers).status_code==200
    current=event(client,path,headers).json();identifier=current['conversation_id']
    link=client.post(path+'/tester/conversations/'+identifier+'/external-link',json={'platform':platform,'external_id':'123'},headers=headers)
    assert link.status_code==200,link.text
    document=client.get(path+'/workflows').json()
    document['nodes'].append(intent('human',[message('Connecting.'),action(platform),message('Must not run')]))
    assert save(client,path,document,headers).status_code==200
    return path,identifier,headers


@pytest.mark.parametrize('config',[{}, {'platform':'other'}, {'platform':'Zendesk'}, {'platform':True}, {'platform':'zendesk','target':'x'}])
def test_strict_external_action_config(config):
    with pytest.raises(ValidationError):ExternalHandoffConfig.model_validate(config)


@pytest.mark.parametrize('origin',['http://company.zendesk.com','https://company.zendesk.com/path','https://a:b@company.zendesk.com','https://company.zendesk.com?token=x','https://company.zendesk.com:444'])
def test_invalid_integration_origin(origin):
    with pytest.raises(ValidationError):IntegrationUpdate.model_validate(body(origin=origin))


@pytest.mark.parametrize('platform',['zendesk','bitrix24'])
def test_verified_encrypted_scoped_settings(client,workflow,transport,app,platform):
    path,document,headers=workflow;base,row=connect(client,path,headers,platform)
    assert 'secret' not in row and 'bot_client_id' not in row
    text=client.get(base).text;assert 'provider-secret' not in text and 'bot-secret' not in text
    with app.state.session_factory() as db:
        saved=db.scalar(select(ExternalIntegration));assert 'provider-secret' not in saved.credentials_ciphertext and 'bot-secret' not in saved.credentials_ciphertext
    options=client.get(base+'/options').json();assert next(r for r in options['platforms'] if r['platform']==platform)['active']
    assert client.post(base+'/'+platform,json=body(platform,expected_revision=0),headers=headers).status_code==409
    update=body(platform,expected_revision=row['revision']);update.pop('secret');update.pop('bot_client_id',None)
    updated=client.post(base+'/'+platform,json=update,headers=headers);assert updated.status_code==200 and not updated.json()['active']
    assert client.post(base+'/'+platform,json=update).status_code==403
    update['expected_revision']=updated.json()['revision'];update['origin']='https://other.'+('zendesk.com' if platform=='zendesk' else 'bitrix24.com')
    assert client.post(base+'/'+platform,json=update,headers=headers).status_code==422


def test_failed_reverification_disables_action(client,workflow,transport):
    path,document,headers=workflow;base,row=connect(client,path,headers)
    transport.valid=False
    failed=client.post(base+'/zendesk/verify',json={'expected_revision':row['revision']},headers=headers)
    assert failed.status_code==200 and not failed.json()['active'] and 'verification_error' in failed.json()
    document['nodes'][0]['children']=[action()]
    assert save(client,path,document,headers).status_code==422


@pytest.mark.parametrize('platform',['zendesk','bitrix24'])
def test_native_transfer_is_terminal_and_history_only(client,workflow,transport,app,platform):
    path,identifier,headers=prepared(client,workflow,transport,platform)
    with app.state.session_factory() as db:
        conversation=db.get(AgentConversation,uuid.UUID(identifier));prior=dict(conversation.workflow_state)
    def check_durable():
        with app.state.session_factory() as db:
            binding=db.get(ExternalConversation,uuid.UUID(identifier));conversation=db.get(AgentConversation,uuid.UUID(identifier))
            assert binding.status=='sending' and binding.attempt_id and conversation.workflow_state['closed']
            assert db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(identifier),AgentMessage.content=='Connecting.')).first()
    transport.during=check_durable
    r=event(client,path,headers,'message_received',identifier,content='human');assert r.status_code==200,r.text
    result=r.json();assert result['closed'] and result['close_source']=='external_handoff' and result['feedback'] is None
    assert result['external_handoff']['status']=='transferred' and contents(r)==['Connecting.']
    assert not result['waiting'] and result['trace'][-1]['outcome']=='transferred'
    call=transport.calls[-1];assert call[0]=='POST' and call[4]==15
    if platform=='zendesk':assert call[1].endswith('/conversations/123/passControl') and call[3]=={'switchboardIntegration':'target'}
    else:assert call[1].endswith('/imopenlines.bot.session.transfer.json') and call[3]=={'CHAT_ID':123,'USER_ID':12,'CLIENT_ID':'bot-secret','LEAVE':'Y'}
    count=len(transport.calls)
    for kind in ('message_received','chat_closed','chat_timeout'):
        assert event(client,path,headers,kind,identifier,**({'content':'later'} if kind=='message_received' else {})).status_code==409
    assert event(client,path,headers,'chat_started',identifier).status_code==422
    assert len(transport.calls)==count
    restored=client.get(path+'/tester/conversations/'+identifier).json();assert restored['closed'] and restored['external_handoff']['status']=='transferred'
    base=path.rsplit('/agents/',1)[0];rows=client.get(base+'/inbox/chats').json()['chats'];assert rows[0]['status']=='external'
    history=client.get(base+'/inbox/chats/'+identifier);assert history.status_code==200 and history.json()['messages'][-1]['content']=='Connecting.'
    assert client.post(base+'/inbox/chats/'+identifier+'/close',json={'expected_revision':result['external_handoff']['revision']},headers=headers).status_code==409


def test_missing_link_never_closes_or_contacts_provider(client,workflow,transport):
    path,document,headers=workflow;connect(client,path,headers);document['nodes'][0]['children']=[action()]
    assert save(client,path,document,headers).status_code==200
    calls=len(transport.calls);r=event(client,path,headers);assert r.status_code==422 and 'not linked' in r.text and len(transport.calls)==calls


@pytest.mark.parametrize('result,status',[(APIResult('error',401,{'errors':[{'title':'private-provider-secret'}]}),'failed'),
    (APIResult('success',200,{'error':'private-provider-secret'}),'failed'),(APIResult('timeout',None,None,'timeout'),'uncertain'),
    (APIResult('error',503,{'error':'upstream'}),'uncertain'),(APIResult('success',200,'not-json'),'uncertain')])
def test_rejection_or_uncertainty_preserves_input_without_replay(client,workflow,transport,result,status):
    path,identifier,headers=prepared(client,workflow,transport);transport.result=result
    r=event(client,path,headers,'message_received',identifier,content='human');assert r.status_code==200,r.text
    data=r.json();assert data['external_handoff']['status']==status and data['closed']==(status=='uncertain')
    assert 'private-provider-secret' not in r.text
    snapshot=client.get(path+'/tester/conversations/'+identifier).json();assert sum(m['content']=='human' for m in snapshot['messages'])==1
    assert sum(m['content']=='Connecting.' for m in snapshot['messages'])==1
    if status=='uncertain':
        calls=len(transport.calls);assert event(client,path,headers,'message_received',identifier,content='human').status_code==409;assert len(transport.calls)==calls


@pytest.mark.parametrize('resolution',['transferred','not_transferred'])
def test_uncertain_transfer_requires_explicit_review(client,workflow,transport,resolution):
    path,identifier,headers=prepared(client,workflow,transport);transport.result=APIResult('timeout',error='timeout')
    r=event(client,path,headers,'message_received',identifier,content='human').json();rev=r['external_handoff']['revision'];calls=len(transport.calls)
    reviewed=client.post(path+'/tester/conversations/'+identifier+'/external-review',json={'expected_revision':rev,'resolution':resolution},headers=headers)
    assert reviewed.status_code==200 and reviewed.json()['status']==('transferred' if resolution=='transferred' else 'failed')
    assert len(transport.calls)==calls
    state=client.get(path+'/tester/conversations/'+identifier).json();assert state['closed']==(resolution=='transferred')
    assert client.post(path+'/tester/conversations/'+identifier+'/external-review',json={'expected_revision':rev,'resolution':resolution},headers=headers).status_code==409


def test_unique_provider_binding_and_wrong_account(client,workflow,transport):
    path,identifier,headers=prepared(client,workflow,transport)
    new=event(client,path,headers).json()['conversation_id']
    r=client.post(path+'/tester/conversations/'+new+'/external-link',json={'platform':'zendesk','external_id':'123'},headers=headers)
    assert r.status_code==409
    base=path.rsplit('/agents/',1)[0]+'/external-integrations';row=client.get(base).json()['integrations'][0]
    change=body(expected_revision=row['revision'],origin='https://other.zendesk.com')
    saved=client.post(base+'/zendesk',json=change,headers=headers).json()
    assert client.post(base+'/zendesk/verify',json={'expected_revision':saved['revision']},headers=headers).json()['active']
    assert event(client,path,headers,'message_received',identifier,content='human').status_code==422


def test_members_cannot_configure_or_read_another_external_history(client,workflow,transport,workers):
    path,identifier,headers=prepared(client,workflow,transport);worker,member_headers,member_id=workers()
    assert event(client,path,headers,'message_received',identifier,content='human').status_code==200
    base=path.rsplit('/agents/',1)[0]
    assert worker.get(base+'/external-integrations').status_code==403
    assert worker.post(base+'/external-integrations/zendesk',json=body(),headers=member_headers).status_code==403
    assert worker.get(base+'/inbox/chats/'+identifier).status_code==404
    assert worker.get(base+'/inbox/chats').json()['chats']==[]


def test_external_handoff_has_no_children_or_post_close_entry(client,workflow,transport):
    path,document,headers=workflow;connect(client,path,headers)
    document['nodes'][0]['children']=[action(children=[message('Unreachable')])]
    assert save(client,path,document,headers).status_code==422
    document['nodes'][0]['children']=[];next(n for n in document['nodes'] if n['type']=='chat_closed')['children']=[action()]
    assert save(client,path,document,headers).status_code==422


def test_disabling_integration_blocks_runtime_without_closing(client,workflow,transport):
    path,identifier,headers=prepared(client,workflow,transport)
    base=path.rsplit('/agents/',1)[0]+'/external-integrations';row=client.get(base).json()['integrations'][0]
    r=client.post(base+'/zendesk',json=body(expected_revision=row['revision'],enabled=False),headers=headers);assert r.status_code==200
    calls=len(transport.calls)
    assert event(client,path,headers,'message_received',identifier,content='human').status_code==422
    assert len(transport.calls)==calls and not client.get(path+'/tester/conversations/'+identifier).json()['closed']


def test_unknown_provider_conversation_is_not_linked(client,workflow,transport):
    path,document,headers=workflow;connect(client,path,headers);identifier=event(client,path,headers).json()['conversation_id']
    transport.valid=False
    r=client.post(path+'/tester/conversations/'+identifier+'/external-link',json={'platform':'zendesk','external_id':'unknown'},headers=headers)
    assert r.status_code==422 and not client.get(path+'/tester/conversations/'+identifier).json()['external_handoff']


def test_native_bitrix_false_and_error_description_are_not_success():
    transport=Transport();provider=ProviderClient(transport);config=body('bitrix24');secrets={'secret':'secret','bot_client_id':'bot'}
    for payload in ({'result':False},{'error':'OPERATOR_WRONG'},{'error_description':'failure'}):
        transport.result=APIResult('success',200,payload)
        result=provider.transfer('bitrix24',config,secrets,'123')
        assert result.status=='failed'


def test_tester_cannot_supply_external_binding_or_resume_mode(client,workflow):
    path,document,headers=workflow
    for extra in ({'external_handoff':{'status':'transferred'}},{'external_id':'123'},{'resume_mode':'handoff'}):
        assert client.post(path+'/tester/events',json={'event':'chat_started',**extra},headers=headers).status_code==422
