"""LLM configuration, Groq wire contract and workflow continuation without live calls."""
from copy import deepcopy
from types import SimpleNamespace
import json
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models import Agent, AgentConversation, AgentMessage
from app.workflows.actions.llm_processing import client as provider_module
from app.workflows.actions.llm_processing.client import GroqProcessingClient, ProcessingResult
from app.workflows.actions.llm_processing.config import LLMProcessingConfig
from app.workflows.actions.llm_processing.handler import execute as process
from app.workflows.actions.llm_processing.service import LLMProcessingService
from app.workflows.context import ActionContext
from app.workflows.runtime import execute
from test_ask_survey import field, survey
from test_branch import branch, variable
from test_chat_closed import closed_root, close, save_closed
from test_close_chat import close_action, configure
from test_collect_feedback import form, submit
from test_jump import jump, prepare, setup_tree, texts
from test_workflows import workflow, node, message, save, event, contents


def llm(success=None, error=None, timeout=None, **settings):
    config = dict(input='Describe the complaint.', input_mode='template', model='intent', temperature=1,
        system_prompt='Return a concise, factual summary.', system_prompt_mode='template',
        use_knowledge_info=False, variable_name='complaint_details', timeout_seconds=30,
        **{key + '_path_id': str(uuid.uuid4()) for key in ('success', 'error', 'timeout')})
    config.update(settings)
    result = node('action', 'llm_processing', name='Complaint summary', config=config)
    result['paths'] = [dict(id=config[key + '_path_id'], children=children or []) for key, children in
        [('success', success), ('error', error), ('timeout', timeout)]]
    return result


class StubProcessing:
    model = 'openai/gpt-oss-20b'
    def __init__(self, result=None):
        self.result = result or ProcessingResult('success', text='Customer requests a refund.')
        self.calls = []
    def process(self, *args):
        self.calls.append(args)
        return self.result, 0


class StubProvider:
    available = True
    def __init__(self, result=None):
        self.result = result or ProcessingResult('success', text='Customer requests a refund.')
        self.calls = []
    def process(self, *args):
        self.calls.append(args)
        return self.result


@pytest.mark.parametrize('settings', [
    {'input': ''}, {'input': 'x'*16001}, {'input_mode': 'javascript'}, {'model': 'arbitrary-model'},
    {'temperature': -0.1}, {'temperature': 2.1}, {'temperature': True}, {'temperature': '1'},
    {'temperature': float('nan')}, {'temperature': float('inf')},
    {'system_prompt': 'x'*8001}, {'system_prompt_mode': 'javascript'}, {'use_knowledge_info': 'true'},
    {'variable_name': '1bad'}, {'variable_name': 'system'}, {'variable_name': 'class'},
    {'variable_name': 'language'}, {'variable_name': 'str'}, {'variable_name': 'a.b'},
    {'timeout_seconds': 0}, {'timeout_seconds': 121}, {'timeout_seconds': True}, {'timeout_seconds': 2.5},
    {'input_mode': 'expression', 'input': '__import__("os")'},
    {'system_prompt_mode': 'expression', 'system_prompt': ''}, {'api_key': 'not-allowed'},
])
def test_invalid_processing_settings_are_rejected(settings):
    with pytest.raises(ValidationError): LLMProcessingConfig.model_validate(llm(**settings)['config'])


def test_defaults_model_paths_and_script_configuration():
    value = llm()['config']
    for key in ['temperature','model','timeout_seconds','input_mode','system_prompt_mode','use_knowledge_info','system_prompt']: value.pop(key)
    parsed = LLMProcessingConfig.model_validate(value)
    assert parsed.temperature == 1 and parsed.timeout_seconds == 30 and parsed.model == 'intent'
    value['error_path_id'] = value['success_path_id']
    with pytest.raises(ValidationError): LLMProcessingConfig.model_validate(value)


