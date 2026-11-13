"""Permanent Chat Closed boundaries, Jump safety and durable CSAT collection."""
from copy import deepcopy
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.models import AgentConversation, AgentMessage, Membership, User, WorkflowDocument
from app.workflows.actions.collect_feedback.config import CollectFeedbackConfig
from app.workflows.actions.collect_feedback.handler import execute as collect_feedback
from app.workflows.context import ActionContext
from app.workflows.defaults import node_id
from app.workflows.post_close import POST_CLOSE_ACTIONS, PostCloseFlow
from app.workflows.service import validate_nodes
from test_ask_survey import field, survey
from test_branch import branch, variable
from test_jump import jump, prepare, run, setup_tree
from test_send_api import api as send_api, StubAPI as FakeAPI
from test_sql_query import sql
from test_show_carousel import carousel
from test_send_attachment import attachment
from test_switch_language import switch
from test_workflows import FakeLLM, contents, event, intent, message, node, save, workflow


def feedback(enabled=True, title='Please rate the conversation', **kwargs):
    return node('action', 'collect_feedback', config={'rating_enabled': enabled,
        'translations': {'en': {'webchat_rating_title': title}}}, **kwargs)


def closed_root(document):
    values = document['nodes'] if isinstance(document, dict) else document.nodes
    return next(value for value in values if value['type'] == 'chat_closed')


def save_closed(client, path, document, headers, actions):
    closed_root(document)['children'] = actions
    response = save(client, path, document, headers)
    assert response.status_code == 200, response.text
    return response.json()


def close(client, path, headers, conversation=None):
    if not conversation: conversation = event(client, path, headers).json()['conversation_id']
    response = event(client, path, headers, 'chat_closed', conversation)
    assert response.status_code == 200, response.text
    return response.json()


def rate(client, path, headers, result, rating=5, token=None):
    return event(client, path, headers, 'collect_feedback', result['conversation_id'], rating=rating,
        feedback_token=token if token is not None else (result.get('feedback') or {}).get('token'))


@pytest.mark.parametrize('config', [{}, {'rating_enabled': True}, {'rating_enabled': 'true'}, {'rating_enabled': 1},
    {'translations': {'en': {'webchat_rating_title': ' '}}}, {'translations': {'en': {'webchat_rating_title': 'X' * 501}}},
    {'translations': {'xx': {'webchat_rating_title': 'Rate us'}}}, {'translations': {'en': {'webchat_rating_title': 'Rate us', 'html': 'bad'}}},
    {'translations': {'en': {'webchat_rating_title': 'Rate us'}}, 'extra': True}])
def test_feedback_settings_are_strict(config):
    with pytest.raises(ValidationError): CollectFeedbackConfig.model_validate(config)


def test_disabled_rating_allows_empty_configuration():
    parsed = CollectFeedbackConfig(rating_enabled=False)
    assert not parsed.rating_enabled and parsed.translations == {}


@pytest.mark.parametrize('change', ['delete', 'disable', 'nest', 'replace_id', 'duplicate'])
def test_chat_closed_is_permanent_enabled_and_top_level(change):
    agent, document = setup_tree(); root = closed_root(document)
    if change == 'delete': document.nodes.remove(root)
    elif change == 'disable': root['enabled'] = False
    elif change == 'nest': document.nodes.remove(root); document.nodes[0]['children'].append(root)
    elif change == 'replace_id': root['id'] = str(uuid.uuid4())
    else: document.nodes.append(deepcopy(root))
    with pytest.raises(HTTPException) as error: validate_nodes(document.nodes, agent.id)
    assert error.value.status_code == 422


@pytest.mark.parametrize('bad', ['message', 'attachment', 'carousel', 'survey', 'message_trigger', 'attachment_trigger', 'feedback_trigger', 'fallback'])
@pytest.mark.parametrize('nested', [False, True])
def test_conversational_steps_cannot_be_added_anywhere_under_chat_closed(bad, nested):
    agent, document = setup_tree()
    actions = {
        'message': lambda: message('No chat message'), 'attachment': attachment, 'carousel': carousel,
        'survey': lambda: survey(field()), 'message_trigger': lambda: intent('No reply'),
        'attachment_trigger': lambda: node('trigger', 'attachment_received'),
        'feedback_trigger': lambda: node('trigger', 'collect_feedback'), 'fallback': lambda: node('trigger', 'fallback')}
    value = actions[bad](); value['enabled'] = False
    closed_root(document)['children'] = [branch(('True', [value]))] if nested else [value]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422


