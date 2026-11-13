"""Configured targets, chat persistence, translated continuations and permissions."""
from copy import deepcopy
import uuid

from fastapi import HTTPException
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.llm_tester import LLMTester
from app.models import AgentConversation, AgentMessage, Membership
from app.workflows.actions.switch_language.config import SwitchLanguageConfig
from app.workflows.actions.switch_language.handler import execute as switch_language
from app.workflows.context import ActionContext
from app.workflows.runtime import execute
from test_ask_survey import field, survey
from test_branch import branch, variable
from test_jump import jump, prepare, run, setup_tree, texts
from test_workflows import contents, event, intent, message, node, save, workflow


def switch(code='fr', **kwargs):
    return node('action', 'switch_language', name='Switch to ' + code, config={'target_language': code}, **kwargs)


def translated(english='How can I help?', french='Comment puis-je vous aider ?', **kwargs):
    return node('action', 'send_message', config={'translations': {
        'en': {'messages': [english], 'quick_replies': ['Help']},
        'fr': {'messages': [french], 'quick_replies': ['Aide']}}}, **kwargs)


def multilingual_tree():
    agent, document = setup_tree()
    agent.languages = ['en', 'fr']
    return agent, document


@pytest.mark.parametrize('config', [
    {}, {'target_language': ''}, {'target_language': 'French'}, {'target_language': 'FR'},
    {'target_language': 'xx'}, {'target_language': True}, {'target_language': 1},
    {'target_language': ['fr']}, {'target_language': None}, {'target_language': 'fr', 'extra': 'value'},
])
def test_invalid_config_is_rejected(config):
    with pytest.raises(ValidationError): SwitchLanguageConfig.model_validate(config)


@pytest.mark.parametrize('code', ['en', 'fr', 'sw', 'pt', 'ar'])
def test_language_codes_are_validated_separately_from_agent_configuration(code):
    assert SwitchLanguageConfig(target_language=code).target_language == code


def test_switch_changes_only_chat_context_and_creates_no_variable_or_message():
    agent, _ = multilingual_tree()
    context = ActionContext(agent, 'en', {}, {'variables': {'customer': 'Chris'}})
    assert switch_language({'target_language': 'fr'}, context) is None
    assert context.language == context.state['language'] == 'fr'
    assert context.environment()['language'] == context.environment()['system']['language'] == 'fr'
    assert context.variables == {'customer': 'Chris'}
    assert agent.default_language == 'en' and agent.languages == ['en', 'fr']
    assert switch_language({'target_language': 'fr'}, context) is None


def test_runtime_rejects_unconfigured_target():
    agent, document = setup_tree()
    document.nodes[0]['children'] = [switch(), message('Must not run')]
    prepare(agent, document)
    with pytest.raises(HTTPException, match='configured language'):
        run(agent, document)


def test_messages_quick_replies_branch_and_trace_use_selected_language():
    agent, document = multilingual_tree()
    action = switch(children=[translated()])
    document.nodes[0]['children'] = [action,
        branch(('language == "fr" and system.language == "fr"', [message('French branch')]), otherwise=[message('Wrong')])]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ['Comment puis-je vous aider ?', 'French branch']
    assert result[1][0]['quick_replies'] == ['Aide'] and result[0]['language'] == 'fr'
    change = next(item for item in result[2] if item['type'] == 'switch_language')
    assert change['previous_language'] == 'en' and change['target_language'] == 'fr' and change['node_number']


def test_french_nested_trigger_matches_on_next_turn():
    agent, document = multilingual_tree()
    trigger = intent('Help', [translated('English help', 'Aide en français')])
    trigger['config']['examples']['fr'] = ['Aide']
    document.nodes[0]['children'] = [switch(), translated(children=[trigger])]
    prepare(agent, document)
    first = run(agent, document)
    assert first[0]['stack'] and first[0]['language'] == 'fr'
    second = run(agent, document, first[0], 'message_received', 'Aide')
    assert texts(second) == ['Aide en français'] and second[0]['language'] == 'fr'


def test_survey_pause_and_resume_keep_selected_language():
    agent, document = multilingual_tree()
    document.nodes[0]['children'] = [switch(), survey(field(question='Quel est votre nom ?')), translated()]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(first) == ['Quel est votre nom ?'] and first[0]['language'] == 'fr'
    second = run(agent, document, first[0], 'message_received', 'Chris')
    assert texts(second) == ['Comment puis-je vous aider ?']
    assert second[0]['language'] == 'fr' and second[0]['variables']['customer_name'] == 'Chris'


def test_jump_can_enter_switch_and_then_continue_in_selected_language():
    agent, document = multilingual_tree()
    target = switch(children=[translated()])
    document.nodes[0]['children'] = [jump(target), message('Abandoned')]
    document.nodes.append(intent('destination', [target]))
    prepare(agent, document)
    assert texts(run(agent, document)) == ['Comment puis-je vous aider ?']


def test_switch_back_and_missing_translation_preserve_existing_behavior():
    agent, document = multilingual_tree()
    document.nodes[0]['children'] = [switch(), translated(), switch('en'), translated()]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ['Comment puis-je vous aider ?', 'How can I help?'] and result[0]['language'] == 'en'
    document.nodes[0]['children'] = [switch(), message('Existing default translation')]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ['Existing default translation'] and result[0]['language'] == 'fr'