@pytest.mark.parametrize('result,expected', [
    (ProcessingResult('success', text='Refund requested.'), 'Success: Refund requested.'),
    (ProcessingResult('error', error='provider_error'), 'Error'),
    (ProcessingResult('timeout', error='timeout'), 'Timeout'),
])
def test_exactly_one_outcome_runs_followed_by_shared_continuation(result, expected):
    agent, document = setup_tree()
    document.nodes[0]['children'] = [llm([message('Success: {complaint_details}')], [message('Error')], [message('Timeout')]), message('After')]
    prepare(agent, document); client = StubProcessing(result)
    output = execute(document, agent, {'variables': {'complaint_details': 'stale'}}, {'event': 'chat_started'}, llm_client=client)
    assert texts(output) == [expected, 'After'] and len(client.calls) == 1
    assert output[0]['variables']['complaint_details'] == (result.text if result.outcome == 'success' else None)
    step = next(step for step in output[2] if step['type'] == 'llm_processing')
    assert step['outcome'] == result.outcome and step['model'] == client.model and step['error'] == result.error


def test_text_and_script_inputs_resolve_survey_values_and_structured_expressions():
    agent, _ = setup_tree(); client = StubProcessing()
    context = ActionContext(agent, 'sw', {}, {'variables': {'description': 'Missing deposit', 'score': 4}}, llm_client=client)
    config = LLMProcessingConfig.model_validate(llm(input='Complaint: {description.text}', system_prompt='Score: {score.norm.value}')['config']).model_dump(mode='json')
    assert process(config, context).outcome == 'success'
    assert client.calls[0] == ('Complaint: Missing deposit', 'Score: 4', 1, 30, False, 'sw')
    config.update(input_mode='expression', input='{"complaint": description.text, "rating": score.norm.value}', system_prompt_mode='expression', system_prompt='"Summarise " + description.text')
    process(config, context)
    assert json.loads(client.calls[1][0]) == {'complaint': 'Missing deposit', 'rating': 4}
    assert client.calls[1][1] == 'Summarise Missing deposit'


def test_transform_can_read_its_existing_result_variable_before_replacing_it():
    agent, _ = setup_tree(); client = StubProcessing()
    context = ActionContext(agent, 'en', {}, {'variables': {'complaint_details': 'Existing long text'}}, llm_client=client)
    config = LLMProcessingConfig.model_validate(llm(input='{complaint_details}')['config']).model_dump(mode='json')
    assert process(config, context).outcome == 'success'
    assert client.calls[0][0] == 'Existing long text'


@pytest.mark.parametrize('input,mode', [('{missing}', 'template'), ('missing.text', 'expression'), ('" "', 'expression')])
def test_invalid_inputs_clear_stale_result_without_request(input, mode):
    agent, _ = setup_tree(); client = StubProcessing()
    context = ActionContext(agent, 'en', {}, {'variables': {'complaint_details': 'stale'}}, llm_client=client)
    config = LLMProcessingConfig.model_validate(llm(input=input, input_mode=mode)['config']).model_dump(mode='json')
    assert process(config, context).error == 'invalid_input' and client.calls == []
    assert context.variables['complaint_details'] is None


def test_variable_capacity_is_checked_before_provider_and_invalid_success_is_not_published():
    agent, _ = setup_tree(); client = StubProcessing()
    context = ActionContext(agent, 'en', {}, {'variables': {'v'+str(i): i for i in range(100)}}, llm_client=client)
    config = LLMProcessingConfig.model_validate(llm()['config']).model_dump(mode='json')
    assert process(config, context).error == 'variable_limit' and not client.calls
    context.state['variables'] = {}; client.result = ProcessingResult('success')
    assert process(config, context).error == 'invalid_response' and context.variables['complaint_details'] is None


def test_generated_text_can_drive_branch_and_jump_without_extra_assistant_message():
    agent, document = setup_tree(); target = variable('reused', 'True')
    document.nodes[0]['children'] = [llm([branch(('complaint_details == "refund"', [jump(target)]), otherwise=[message('Wrong')])]), message('Abandoned')]
    document.nodes[-1]['enabled'] = True; document.nodes[-1]['children'] = [target, message('Menu')]
    prepare(agent, document)
    output = execute(document, agent, {}, {'event': 'chat_started'}, llm_client=StubProcessing(ProcessingResult('success', text='refund')))
    assert texts(output) == ['Menu'] and output[0]['variables']['reused'] is True