def test_catalogue_has_exact_post_close_allow_list_with_planned_entries(client, workflow):
    path, document, _ = workflow
    assert len(document['actions']) == 20
    assert {value['type'] for value in document['actions'] if value['allowed_after_close']} == set(POST_CLOSE_ACTIONS)
    assert len(POST_CLOSE_ACTIONS) == 13
    assert next(value for value in document['actions'] if value['type'] == 'collect_feedback')['post_close_only']
    assert all(not value['allowed_after_close'] for value in document['actions'] if value['type'] in {'send_message','send_attachment','show_carousel','ask_survey'})
    for kind in ['create_ticket','auto_fill_field','auto_team_match','send_graph']:
        assert not next(value for value in document['actions'] if value['type'] == kind)['available']
    root = closed_root(document)
    assert root['id'] == node_id(document['agent_id'], 'chat_closed') and root['enabled']


@pytest.mark.parametrize('destination', ['direct_message', 'following_message', 'nested_message', 'branch_else', 'api_error', 'sql_timeout', 'waiting_trigger', 'jump_chain'])
def test_jump_cannot_escape_the_post_close_boundary(destination):
    agent, document = setup_tree()
    target = variable('logged', 'True'); shared = intent('shared', [target])
    if destination == 'direct_message': shared['children'] = [message('Blocked')]; target = shared['children'][0]
    elif destination == 'following_message': shared['children'].append(message('Blocked continuation'))
    elif destination == 'nested_message': target['children'] = [message('Blocked child')]
    elif destination == 'branch_else': target = branch(('True', [variable('safe', 'True')]), otherwise=[message('Blocked Else')]); shared['children'] = [target]
    elif destination == 'api_error': target = send_api(error=[message('Blocked error')]); shared['children'] = [target]
    elif destination == 'sql_timeout': target = sql(timeout=[message('Blocked timeout')]); shared['children'] = [target]
    elif destination == 'waiting_trigger': target['children'] = [intent('need reply', [variable('unsafe', 'True')])]
    elif destination == 'jump_chain': target['children'] = [jump(message('Missing'))]; bad = message('Blocked final target'); target['children'][0]['config']['target_node_id'] = bad['id']; document.nodes.append(intent('bad', [bad]))
    document.nodes.append(shared); closed_root(document)['children'] = [jump(target)]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422


def test_jump_can_reuse_a_safe_trigger_and_its_continuations():
    agent, document = setup_tree(); shared = intent('shared', [variable('logged', 'True'), branch(('True', [variable('reported', 'True')]))])
    document.nodes.append(shared); closed_root(document)['children'] = [jump(shared)]
    prepare(agent, document); result = run(agent, document, event_type='chat_closed')
    assert result[0]['closed'] and result[0]['variables'] == {'logged': True, 'reported': True}
    assert result[1] == [] and not result[0]['stack']


def test_normal_workflows_cannot_jump_into_chat_closed_steps():
    agent, document = setup_tree(); target = variable('after_close', 'True')
    closed_root(document)['children'] = [target]; document.nodes[0]['children'] = [jump(target)]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422 and 'Chat Closed' in error.value.detail


def test_collect_feedback_is_an_action_scoped_to_chat_closed():
    agent, document = setup_tree(); document.nodes[0]['children'] = [feedback()]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422 and 'beneath Chat Closed' in error.value.detail
    context = ActionContext(agent, 'en', {'event': 'message_received'}, {'closed': False})
    with pytest.raises(ValueError, match='closure'): collect_feedback(feedback()['config'], context)


def test_closing_requests_feedback_without_a_message_and_continues_after_submission(client, workflow, app):
    path, document, headers = workflow
    save_closed(client, path, document, headers, [feedback(), variable('closure_logged', 'True')])
    result = close(client, path, headers)
    assert result['closed'] and result['messages'] == [] and 'closure_logged' not in result['variables']
    assert result['feedback']['title'] == 'Please rate the conversation' and result['feedback']['min_rating'] == 1 and result['feedback']['max_rating'] == 5
    assert any(value['outcome'] == 'rating_requested' for value in result['trace'])
    with app.state.session_factory() as db:
        state = db.get(AgentConversation, uuid.UUID(result['conversation_id'])).workflow_state
        assert state['closed'] and state['close_automation']['status'] == 'waiting_feedback'
        assert state['closed_at'] and state['close_source'] == 'tester' and state['feedback_request']['token'] == result['feedback']['token']
    submitted = rate(client, path, headers, result, 4)
    assert submitted.status_code == 200 and submitted.json()['variables']['closure_logged']
    assert submitted.json()['variables']['feedback_rating'] == 4


