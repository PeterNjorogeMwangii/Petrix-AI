"""HTTPS request configuration, variables, paths, and authenticated execution."""
from copy import deepcopy
import json
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.workflows.actions.send_api.client import APIResult
from app.workflows.actions.send_api.config import SendAPIConfig
from app.workflows.actions.send_api.handler import execute as send_request, prepare_request
from app.workflows.context import ActionContext
from app.workflows.numbering import assign_numbers, walk
from app.workflows.runtime import execute
from app.workflows.service import validate_nodes
from test_ask_survey import field, survey
from test_branch import branch, variable
from test_jump import jump, prepare, setup_tree, texts
from test_workflows import contents, event, intent, message, node, save, workflow


def api(success=None, error=None, timeout=None, **settings):
    config = dict(method='GET', url='https://api.example.test/complaints/{complaint_ref}', headers=[], payload_mode='json',
        payload='', variable_name='complaint_status', timeout_seconds=30,
        success_path_id=str(uuid.uuid4()), error_path_id=str(uuid.uuid4()), timeout_path_id=str(uuid.uuid4()))
    config.update(settings)
    result = node('action', 'send_api', name='Complaint lookup', config=config)
    result['paths'] = [dict(id=config[key + '_path_id'], children=children or []) for key, children in
        [('success', success), ('error', error), ('timeout', timeout)]]
    return result


def config(**settings):
    return SendAPIConfig.model_validate(api(**settings)['config']).model_dump(mode='json')


class StubAPI:
    def __init__(self, *results):
        self.results = list(results) or [APIResult('success', 200, {'status': 'resolved'})]
        self.calls = []

    def send(self, method, url, headers, body, timeout_seconds):
        self.calls.append(dict(method=method, url=url, headers=headers, body=body, timeout_seconds=timeout_seconds))
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


def run_api(agent, document, client, state=None, event_type='chat_started', content=''):
    return execute(document, agent, state or {}, {'event': event_type, 'content': content}, api_client=client)


@pytest.mark.parametrize('settings', [
    {'method': 'HEAD'}, {'method': 'get'}, {'url': 'http://api.example.test'}, {'url': 'ftp://api.example.test'},
    {'url': 'https://'}, {'url': 'https://user:secret@api.example.test'}, {'url': 'https://api.example.test/#fragment'},
    {'url': 'https://api.example.test/#'}, {'url': 'https://api.example.test:0'}, {'url': 'https://api.example.test:70000'},
    {'url': 'https://{host}/account'}, {'url': 'https://api.example.test/{bad-name}'}, {'url': 'https://api.example.test/a\r\nb'},
    {'url': 'https://api.example.test\\@127.0.0.1'}, {'url': 'https://[fe80::1%25eth0]'}, {'url': 'https://api.example.test/' + 'x'*2050},
    {'variable_name': '1bad'}, {'variable_name': 'two words'}, {'variable_name': 'status.value'}, {'variable_name': 'class'},
    {'variable_name': 'system'}, {'variable_name': 'language'}, {'variable_name': 'str'}, {'variable_name': 'a'*65},
    {'timeout_seconds': 0}, {'timeout_seconds': 61}, {'timeout_seconds': True}, {'timeout_seconds': 1.5},
    {'payload_mode': 'javascript'}, {'method': 'POST', 'payload': '{broken}'}, {'method': 'POST', 'payload': '{"x": NaN}'},
    {'method': 'POST', 'payload_mode': 'expression', 'payload': 'x = 1'},
    {'method': 'POST', 'payload_mode': 'expression', 'payload': '__import__("os")'},
    {'headers': [{'key': 'Authorization', 'value': 'secret\r\nInjected: yes'}]},
    {'headers': [{'key': 'Bad Header', 'value': 'x'}]}, {'headers': [{'key': 'Host', 'value': 'other.test'}]},
    {'headers': [{'key': 'Content-Length', 'value': '1'}]}, {'headers': [{'key': 'Accept-Encoding', 'value': 'gzip'}]},
    {'headers': [{'key': 'Proxy-Authorization', 'value': 'secret'}]},
    {'headers': [{'key': 'Authorization', 'value': 'a'}, {'key': 'authorization', 'value': 'b'}]},
    {'headers': [{'key': 'X-' + str(i), 'value': 'x'} for i in range(21)]}, {'unknown_setting': 'value'}])
