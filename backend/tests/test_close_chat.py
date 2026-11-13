"""Ready Close Chat action, terminal flow control and durable close automation."""
from copy import deepcopy
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.models import AgentConversation, AgentMessage, Membership, WorkflowDocument
from app.workflows.actions.close_chat.config import CloseChatConfig
from app.workflows.actions.close_chat.handler import CloseChat, execute as request_close
from app.workflows.actions.registry import ACTIONS, config_for, handler_for
from test_ask_survey import field, survey, upload as survey_upload
from test_branch import branch, variable
from test_chat_closed import closed_root
from test_collect_feedback import form, submit
from test_jump import jump, prepare, run, setup_tree
from test_send_api import APIResult, StubAPI, api
from test_send_attachment import PDF, attachment, image_bytes, translation, uploaded
from test_show_carousel import card, carousel
from test_workflows import FakeLLM, contents, event, intent, message, node, save, workflow


def close_action(**kwargs):
    return node('action', 'close_chat', name='Close chat', config={}, **kwargs)


def configure(client,path,document,headers,actions,after_close=None):
    document['nodes'][0]['children']=actions
    closed_root(document)['children']=after_close or []
    response=save(client,path,document,headers);assert response.status_code==200,response.text
    return response.json()


@pytest.mark.parametrize('config',[{'message':'Bye'},{'enabled':True},{'reopen':False},{'target_node_id':str(uuid.uuid4())}])
def test_close_chat_has_no_untrusted_extra_settings(config):
    with pytest.raises(ValidationError):CloseChatConfig.model_validate(config)


def test_close_chat_registry_is_ready_and_has_real_config_and_handler(client,workflow):
    path,document,_=workflow
    definition=next(value for value in document['actions'] if value['type']=='close_chat')
    assert definition['available'] and definition['allowed_after_close'] and not definition['post_close_only']
    assert definition['group']=='Flow control' and ACTIONS['close_chat']['available']
    assert CloseChatConfig.model_validate({}).model_dump()=={} and config_for('close_chat') is CloseChatConfig
    assert handler_for('close_chat') is request_close and isinstance(request_close({},None),CloseChat)


def test_runtime_close_is_terminal_and_skips_children_siblings_and_waits():
    agent,document=setup_tree();action=close_action(children=[variable('child','True')])
    document.nodes[0]['children']=[variable('before','True'),action,variable('after','True'),intent('wait',[message('Never')])]
    prepare(agent,document);state,output,trace,matched=run(agent,document)
    assert state['closed'] and matched and state['variables']=={'before':True} and output==[] and state['stack']==[]
    assert state['close_requested']['node_id']==action['id'] and trace[-1]['type']=='close_chat' and trace[-1]['outcome']=='closed'


def test_close_chat_is_saved_and_executes_chat_closed_once_without_extra_messages(client,workflow,app):
    path,document,headers=workflow;action=close_action()
    document=configure(client,path,document,headers,[variable('before','True'),message('Goodbye.'),action,message('Never send this')],
        [variable('close_runs','variables.get("close_runs",0)+1')])
    response=event(client,path,headers);assert contents(response)==['Goodbye.'];result=response.json()
    assert result['closed'] and result['close_source']=='workflow' and result['closed_at'] and not result['waiting']
    assert result['variables']=={'before':True,'close_runs':1} and result['feedback'] is None
    assert sum(step['type']=='chat_closed' for step in result['trace'])==1
    with app.state.session_factory() as db:
        stored=db.get(AgentConversation,uuid.UUID(result['conversation_id'])).workflow_state
        assert stored['close_automation']['status']=='completed' and stored['closed_by']
        assert stored['close_action']['node_id']==action['id'] and stored['close_action']['node_number']
        assert 'close_requested' not in stored and 'survey' not in stored
        assert db.scalar(select(func.count()).select_from(AgentMessage))==1
    assert event(client,path,headers,'chat_closed',result['conversation_id']).status_code==409


def test_disabled_close_keeps_conversation_open_and_skips_its_subtree(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[close_action(enabled=False,children=[message('Skipped child')]),message('Still open')],[variable('closed_only','True')])
    response=event(client,path,headers);assert contents(response)==['Still open'];result=response.json()
    assert not result['closed'] and 'closed_only' not in result['variables'] and result['feedback'] is None