@pytest.mark.parametrize('enabled', [False, None])
def test_no_unrequested_feedback_when_rating_disabled_or_action_absent(client, workflow, enabled):
    path, document, headers = workflow
    save_closed(client, path, document, headers, [feedback(False)] if enabled is False else [])
    result = close(client, path, headers)
    assert result['closed'] and not result['feedback']
    assert rate(client, path, headers, result, token=str(uuid.uuid4())).status_code == 409


def test_rating_title_is_localised_and_can_use_existing_variables(client, workflow):
    path, document, headers = workflow; agent = client.get(path).json()
    assert client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en','fr'], 'default_language': 'en'}, headers=headers).status_code == 200
    action = feedback(); action['config']['translations']['fr'] = {'webchat_rating_title': 'Évaluez votre conversation avec {_chatbot.name}'}
    save_closed(client, path, document, headers, [switch(), action])
    result = close(client, path, headers)
    assert result['language'] == 'fr' and result['feedback']['language'] == 'fr'
    assert result['feedback']['title'] == 'Évaluez votre conversation avec Default AI Agent'


def test_rating_title_falls_back_to_default_language(client, workflow):
    path, document, headers = workflow; agent = client.get(path).json()
    client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en','fr'], 'default_language': 'en'}, headers=headers)
    save_closed(client, path, document, headers, [switch(), feedback()])
    assert close(client, path, headers)['feedback']['title'] == 'Please rate the conversation'


def test_a_rating_is_saved_once_and_feedback_trigger_can_acknowledge_it(client, workflow, app):
    path, document, headers = workflow
    document['nodes'].append(node('trigger', 'collect_feedback', children=[message('Thank you for {feedback.rating} / 5')]))
    save_closed(client, path, document, headers, [feedback()])
    result = close(client, path, headers); response = rate(client, path, headers, result, 4)
    assert contents(response) == ['Thank you for 4 / 5']
    saved = response.json(); assert saved['closed'] and saved['feedback_submitted'] and saved['csat_rating'] == 4 and saved['feedback'] is None
    assert rate(client, path, headers, result, 5).status_code == 409
    with app.state.session_factory() as db:
        state = db.get(AgentConversation, uuid.UUID(result['conversation_id'])).workflow_state
        assert state['feedback_submitted_at'] and state['csat_rating'] == 4 and 'feedback_request' not in state


def test_rating_without_a_feedback_trigger_does_not_run_fallback(client, workflow):
    path, document, headers = workflow
    fallback = next(value for value in document['nodes'] if value['type'] == 'fallback'); fallback.update(enabled=True, children=[message('Do not send fallback')])
    save_closed(client, path, document, headers, [feedback()])
    result = close(client, path, headers); response = rate(client, path, headers, result)
    assert response.status_code == 200 and response.json()['messages'] == []
    assert response.json()['notice'] == 'Feedback saved.' and response.json()['feedback_submitted']


@pytest.mark.parametrize('value', [0, 6, True, '5', 4.5, None])
def test_rating_requires_a_strict_integer_in_range(client, workflow, value):
    path, document, headers = workflow; save_closed(client, path, document, headers, [feedback()])
    result = close(client, path, headers)
    assert rate(client, path, headers, result, value).status_code == 422
    assert rate(client, path, headers, result, 5).status_code == 200


def test_missing_wrong_and_cross_conversation_tokens_are_rejected(client, workflow):
    path, document, headers = workflow; save_closed(client, path, document, headers, [feedback()])
    first = close(client, path, headers); second = close(client, path, headers)
    assert event(client, path, headers, 'collect_feedback', first['conversation_id'], rating=5).status_code == 409
    assert rate(client, path, headers, first, token=str(uuid.uuid4())).status_code == 409
    assert rate(client, path, headers, first, token=second['feedback']['token']).status_code == 409
    assert rate(client, path, headers, first).status_code == 200
    assert rate(client, path, headers, second).status_code == 200


def test_closure_interrupts_survey_and_rejects_later_chat_events(client, workflow):
    path, document, headers = workflow
    document['nodes'][0]['children'] = [survey(field('customer_name'))]
    save_closed(client, path, document, headers, [feedback()])
    first = event(client, path, headers).json(); assert first['survey']
    result = close(client, path, headers, first['conversation_id']); assert result['survey'] is None and not result['waiting']
    assert event(client, path, headers, 'message_received', first['conversation_id'], content='Chris', survey_answer_token=first['survey']['answer_token']).status_code == 409
    for kind in ['chat_closed', 'chat_timeout', 'ai_timeout']:
        assert event(client, path, headers, kind, first['conversation_id']).status_code == 409