def test_invalid_api_settings_are_rejected_without_network_access(settings):
    with pytest.raises(ValidationError): config(**settings)


@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])
def test_all_five_methods_and_optional_payload(method):
    assert config(method=method)['method'] == method
    if method != 'GET':
        assert config(method=method, payload='{"active":true,"value":null}')['payload']


@pytest.mark.parametrize('collision', ['same_outcome', 'bad_uuid'])
def test_outcome_paths_require_distinct_uuid_identities(collision):
    values = api()['config']
    if collision == 'same_outcome': values['timeout_path_id'] = values['success_path_id']
    else: values['error_path_id'] = '#12'
    with pytest.raises(ValidationError): SendAPIConfig.model_validate(values)


def test_url_query_headers_and_json_values_use_safe_single_pass_substitution():
    agent, _ = setup_tree()
    ctx = ActionContext(agent, 'en', {}, {'variables': {'complaint_ref': '../ABC/12?x=1&next=https://other.test/#part',
        'access_token': 'Bearer-safe-token', 'name': 'Chris"}, "admin":true, "x":"', 'nested': '{agent_name}'}})
    values = config(method='POST', url='https://api.example.test/complaints/{complaint_ref}?reference={complaint_ref}',
        headers=[{'key': 'Authorization', 'value': 'Bearer {access_token}'}], payload='{"name":"{name}","other":"{nested}","empty":""}')
    url, headers, body = prepare_request(values, ctx)
    assert url.startswith('https://api.example.test/complaints/%2E%2E%2FABC%2F12%3Fx%3D1%26')
    assert '&next=' not in url and '#' not in url
    assert headers['Authorization'] == 'Bearer Bearer-safe-token'
    assert headers['Content-Type'] == 'application/json'
    assert json.loads(body) == {'name': ctx.variables['name'], 'other': '{agent_name}', 'empty': ''}


def test_python_payload_preserves_typed_survey_values_objects_and_explicit_content_type():
    agent, _ = setup_tree()
    ctx = ActionContext(agent, 'en', {}, {'variables': {'score': 9, 'name': 'Chris', 'customer': {'id': 1}}})
    values = config(method='PATCH', payload_mode='expression', payload='{"score": score.norm.value, "name": name.text, "customer": customer, "active": True}',
        headers=[{'key': 'content-type', 'value': 'application/vnd.petrix+json'}])
    values['url'] = 'https://api.example.test/customers/1'
    _, headers, body = prepare_request(values, ctx)
    assert json.loads(body) == {'score': 9, 'name': 'Chris', 'customer': {'id': 1}, 'active': True}
    assert 'Content-Type' not in headers


def test_get_never_sends_a_body_even_if_a_saved_payload_exists():
    agent, _ = setup_tree()
    values = config(url='https://api.example.test', payload='invalid ignored content')
    _, headers, body = prepare_request(values, ActionContext(agent, 'en', {}, {}))
    assert body is None and 'Content-Type' not in headers


@pytest.mark.parametrize('values,variables', [
    ({}, {}), ({'headers': [{'key': 'Authorization', 'value': '{access_token}'}]}, {'complaint_ref': '123', 'access_token': 'secret\r\nX: injected'}),
    ({'method': 'POST', 'payload_mode': 'expression', 'payload': 'missing'}, {'complaint_ref': '123'}),
    ({'headers': [{'key': 'X-' + str(i), 'value': 'v'*4000} for i in range(5)]}, {'complaint_ref': '123'})])
def test_request_preparation_failures_use_error_path_without_calling_endpoint(values, variables):
    agent, _ = setup_tree()
    client = StubAPI()
    ctx = ActionContext(agent, 'en', {}, {'variables': variables}, api_client=client)
    result = send_request(config(**values), ctx)
    assert result.outcome == 'error' and result.error == 'request_error' and client.calls == []
    assert ctx.variables['complaint_status'] == {'ok': False, 'status_code': None, 'body': None, 'error': 'request_error'}