@pytest.mark.parametrize('mutation', ['missing','extra','duplicate','children','collision','post_close_message'])
def test_llm_outcome_paths_are_validated_and_post_close_restrictions_apply(mutation):
    agent, document = setup_tree(); action = llm([message('Good')])
    document.nodes[0]['children'] = [action]
    if mutation == 'missing': action['paths'].pop()
    elif mutation == 'extra': action['paths'].append({'id': str(uuid.uuid4()), 'children': []})
    elif mutation == 'duplicate': action['paths'][1] = deepcopy(action['paths'][0])
    elif mutation == 'children': action['children'] = [message('Wrong')]
    elif mutation == 'collision': action['paths'][0]['children'][0]['id'] = action['paths'][0]['id']
    else: document.nodes[0]['children'] = []; closed_root(document)['children'] = [action]
    with pytest.raises(HTTPException): prepare(agent, document)


def test_survey_reply_resumes_llm_then_another_survey_and_shared_actions(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    action = llm([survey(field('confirmation')), message('Saved: {complaint_details}')], input='{description.text}')
    document['nodes'][0]['children'] = [survey(field('description')), action, message('Done')]
    assert save(client, path, document, headers).status_code == 200
    first = event(client, path, headers).json(); assert not provider.calls
    second = event(client, path, headers, 'message_received', first['conversation_id'], content='I need a refund.', survey_answer_token=first['survey']['answer_token']).json()
    assert second['survey']['variable_name'] == 'confirmation' and len(provider.calls) == 1
    third = event(client, path, headers, 'message_received', first['conversation_id'], content='Yes', survey_answer_token=second['survey']['answer_token'])
    assert contents(third) == ['Saved: Customer requests a refund.', 'Done'] and len(provider.calls) == 1
    with app.state.session_factory() as db:
        stored = db.get(AgentConversation, uuid.UUID(first['conversation_id']))
        assert stored.workflow_state['variables']['complaint_details'] == 'Customer requests a refund.'
        assert not db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == stored.id, AgentMessage.content == 'Customer requests a refund.')).all()