def test_failed_close_automation_preserves_closure_and_rolls_back_partial_work(client, workflow, app):
    path, document, headers = workflow
    save_closed(client, path, document, headers, [variable('partial', 'True'), feedback(title='{missing_title}')])
    result = close(client, path, headers)
    assert result['closed'] and result['automation_error'] and not result['feedback'] and not result['variables'].get('partial')
    assert result['trace'][0]['outcome'] == 'automation_failed'
    assert event(client, path, headers, 'chat_closed', result['conversation_id']).status_code == 409
    with app.state.session_factory() as db:
        state = db.get(AgentConversation, uuid.UUID(result['conversation_id'])).workflow_state
        assert state['closed'] and state['close_automation']['status'] == 'failed' and state['closed_at']


def test_legacy_unsafe_close_workflow_is_stopped_before_sending_messages(client, workflow, app):
    path, document, headers = workflow
    closed_root(document)['children'] = [message('Old unsafe greeting')]
    with app.state.session_factory() as db:
        stored = db.get(WorkflowDocument, uuid.UUID(document['agent_id'])); stored.nodes = document['nodes']; db.commit()
    result = close(client, path, headers)
    assert result['closed'] and result['messages'] == [] and result['automation_error']
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.content == 'Old unsafe greeting')) == 0


def test_feedback_callback_failure_keeps_the_accepted_rating(client, workflow, app):
    path, document, headers = workflow
    document['nodes'].append(node('trigger', 'collect_feedback', children=[message('{missing_acknowledgment}')]))
    save_closed(client, path, document, headers, [feedback()])
    result = close(client, path, headers); response = rate(client, path, headers, result, 3)
    assert response.status_code == 200
    value = response.json(); assert value['closed'] and value['feedback_submitted'] and value['csat_rating'] == 3 and value['automation_error']
    assert rate(client, path, headers, result, 4).status_code == 409


def test_multiple_feedback_actions_only_issue_one_prompt(client, workflow):
    path, document, headers = workflow
    save_closed(client, path, document, headers, [feedback(title='First title'), feedback(title='Second title')])
    result = close(client, path, headers)
    assert result['feedback']['title'] == 'First title'
    assert sum(value['outcome'] == 'rating_requested' for value in result['trace']) == 1


def test_feedback_prompt_survives_later_workflow_changes(client, workflow):
    path, document, headers = workflow
    document = save_closed(client, path, document, headers, [feedback()]); result = close(client, path, headers)
    save_closed(client, path, document, headers, [feedback(False)])
    assert rate(client, path, headers, result, 5).status_code == 200


def test_feedback_requires_conversation_ownership_csrf_and_permission(client, workflow, app):
    path, document, headers = workflow; save_closed(client, path, document, headers, [feedback()]); result = close(client, path, headers)
    assert rate(client, path, {}, result).status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    assert rate(client, path, headers, result).status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'owner'
        other = User(email='other-feedback-owner@example.com'); db.add(other); db.flush()
        db.get(AgentConversation, uuid.UUID(result['conversation_id'])).user_id = other.id; db.commit()
    assert rate(client, path, headers, result).status_code == 404


def test_close_can_log_via_send_api_and_branch_without_messages(client, workflow, app):
    path, document, headers = workflow; fake = FakeAPI(); app.state.send_api_client = fake
    save_closed(client, path, document, headers, [send_api(success=[branch(('complaint_status.ok', [feedback()]))], url='https://api.example.test/close-log')])
    result = close(client, path, headers)
    assert result['closed'] and result['feedback'] and result['messages'] == [] and len(fake.calls) == 1
    assert event(client, path, headers, 'chat_closed', result['conversation_id']).status_code == 409 and len(fake.calls) == 1


def test_legacy_generative_endpoint_cannot_resume_a_closed_conversation(client, workflow, app):
    path, document, headers = workflow
    fake = FakeLLM(); app.state.llm_tester = fake
    save_closed(client, path, document, headers, [feedback()])
    first = client.post(path + '/tester/messages', headers=headers, json={'content': 'Hello'})
    assert first.status_code == 200
    conversation = first.json()['conversation_id']
    result = close(client, path, headers, conversation)
    assert result['closed'] and result['feedback'] and result['messages'] == []
    response = client.post(path + '/tester/messages', headers=headers,
        json={'content': 'A late reply', 'conversation_id': conversation})
    assert response.status_code == 409 and len(fake.calls) == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentMessage)
            .where(AgentMessage.conversation_id == uuid.UUID(conversation))) == 2