@pytest.mark.parametrize('result,expected', [
    (APIResult('success', 200, {'status': 'resolved'}), 'Success'),
    (APIResult('error', 404, {'detail': 'Missing'}, 'http_error'), 'Error'),
    (APIResult('error', error='network_error'), 'Error'), (APIResult('timeout', error='timeout'), 'Timeout')])
def test_exactly_one_outcome_path_runs_and_then_shared_actions_continue(result, expected):
    agent, document = setup_tree()
    action = api([message('Success')], [message('Error')], [message('Timeout')])
    document.nodes[0]['children'] = [variable('complaint_ref', '"REF-123"'), action, message('After API')]
    prepare(agent, document)
    client = StubAPI(result)
    output = run_api(agent, document, client)
    assert texts(output) == [expected, 'After API'] and not output[0]['stack']
    assert output[0]['variables']['complaint_status'] == result.variable()
    trace = next(step for step in output[2] if step['type'] == 'send_api')
    assert trace['outcome'] == result.outcome and trace['path_id'] == action['config'][result.outcome + '_path_id']
    assert trace['status_code'] == result.status_code and trace['error'] == result.error
    assert client.calls[0]['url'] == 'https://api.example.test/complaints/REF-123'


def test_api_response_can_drive_branch_and_message_inside_success():
    agent, document = setup_tree()
    decision = branch(('complaint_status.body.status == "resolved"', [message('Resolved {complaint_status.body.reference}')]), otherwise=[message('Open')])
    document.nodes[0]['children'] = [api([decision], url='https://api.example.test/status')]
    prepare(agent, document)
    assert texts(run_api(agent, document, StubAPI(APIResult('success', 200, {'status': 'resolved', 'reference': 'REF-123'})))) == ['Resolved REF-123']


def test_empty_or_disabled_api_paths_do_not_add_messages_or_call_disabled_actions():
    agent, document = setup_tree()
    action = api(url='https://api.example.test')
    disabled = api([message('Never')], url='https://api.example.test'); disabled['enabled'] = False
    document.nodes[0]['children'] = [action, disabled, message('After')]
    prepare(agent, document)
    client = StubAPI()
    assert texts(run_api(agent, document, client)) == ['After'] and len(client.calls) == 1


def test_survey_inside_api_path_resumes_without_repeating_external_request():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api([survey(field('name')), message('Thanks {name}')], url='https://api.example.test'), message('After')]
    prepare(agent, document)
    client = StubAPI()
    first = run_api(agent, document, client)
    assert texts(first) == ['Question for name']
    second = run_api(agent, document, client, first[0], 'message_received', 'Chris')
    assert texts(second) == ['Thanks Chris', 'After'] and len(client.calls) == 1


def test_trigger_wait_and_fallback_belong_only_to_selected_outcome():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api([intent('yes', [message('Success yes')])],
        [intent('yes', [message('Error yes')]), node('trigger', 'fallback', children=[message('Error fallback')])],
        [node('trigger', 'fallback', children=[message('Wrong timeout')])], url='https://api.example.test'), message('After')]
    prepare(agent, document)
    client = StubAPI(APIResult('error', error='network_error'))
    first = run_api(agent, document, client)
    assert texts(first) == [] and first[0]['stack']
    second = run_api(agent, document, client, first[0], 'message_received', 'other')
    assert texts(second) == ['Error fallback', 'After'] and len(client.calls) == 1


def test_jump_directly_to_api_path_bypasses_request_and_keeps_destination_continuation():
    agent, document = setup_tree()
    target = message('Error destination')
    action = api([message('Wrong success')], [message('Before target'), target, message('Path end')], url='https://api.example.test')
    document.nodes.append(intent('destination', [action, message('Destination end')]))
    document.nodes[0]['children'] = [jump(target), message('Abandoned')]
    prepare(agent, document)
    client = StubAPI()
    assert texts(run_api(agent, document, client)) == ['Error destination', 'Path end', 'Destination end'] and client.calls == []