def test_post_close_feedback_continuation_uses_model_without_sending_a_chat_message(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    feedback = form(); key = feedback['config']['fields'][0]['id']
    save_closed(client, path, document, headers, [feedback, llm([variable('processed', 'True')], input='{feedback_comment}')])
    requested = close(client, path, headers)
    response = submit(client, path, headers, requested, answers={key: 'Helpful agent'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['closed'] and result['messages'] == [] and result['variables']['processed'] is True
    assert result['variables']['complaint_details'] == 'Customer requests a refund.' and len(provider.calls) == 1


def test_same_processing_budget_covers_open_chat_and_close_automation(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    actions = [llm(variable_name='r'+str(i)) for i in range(3)] + [close_action()]
    configure(client, path, document, headers, actions, [llm(error=[variable('limited', 'True')])])
    result = event(client, path, headers).json()
    assert result['closed'] and result['variables']['limited'] is True and len(provider.calls) == 3
    assert any(step.get('error') == 'processing_limit' for step in result['trace'])


@pytest.mark.parametrize('mode,error', [('disabled','llm_disabled'),('unavailable','provider_unavailable'),('knowledge','knowledge_unavailable'),('rate','request_limit')])
def test_service_preconditions_follow_error_path_without_model_call(mode, error, client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    if mode == 'disabled':
        with app.state.session_factory() as db:
            db.get(Agent, uuid.UUID(document['agent_id'])).llm_enabled = False; db.commit()
    if mode == 'unavailable': provider.available = False
    if mode == 'rate': app.state.settings.llm_tester_requests_per_minute = 0
    document['nodes'][0]['children'] = [llm(error=[message('Unable to process.')], use_knowledge_info=mode=='knowledge')]
    assert save(client, path, document, headers).status_code == 200
    response = event(client, path, headers)
    assert contents(response) == ['Unable to process.'] and not provider.calls
    assert any(step.get('error') == error for step in response.json()['trace'])


def test_disabled_llm_does_not_run_or_mutate_result(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    action = llm(); action['enabled'] = False
    document['nodes'][0]['children'] = [variable('complaint_details', '"Keep me"'), action, message('{complaint_details}')]
    assert save(client, path, document, headers).status_code == 200
    assert contents(event(client, path, headers)) == ['Keep me'] and not provider.calls


def test_unhandled_later_script_failure_rolls_back_chat_state(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    document['nodes'][0]['children'] = [llm(), variable('broken', 'missing_variable')]
    assert save(client, path, document, headers).status_code == 200
    response = event(client, path, headers)
    assert response.status_code == 422 and len(provider.calls) == 1
    with app.state.session_factory() as db: assert not db.scalars(select(AgentConversation)).all()


def wire_response(text='Summary', finish='stop'):
    return {'id': 'chat-test', 'object': 'chat.completion', 'created': 1, 'model': 'openai/gpt-oss-20b',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': finish}]}


def mocked_provider(monkeypatch, handler):
    original = provider_module.Groq
    options = []
    def factory(**kwargs):
        options.append({k: v for k,v in kwargs.items() if k != 'http_client'})
        kwargs['http_client'].close()
        kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(handler))
        return original(**kwargs)
    monkeypatch.setattr(provider_module, 'Groq', factory)
    settings = Settings(_env_file=None, app_env='test', database_url='postgresql+psycopg://localhost/unused', session_signing_secret='qa-only-signing-secret-over-32-characters', groq_api_key='qa-provider-key')
    return GroqProcessingClient(settings), options


def test_real_groq_sdk_wire_contract_and_final_text_only(monkeypatch):
    requests = []
    def respond(request): requests.append(request); return httpx.Response(200, json=wire_response('  Factual summary.  '))
    client, options = mocked_provider(monkeypatch, respond)
    result = client.process('openai/gpt-oss-20b', [{'role':'user','content':'Hello'}], 0, 7)
    assert result == ProcessingResult('success', text='Factual summary.')
    request = requests[0]; payload = json.loads(request.content)
    assert str(request.url) == 'https://api.groq.com/openai/v1/chat/completions'
    assert payload['temperature'] == 0 and payload['reasoning_format'] == 'hidden'
    assert payload['max_completion_tokens'] == 4096 and payload['stream'] is False and 'tools' not in payload
    assert options[0]['max_retries'] == 0 and options[0]['timeout'] == 7


@pytest.mark.parametrize('status,body,error', [(200,wire_response(''),'invalid_response'),
    (200,wire_response(None),'invalid_response'),(200,wire_response('Partial','length'),'invalid_response'),
    (200,wire_response('x'*8193),'response_too_large'),(200,wire_response('你'*6000),'response_too_large'),
    (200,{'choices': []},'invalid_response'),(429,{'error':{'message':'PRIVATE provider details'}},'provider_rate_limit'),
    (500,{'error':{'message':'PRIVATE provider details'}},'provider_error')])
def test_provider_failures_are_bounded_sanitized_and_not_retried(status, body, error, monkeypatch):
    calls = []
    def respond(request): calls.append(request); return httpx.Response(status, json=body)
    client, _ = mocked_provider(monkeypatch, respond)
    result = client.process('openai/gpt-oss-20b', [{'role':'user','content':'Hello'}], 1, 2)
    assert result.outcome == 'error' and result.error == error and result.text is None and len(calls) == 1
    assert 'PRIVATE' not in repr(result)


def test_sdk_timeout_maps_to_timeout_path_without_retry(monkeypatch):
    calls = []
    def timed_out(request): calls.append(request); raise httpx.ReadTimeout('PRIVATE timeout context', request=request)
    client, _ = mocked_provider(monkeypatch, timed_out)
    assert client.process('openai/gpt-oss-20b', [{'role':'user','content':'Hello'}], 1, 1) == ProcessingResult('timeout', error='timeout')
    assert len(calls) == 1