@pytest.mark.parametrize('condition,closed,content',[('True',True,'Before close'),('False',False,'Else path')])
def test_branch_close_stops_enclosing_continuation_only_on_the_selected_path(client,workflow,condition,closed,content):
    path,document,headers=workflow
    configure(client,path,document,headers,[branch((condition,[message('Before close'),close_action(),message('Never')]),otherwise=[message('Else path')]),variable('continued','True')])
    response=event(client,path,headers);assert contents(response)==[content];result=response.json()
    assert result['closed'] is closed and ('continued' in result['variables']) is not closed


def test_jump_to_close_stops_the_destination_and_source_continuations(client,workflow):
    path,document,headers=workflow;action=close_action();document['nodes'].append(intent('Shared close',[variable('skip','True'),action,message('Never')]))
    configure(client,path,document,headers,[jump(action),message('Abandoned')],[variable('closed_once','True')])
    result=event(client,path,headers).json();assert result['closed'] and result['messages']==[]
    assert result['variables']=={'closed_once':True} and any(step['type']=='jump' for step in result['trace'])


@pytest.mark.parametrize('outcome',['success','error','timeout'])
def test_api_outcomes_can_close_without_resuming_the_open_flow(client,workflow,app,outcome):
    path,document,headers=workflow;app.state.send_api_client=StubAPI(APIResult(outcome,200 if outcome=='success' else None,{}))
    request=api(url='https://api.example.test/end',**{outcome:[close_action()]})
    configure(client,path,document,headers,[request,message('Never')],[variable('closed_after_api','True')])
    result=event(client,path,headers).json();assert result['closed'] and result['variables']['closed_after_api'] and result['messages']==[]
    assert len(app.state.send_api_client.calls)==1


def test_message_received_closure_preserves_input_and_blocks_all_later_replies(client,workflow,app):
    path,document,headers=workflow;fake=FakeLLM();app.state.llm_tester=fake
    document['nodes'].append(intent('done',[message('Thanks for contacting us.'),close_action()]))
    configure(client,path,document,headers,[])
    start=event(client,path,headers).json();response=event(client,path,headers,'message_received',start['conversation_id'],content='done')
    assert contents(response)==['Thanks for contacting us.'];result=response.json();assert result['closed'] and result['matching']['node_id']
    assert event(client,path,headers,'message_received',result['conversation_id'],content='again').status_code==409
    assert client.post(path+'/tester/messages',headers=headers,json={'conversation_id':result['conversation_id'],'content':'again'}).status_code==409
    assert fake.calls==[]
    with app.state.session_factory() as db:
        values=db.scalars(select(AgentMessage).where(AgentMessage.conversation_id==uuid.UUID(result['conversation_id']))).all()
        assert sorted((item.role,item.content) for item in values)==[('assistant','Thanks for contacting us.'),('user','done')]


def test_close_chat_automatically_requests_feedback_and_resumes_after_rating(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[message('Goodbye.'),close_action()],[form(),variable('saved_rating','feedback_rating')])
    requested=event(client,path,headers).json();assert requested['closed'] and requested['feedback'] and requested['close_source']=='workflow'
    assert 'saved_rating' not in requested['variables'];key=requested['feedback']['fields'][0]['id']
    result=submit(client,path,headers,requested,5,{key:'Helpful'}).json()
    assert result['feedback_submitted'] and result['variables']['saved_rating']==5 and result['variables']['feedback_comment']=='Helpful'
    assert result['close_source']=='workflow' and result['closed_at']==requested['closed_at']


def test_close_chat_in_closed_and_feedback_paths_does_not_loop_or_drop_feedback(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[close_action()],
        [close_action(children=[variable('already_closed_child','True')]),variable('close_runs','variables.get("close_runs",0)+1'),
         form(True,False),close_action(),variable('after_feedback','feedback_rating')])
    requested=event(client,path,headers).json();assert requested['feedback'] and requested['variables']=={'already_closed_child':True,'close_runs':1}
    assert any(step['type']=='close_chat' and step['outcome']=='already_closed' for step in requested['trace'])
    result=submit(client,path,headers,requested,4).json()
    assert result['closed'] and result['feedback_submitted'] and result['variables']['after_feedback']==4 and result['variables']['close_runs']==1
    assert sum(step['type']=='chat_closed' for step in result['trace'])==0


def test_close_chat_under_manual_chat_closed_is_idempotent(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[],[close_action(),variable('closed_runs','variables.get("closed_runs",0)+1')])
    start=event(client,path,headers).json();result=event(client,path,headers,'chat_closed',start['conversation_id']).json()
    assert result['close_source']=='tester' and result['variables']['closed_runs']==1 and not result['automation_error']