def test_jump_to_api_runs_request_and_jump_out_abandons_source_path():
    agent, document = setup_tree()
    menu = message('Menu')
    action = api([message('Success'), jump(menu), message('Abandoned path')], url='https://api.example.test')
    document.nodes.append(intent('destination', [action, message('Abandoned destination')]))
    document.nodes.append(intent('menu', [menu]))
    document.nodes[0]['children'] = [jump(action), message('Abandoned source')]
    prepare(agent, document)
    client = StubAPI()
    assert texts(run_api(agent, document, client)) == ['Success', 'Menu'] and len(client.calls) == 1


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'foreign', 'node_collision', 'children', 'duplicate_node', 'nested_system', 'unsupported'])
def test_api_outcome_paths_obey_workflow_integrity_rules(mutation):
    agent, document = setup_tree()
    action = api([message('Success')], [message('Error')], [message('Timeout')], url='https://api.example.test')
    document.nodes[0]['children'] = [action]
    if mutation == 'missing': action['paths'].pop()
    elif mutation == 'extra': action['paths'].append({'id': str(uuid.uuid4()), 'children': []})
    elif mutation == 'duplicate': action['paths'][1] = deepcopy(action['paths'][0])
    elif mutation == 'foreign': action['paths'][0]['id'] = str(uuid.uuid4())
    elif mutation == 'node_collision': action['paths'][0]['children'][0]['id'] = action['paths'][0]['id']
    elif mutation == 'children': action['children'] = [message('Wrong container')]
    elif mutation == 'duplicate_node': action['paths'][2]['children'] = deepcopy(action['paths'][0]['children'])
    elif mutation == 'nested_system': action['paths'][0]['children'] = [document.nodes.pop(1)]
    else: action['paths'][1]['children'] = [node('action', 'sql_query')]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422


@pytest.mark.parametrize('mutation', ['delete', 'disable_api', 'target_container'])
def test_jump_references_into_api_paths_are_validated(mutation):
    agent, document = setup_tree()
    target = message('Success')
    action = api([target], url='https://api.example.test')
    transfer = jump(target)
    document.nodes[0]['children'] = [transfer, action]
    prepare(agent, document)
    if mutation == 'delete': action['paths'][0]['children'] = []
    elif mutation == 'disable_api': action['enabled'] = False
    else: transfer['config']['target_node_id'] = action['config']['success_path_id']
    with pytest.raises(HTTPException) as error: validate_nodes(document.nodes, agent.id)
    assert 'Jump' in error.value.detail


def test_editing_api_settings_and_path_order_preserves_child_numbers():
    agent, document = setup_tree()
    action = api([message('Success')], [message('Error')], [message('Timeout')], url='https://api.example.test')
    document.nodes[0]['children'] = [action]
    prepare(agent, document)
    original = deepcopy(document.nodes)
    numbers = {value['id']: value['number'] for value in walk(original)}
    action['config']['method'] = 'PATCH'; action['config']['payload'] = '{"active":true}'
    action['paths'].reverse()
    validate_nodes(document.nodes, agent.id)
    assign_numbers(document.nodes, original, 100)
    assert {value['id']: value['number'] for value in walk(document.nodes)} == numbers
    assert [path['id'] for path in action['paths']] == [action['config'][outcome + '_path_id'] for outcome in ('success','error','timeout')]


def test_each_new_api_result_overwrites_the_old_body_on_timeout():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api(url='https://api.example.test'), api([message('Wrong')], timeout=[message('Timed out')], url='https://api.example.test')]
    prepare(agent, document)
    client = StubAPI(APIResult('success', 200, {'status': 'old'}), APIResult('timeout', error='timeout'))
    result = run_api(agent, document, client)
    assert texts(result) == ['Timed out'] and result[0]['variables']['complaint_status']['body'] is None


def test_request_count_limit_bounds_external_calls_per_event():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api(error=[message('Limit')], url='https://api.example.test') for _ in range(11)]
    prepare(agent, document)
    client = StubAPI()
    result = run_api(agent, document, client)
    assert len(client.calls) == 10 and texts(result) == ['Limit']
    assert result[0]['variables']['complaint_status']['error'] == 'request_limit'
    run_api(agent, document, client)
    assert len(client.calls) == 20


def test_variable_capacity_is_checked_before_sending_request():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api(url='https://api.example.test')]
    prepare(agent, document)
    client = StubAPI()
    with pytest.raises(HTTPException):
        run_api(agent, document, client, {'variables': {'v'+str(index): index for index in range(100)}})
    assert client.calls == []