@pytest.fixture
def multilingual(client, workflow):
    path, document, headers = workflow
    agent = client.get(path).json()
    response = client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en', 'fr'], 'default_language': 'en'}, headers=headers)
    assert response.status_code == 200, response.text
    return path, document, headers


def test_unconfigured_target_is_rejected_at_save_without_changing_workflow(client, workflow):
    path, document, headers = workflow
    original = deepcopy(document['nodes'])
    document['nodes'][0]['children'] = [switch()]
    response = save(client, path, document, headers)
    assert response.status_code == 422 and 'Target language must be configured' in response.json()['detail']
    assert client.get(path + '/workflows').json()['nodes'] == original


def test_save_runtime_persistence_french_matching_and_conversation_isolation(client, multilingual, app):
    path, document, headers = multilingual
    document['nodes'][0]['children'] = [translated()]
    select_french = intent('French', [switch(), translated()])
    select_french['config']['examples']['fr'] = ['Français']
    help_trigger = intent('Help', [translated('English help', 'Aide en français')])
    help_trigger['config']['examples']['fr'] = ['Aide']
    document['nodes'].extend([select_french, help_trigger])
    saved = save(client, path, document, headers)
    assert saved.status_code == 200, saved.text
    assert save(client, path, saved.json(), headers).json()['revision'] == saved.json()['revision']
    first = event(client, path, headers).json()
    french = event(client, path, headers, 'message_received', first['conversation_id'], content='French')
    assert contents(french) == ['Comment puis-je vous aider ?'] and french.json()['language'] == 'fr'
    assert contents(event(client, path, headers, 'message_received', first['conversation_id'], content='Aide')) == ['Aide en français']
    other = event(client, path, headers)
    assert contents(other) == ['How can I help?'] and other.json()['language'] == 'en'
    with app.state.session_factory() as db:
        chat = db.get(AgentConversation, uuid.UUID(first['conversation_id']))
        assert chat.workflow_state['language'] == 'fr'
        messages = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == chat.id)).all()
        assert all(item.message_data.get('source') != 'switch_language' for item in messages)
    assert client.get(path).json()['default_language'] == 'en'


@pytest.mark.parametrize('placement', ['normal', 'disabled', 'branch'])
def test_language_removal_is_blocked_for_every_saved_switch_reference(client, multilingual, placement):
    path, document, headers = multilingual
    target = switch(enabled=placement != 'disabled')
    document['nodes'][0]['children'] = [branch(('True', [target]))] if placement == 'branch' else [target]
    assert save(client, path, document, headers).status_code == 200
    agent = client.get(path).json()
    response = client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en'], 'default_language': 'en'}, headers=headers)
    assert response.status_code == 422 and 'Update or remove' in response.json()['detail']
    assert client.get(path).json()['languages'] == ['en', 'fr']
    document = client.get(path + '/workflows').json(); document['nodes'][0]['children'] = []
    assert save(client, path, document, headers).status_code == 200
    assert client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en'], 'default_language': 'en'}, headers=headers).status_code == 200


def test_version_restore_cannot_remove_a_referenced_language(client, multilingual):
    path, document, headers = multilingual
    document['nodes'][0]['children'] = [switch()]
    assert save(client, path, document, headers).status_code == 200
    agent = client.get(path).json()
    result = client.post(path + '/versions/1/restore', json={'expected_revision': agent['revision']}, headers=headers)
    assert result.status_code == 422 and client.get(path).json()['languages'] == ['en', 'fr']


def test_failed_turn_rolls_back_language_change(client, multilingual, app):
    path, document, headers = multilingual
    document['nodes'][0]['children'] = []
    document['nodes'].append(intent('French', [switch(), variable('answer', 'missing_variable')]))
    assert save(client, path, document, headers).status_code == 200
    first = event(client, path, headers).json()
    result = event(client, path, headers, 'message_received', first['conversation_id'], content='French')
    assert result.status_code == 422
    with app.state.session_factory() as db:
        assert db.get(AgentConversation, uuid.UUID(first['conversation_id'])).workflow_state['language'] == 'en'


def test_workflow_switch_preserves_csrf_permission_and_revision_checks(client, multilingual, app):
    path, document, headers = multilingual
    document['nodes'][0]['children'] = [switch()]
    assert save(client, path, document, {}).status_code == 403
    assert save(client, path, document, headers).status_code == 200
    assert save(client, path, document, headers).status_code == 409
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    document = client.get(path + '/workflows').json()
    assert save(client, path, document, headers).status_code == 403


def test_generative_fallback_receives_active_language_and_prompt(client, multilingual, app, monkeypatch):
    for key in ('ALL_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'http_proxy', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    path, document, headers = multilingual
    document['nodes'][0]['children'] = [switch()]
    assert save(client, path, document, headers).status_code == 200
    first = event(client, path, headers).json()
    app.state.settings.llm_base_url = 'https://model.example/v1'
    app.state.settings.llm_model = 'test-model'
    captured = []
    original_post = httpx.Client.post
    def post(self, url, **kwargs):
        if str(url) != 'https://model.example/v1/chat/completions': return original_post(self, url, **kwargs)
        captured.append(kwargs['json'])
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Une réponse en français'}}]}, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    app.state.llm_tester = LLMTester(app.state.settings)
    result = event(client, path, headers, 'message_received', first['conversation_id'], content='an unmatched question')
    assert contents(result) == ['Une réponse en français'] and result.json()['language'] == 'fr'
    assert 'Active conversation language: fr. Respond in this language.' in captured[0]['messages'][0]['content']