def test_error_before_close_rolls_back_the_open_turn(client,workflow,app):
    path,document,headers=workflow
    document['nodes'].append(intent('done',[variable('partial','True'),message('{missing}'),close_action()]))
    configure(client,path,document,headers,[]);start=event(client,path,headers).json()
    assert event(client,path,headers,'message_received',start['conversation_id'],content='done').status_code==422
    with app.state.session_factory() as db:
        state=db.get(AgentConversation,uuid.UUID(start['conversation_id'])).workflow_state
        assert not state.get('closed') and 'partial' not in state.get('variables',{})
        assert db.scalar(select(func.count()).select_from(AgentMessage))==0


def test_error_after_close_keeps_prefix_variables_messages_and_the_closed_state(client,workflow,app):
    path,document,headers=workflow
    configure(client,path,document,headers,[variable('before','True'),message('Goodbye.'),close_action()],
        [variable('partial','True'),variable('broken','missing_close_value')])
    response=event(client,path,headers);assert response.status_code==200;result=response.json()
    assert result['closed'] and result['automation_error'] and result['variables']=={'before':True}
    assert [item['content'] for item in result['messages']]==['Goodbye.']
    assert [step['type'] for step in result['trace']][-2:]==['close_chat','chat_closed']
    assert result['trace'][-1]['outcome']=='automation_failed'
    with app.state.session_factory() as db:
        state=db.get(AgentConversation,uuid.UUID(result['conversation_id'])).workflow_state
        assert state['close_automation']['status']=='failed' and state['close_source']=='workflow'
        assert db.scalar(select(func.count()).select_from(AgentMessage))==1


@pytest.mark.parametrize('kind',['send_attachment','show_carousel'])
def test_sent_media_snapshots_survive_post_close_automation_failure(client,workflow,kind):
    path,document,headers=workflow
    data=PDF if kind=='send_attachment' else image_bytes()
    asset=uploaded(client,path,headers,name='form.pdf' if kind=='send_attachment' else 'image.png',data=data,file_format='document' if kind=='send_attachment' else 'image')
    if kind=='send_attachment':
        value=translation('upload');value['asset_id']=asset['id'];media=attachment(value)
    else:media=carousel(card(image={'source':'upload','asset_id':asset['id'],'url':''}))
    configure(client,path,document,headers,[media,close_action()],[variable('broken','missing_close_value')])
    result=event(client,path,headers).json();assert result['closed'] and result['automation_error'] and result['messages'][0]['source']==kind
    file=result['messages'][0]['attachments'][0] if kind=='send_attachment' else result['messages'][0]['carousel']['cards'][0]['image']
    assert client.get(file['url']).content==data


def test_survey_upload_and_answer_are_preserved_when_close_automation_fails(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[survey(field('evidence','attachment')),close_action()], [variable('broken','missing_close_value')])
    requested=event(client,path,headers).json();response=survey_upload(client,path,headers,requested,[('evidence.txt',b'Evidence bytes','text/plain')])
    assert response.status_code==200,response.text
    result=response.json();assert result['closed'] and result['automation_error'] and result['survey'] is None
    file=result['variables']['evidence'];assert client.get(file['url']).content==b'Evidence bytes'
    assert result['attachments'][0]['id']==file['id'] and result['waiting'] is False


def test_legacy_unsafe_chat_closed_workflow_cannot_reopen_or_post_messages(client,workflow,app):
    path,document,headers=workflow;configure(client,path,document,headers,[message('Goodbye.'),close_action()])
    with app.state.session_factory() as db:
        stored=db.get(WorkflowDocument,uuid.UUID(document['agent_id']));values=deepcopy(stored.nodes);closed_root({'nodes':values})['children']=[message('Forbidden legacy reply')];stored.nodes=values;db.commit()
    result=event(client,path,headers).json();assert result['closed'] and result['automation_error']
    assert [item['content'] for item in result['messages']]==['Goodbye.']


def test_close_chat_definition_save_remains_revision_permission_and_csrf_checked(client,workflow,app):
    path,document,headers=workflow;document['nodes'][0]['children']=[close_action()]
    assert save(client,path,document,{}).status_code==403
    response=save(client,path,document,headers);assert response.status_code==200
    document['nodes'][0]['children'][0]['name']='Changed close'
    assert save(client,path,document,headers).status_code==409
    with app.state.session_factory() as db:db.scalar(select(Membership)).role='member';db.commit()
    assert event(client,path,headers).status_code==403