def test_response_exceeding_remaining_chat_capacity_uses_error_path():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [api(error=[message('Limit handled')], url='https://api.example.test')]
    prepare(agent, document)
    client = StubAPI(APIResult('success', 200, {'text': 'y'*3000}))
    result = run_api(agent, document, client, {'variables': {'v'+str(index): 'x'*8000 for index in range(8)}})
    assert len(client.calls) == 1 and texts(result) == ['Limit handled']
    assert result[0]['variables']['complaint_status'] == {'ok': False, 'status_code': 200, 'body': None, 'error': 'variable_limit'}
    assert result[0]['variables']['v0'] == 'x'*8000


@pytest.mark.parametrize('outcome,status,body,error_code,expected', [
    ('success', 200, {'status': 'resolved'}, None, 'Status: resolved'),
    ('error', 503, {'message': 'Unavailable'}, 'http_error', 'API failed'),
    ('timeout', None, None, 'timeout', 'Try again later')])
def test_api_save_roundtrip_and_outcome_execution(client, workflow, app, outcome, status, body, error_code, expected):
    path, document, csrf = workflow
    app.state.send_api_client = StubAPI(APIResult(outcome, status, body, error_code))
    action = api([message('Status: {complaint_status.body.status}')], [message('API failed')], [message('Try again later')], url='https://api.example.test')
    document['nodes'][0]['children'] = [action]
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    document = response.json()
    assert save(client, path, document, csrf).json()['revision'] == document['revision']
    result = event(client, path, csrf)
    assert contents(result) == [expected]
    assert result.json()['variables']['complaint_status'] == APIResult(outcome, status, body, error_code).variable()
    assert result.json()['trace'][1]['outcome'] == outcome
    assert client.get(path+'/workflows').json()['nodes'] == document['nodes']


def test_survey_answer_to_api_url_then_branch_and_jump(client, workflow, app):
    path, document, csrf = workflow
    app.state.send_api_client = StubAPI()
    menu = message('Main menu')
    decision = branch(('complaint_status.body.status == "resolved"', [message('Resolved'), jump(menu)]), otherwise=[message('Open')])
    document['nodes'][0]['children'] = [survey(field('complaint_ref')), api([decision])]
    document['nodes'].append(intent('menu', [menu]))
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf).json()
    response = event(client, path, csrf, 'message_received', first['conversation_id'], content='REF-123', survey_answer_token=first['survey']['answer_token'])
    assert contents(response) == ['Resolved', 'Main menu']
    assert app.state.send_api_client.calls[0]['url'].endswith('/REF-123')


def test_file_survey_answer_can_feed_api_payload_without_blocking_async_upload(client, workflow, app):
    path, document, csrf = workflow
    app.state.send_api_client = StubAPI(APIResult('success', 201, {'accepted': True}))
    document['nodes'][0]['children'] = [survey(field('document', 'attachment')),
        api([message('Accepted')], method='POST', url='https://api.example.test/documents', payload_mode='expression', payload='{"file_name": document.name}')]
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf).json()
    response = client.post(path+'/tester/attachments', data={'conversation_id': first['conversation_id'], 'survey_answer_token': first['survey']['answer_token']},
        files=[('files', ('identity.txt', b'IDENTITY', 'text/plain'))], headers=csrf)
    assert contents(response) == ['Accepted']
    assert json.loads(app.state.send_api_client.calls[0]['body']) == {'file_name': 'identity.txt'}


def test_rejected_api_save_retains_workflow_and_does_not_allocate_numbers(client, workflow):
    path, document, csrf = workflow
    original = deepcopy(document)
    document['nodes'][0]['children'] = [api(url='http://api.example.test')]
    response = save(client, path, document, csrf)
    assert response.status_code == 422 and 'HTTPS' in response.json()['detail']
    current = client.get(path+'/workflows').json()
    assert current['revision'] == original['revision'] and current['nodes'] == original['nodes']
    document['nodes'][0]['children'] = [api(url='https://api.example.test')]
    assert save(client, path, document, csrf).json()['nodes'][0]['children'][0]['number'] == 7
