"""Carousel cards, localised buttons, owned images and workflow continuations."""
from copy import deepcopy
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.models import AgentAttachment, AgentConversation, AgentMessage, Membership
from app.workflows.actions.show_carousel.config import ShowCarouselConfig, validate_link
from app.workflows.actions.show_carousel.handler import execute as show_carousel
from app.workflows.assets import asset_ids
from app.workflows.context import ActionContext
from test_ask_survey import field, survey
from test_branch import branch, variable
from test_jump import jump, prepare, run, setup_tree
from test_send_attachment import attachment, image_bytes, store_workflow, uploaded, translation
from test_switch_language import switch
from test_workflows import event, intent, message, node, save, workflow


def button(label='Select', value='Transactions', type='reply', **kwargs):
    return dict(id=str(uuid.uuid4()), label=label, type=type, message=value if type == 'reply' else '',
        url='https://example.com/help#details' if type == 'link' else '', webview=False, **kwargs)


def card(title='Transactions', buttons=None, image=None):
    return dict(id=str(uuid.uuid4()), title=title, subtitle='Help with transactions.', image=image,
        buttons=[button()] if buttons is None else buttons)


def carousel(*cards, **kwargs):
    return node('action', 'show_carousel', config={'translations': {'en': {'cards': list(cards) or [card()]}}}, **kwargs)


@pytest.mark.parametrize('mutation', [
    'empty_translations', 'unsupported_language', 'zero_cards', 'eleven_cards', 'blank_title', 'long_title', 'long_subtitle',
    'four_buttons', 'blank_label', 'long_label', 'blank_message', 'long_message', 'bad_button_type', 'bad_card_id',
    'duplicate_card_id', 'duplicate_button_id', 'card_button_collision', 'empty_link', 'javascript_link', 'coerced_webview',
    'image_missing_upload', 'image_wrong_type', 'image_bad_url', 'extra_card', 'extra_button', 'extra_config',
])
def test_invalid_settings_are_rejected(mutation):
    config = carousel()['config']; cards = config['translations']['en']['cards']; value = cards[0]; item = value['buttons'][0]
    if mutation == 'empty_translations': config['translations'] = {}
    elif mutation == 'unsupported_language': config['translations'] = {'zz': {'cards': cards}}
    elif mutation == 'zero_cards': cards.clear()
    elif mutation == 'eleven_cards': cards[:] = [card(str(index)) for index in range(11)]
    elif mutation == 'blank_title': value['title'] = ' '
    elif mutation == 'long_title': value['title'] = 'X' * 81
    elif mutation == 'long_subtitle': value['subtitle'] = 'X' * 201
    elif mutation == 'four_buttons': value['buttons'] = [button() for _ in range(4)]
    elif mutation == 'blank_label': item['label'] = ' '
    elif mutation == 'long_label': item['label'] = 'X' * 81
    elif mutation == 'blank_message': item['message'] = ' '
    elif mutation == 'long_message': item['message'] = 'X' * 1001
    elif mutation == 'bad_button_type': item['type'] = 'javascript'
    elif mutation == 'bad_card_id': value['id'] = '#1'
    elif mutation == 'duplicate_card_id': cards.append(deepcopy(value))
    elif mutation == 'duplicate_button_id': cards.append(card('Accounts', buttons=[deepcopy(item)]))
    elif mutation == 'card_button_collision': item['id'] = value['id']
    elif mutation == 'empty_link': item.update(type='link', url='')
    elif mutation == 'javascript_link': item.update(type='link', url='javascript:alert(1)')
    elif mutation == 'coerced_webview': item['webview'] = 'false'
    elif mutation == 'image_missing_upload': value['image'] = {'source': 'upload'}
    elif mutation == 'image_wrong_type': value['image'] = {'source': 'link', 'url': 'https://example.com/form.pdf'}
    elif mutation == 'image_bad_url': value['image'] = {'source': 'link', 'url': 'data:image/png;base64,aaa'}
    elif mutation == 'extra_card': value['html'] = '<script/>'
    elif mutation == 'extra_button': item['target_node_id'] = str(uuid.uuid4())
    elif mutation == 'extra_config': config['webview_html'] = '<iframe/>'
    with pytest.raises(ValidationError): ShowCarouselConfig.model_validate(config)


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,hello', 'file:///etc/passwd', '//example.com/path',
    'https://user:password@example.com/help', 'https://example.com/a b', 'https://example.com/\\evil', 'https://example.com/{id}',
    'https://example.com:99999/help', 'https://example.com/' + 'a' * 2048])
def test_unsafe_webpage_urls_are_rejected(url):
    with pytest.raises(ValueError): validate_link(url)


@pytest.mark.parametrize('url', ['https://example.com/help#faq', 'http://localhost:4301/help?q=1', 'HTTPS://example.com/path', 'https://例え.テスト/help'])
def test_complete_webpage_urls_and_anchors_are_supported(url):
    assert validate_link(url) == url


def test_ten_cards_three_buttons_and_optional_images_subtitles_are_valid():
    cards = [card(str(index), buttons=[button(str(n), 'option_' + str(n)) for n in range(3)]) for index in range(10)]
    cards[0].update(image=None, subtitle='', buttons=[])
    parsed = ShowCarouselConfig.model_validate(carousel(*cards)['config']).model_dump(mode='json')
    assert len(parsed['translations']['en']['cards']) == 10
    assert len(parsed['translations']['en']['cards'][1]['buttons']) == 3


def test_inactive_button_and_image_fields_are_cleared():
    first = button(); first.update(url='unneeded', webview=True)
    second = button('Help', type='link'); second.update(message='unneeded', webview=True)
    image = {'source': 'link', 'asset_id': str(uuid.uuid4()), 'url': 'https://example.com/image.png'}
    parsed = ShowCarouselConfig.model_validate(carousel(card(buttons=[first, second], image=image))['config']).model_dump(mode='json')
    value = parsed['translations']['en']['cards'][0]
    assert value['buttons'][0]['url'] == '' and not value['buttons'][0]['webview']
    assert value['buttons'][1]['message'] == '' and value['buttons'][1]['webview']
    assert value['image']['asset_id'] is None


def test_one_output_per_carousel_keeps_card_and_button_order():
    agent, document = setup_tree(); first = card(); second = card('Accounts', [button('Website', type='link')])
    document.nodes[0]['children'] = [message('Choose a service'), carousel(first, second, children=[message('Choose below')])]
    prepare(agent, document); result = run(agent, document)
    assert [value['content'] for value in result[1]] == ['Choose a service', '', 'Choose below']
    output = result[1][1]
    assert output['source'] == 'show_carousel' and output['quick_replies'] == []
    assert [value['title'] for value in output['carousel']['cards']] == ['Transactions', 'Accounts']
    assert output['carousel']['cards'][1]['buttons'][0]['url'] == 'https://example.com/help#details'


def test_card_text_and_reply_values_use_existing_chat_variables():
    agent, document = setup_tree(); item = card('Hello {customer}', [button('{choice}', '{choice}')]); item['subtitle'] = 'For {_chatbot.name}'
    document.nodes[0]['children'] = [variable('customer', "'Chris'"), variable('choice', "'Accounts'"), carousel(item)]
    prepare(agent, document); output = run(agent, document)[1][0]['carousel']['cards'][0]
    assert output['title'] == 'Hello Chris' and output['subtitle'] == 'For Petrix'
    assert output['buttons'][0]['label'] == output['buttons'][0]['message'] == 'Accounts'


@pytest.mark.parametrize('field,value', [('title', '{missing}'), ('title', '{long_text}'), ('subtitle', '{long_text}')])
def test_missing_or_oversized_rendered_card_text_is_controlled(field, value):
    agent, document = setup_tree(); item = card(); item[field] = value
    document.nodes[0]['children'] = [variable('long_text', "'X' * 300"), carousel(item)]
    prepare(agent, document)
    with pytest.raises(HTTPException) as error: run(agent, document)
    assert error.value.status_code == 422


def test_language_fallback_and_independent_card_layouts():
    agent, document = setup_tree(); agent.languages = ['en', 'fr']; action = carousel()
    action['config']['translations']['fr'] = {'cards': [card('Comptes', [button('Choisir', 'Comptes')]), card('Aide', [])]}
    document.nodes[0]['children'] = [switch(), action, carousel()]
    prepare(agent, document); output = run(agent, document)[1]
    assert [value['title'] for value in output[0]['carousel']['cards']] == ['Comptes', 'Aide']
    assert output[1]['carousel']['cards'][0]['title'] == 'Transactions'
    action['config']['translations'].pop('en')
    document.nodes[0]['children'] = [action]; prepare(agent, document)
    assert run(agent, document)[1][0]['carousel']['cards'][0]['title'] == 'Comptes'


def test_reply_selection_runs_nested_trigger_and_shared_continuation(client, workflow):
    path, document, headers = workflow
    action = carousel(card(), card('Accounts', [button('Select', 'Accounts')]), children=[
        intent('Transactions', [variable('category', "'transactions'"), survey(field('reference')), message('Reference {reference}')]),
        intent('Accounts', [message('Account support')])])
    store_workflow(client, path, document, headers, [action, message('Done')])
    first = event(client, path, headers).json(); assert first['waiting'] and len(first['messages']) == 1
    selected = first['messages'][0]['carousel']['cards'][0]['buttons'][0]['message']
    second = event(client, path, headers, 'message_received', first['conversation_id'], content=selected).json()
    assert second['survey'] and second['variables']['category'] == 'transactions'
    result = event(client, path, headers, 'message_received', first['conversation_id'], content='REF-123', survey_answer_token=second['survey']['answer_token'])
    assert result.status_code == 200, result.text
    assert [value['content'] for value in result.json()['messages']] == ['Reference REF-123', 'Done']


def test_french_button_message_matches_french_trigger_and_persists_language(client, workflow):
    path, document, headers = workflow; agent = client.get(path).json()
    assert client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en','fr'], 'default_language': 'en'}, headers=headers).status_code == 200
    trigger = intent('Transactions', [message('Support sélectionné')]); trigger['config']['examples']['fr'] = ['Opérations']
    action = carousel(children=[trigger]); action['config']['translations']['fr'] = {'cards': [card('Opérations', [button('Choisir', 'Opérations')])]}
    store_workflow(client, path, document, headers, [switch(), action])
    first = event(client, path, headers).json(); assert first['language'] == 'fr'
    reply = first['messages'][0]['carousel']['cards'][0]['buttons'][0]['message']
    second = event(client, path, headers, 'message_received', first['conversation_id'], content=reply).json()
    assert second['language'] == 'fr' and second['messages'][0]['content'] == 'Support sélectionné'


def test_branch_jump_and_disabled_carousel_behavior(client, workflow):
    path, document, headers = workflow; target = carousel(children=[intent('Transactions', [message('Selected')])])
    document['nodes'].append(intent('menu', [target]))
    store_workflow(client, path, document, headers, [carousel(enabled=False), branch(('True', [jump(target)])), message('Abandoned')])
    first = event(client, path, headers).json()
    assert len(first['messages']) == 1 and first['messages'][0]['source'] == 'show_carousel'
    second = event(client, path, headers, 'message_received', first['conversation_id'], content='Transactions')
    assert [value['content'] for value in second.json()['messages']] == ['Selected']


def test_upload_image_is_immutable_and_reused_once_per_turn(client, workflow, app):
    path, document, headers = workflow
    file = uploaded(client, path, headers, name='service.png', data=image_bytes(), file_format='image')
    image = {'source': 'upload', 'asset_id': file['id'], 'url': ''}
    document = store_workflow(client, path, document, headers, [carousel(card(image=image), card('Accounts', image=image))])
    first = event(client, path, headers).json(); images = [value['image'] for value in first['messages'][0]['carousel']['cards']]
    assert images[0]['id'] == images[1]['id'] != file['id']
    assert client.get(images[0]['url'] + '?inline=true').content == image_bytes()
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 409
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 1
        saved = db.scalar(select(AgentMessage).where(AgentMessage.role == 'assistant'))
        assert saved.message_data['carousel']['cards'][0]['image']['id'] == images[0]['id']
    store_workflow(client, path, document, headers, [carousel()])
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 200
    assert client.get(images[0]['url']).content == image_bytes()


def test_shared_attachment_and_carousel_image_reference_protects_deletion(client, workflow):
    path, document, headers = workflow
    file = uploaded(client, path, headers, name='shared.png', data=image_bytes(), file_format='image')
    image = {'source': 'upload', 'asset_id': file['id']}
    photo = attachment({**translation('upload', 'image'), 'asset_id': file['id']})
    action = carousel(card(image=image)); action['config']['translations']['fr'] = deepcopy(action['config']['translations']['en'])
    document = store_workflow(client, path, document, headers, [photo, branch(('True', [action]))])
    assert asset_ids(document['nodes']) == {file['id']}
    document = store_workflow(client, path, document, headers, [branch(('True', [action]))])
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 409
    action['enabled'] = False
    store_workflow(client, path, document, headers, [action])
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 409


@pytest.mark.parametrize('problem', ['missing', 'foreign_agent', 'wrong_format'])
def test_bad_image_references_are_rejected_in_nested_disabled_cards(client, workflow, problem):
    path, document, headers = workflow; asset_id = str(uuid.uuid4())
    if problem == 'foreign_agent':
        other = client.post(path.rsplit('/', 1)[0], data={'name': 'Other', 'default_language': 'en'}, headers=headers).json()
        other_path = path.rsplit('/', 1)[0] + '/' + other['id']
        asset_id = uploaded(client, other_path, headers, name='image.png', data=image_bytes(), file_format='image')['id']
    elif problem == 'wrong_format': asset_id = uploaded(client, path, headers)['id']
    document['nodes'][0]['children'] = [branch(('True', [carousel(card(image={'source': 'upload', 'asset_id': asset_id}), enabled=False)]))]
    response = save(client, path, document, headers)
    assert response.status_code == 422


def test_external_images_and_webview_links_are_metadata_without_backend_fetch(client, workflow, monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs): raise AssertionError('Carousel should not fetch the website or hosted image.')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    path, document, headers = workflow
    link = button('Read help', type='link'); link['webview'] = True
    store_workflow(client, path, document, headers, [carousel(card(image={'source': 'link', 'url': 'https://example.com/service.png'}, buttons=[link]))])
    first = event(client, path, headers).json(); value = first['messages'][0]['carousel']['cards'][0]
    assert value['image']['external'] and value['image']['size_bytes'] is None
    assert value['buttons'][0]['webview'] and value['buttons'][0]['url'].endswith('#details')


def test_failed_later_step_rolls_back_carousel_image_copies_and_messages(client, workflow, app):
    path, document, headers = workflow
    file = uploaded(client, path, headers, name='image.png', data=image_bytes(), file_format='image')
    store_workflow(client, path, document, headers, [carousel(card(image={'source': 'upload', 'asset_id': file['id']})), message('{unknown}')])
    assert event(client, path, headers).status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 0
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 0
        assert db.scalar(select(func.count()).select_from(AgentConversation)) == 0


def test_card_and_button_ids_survive_reorder_save_and_noop_revision(client, workflow):
    path, document, headers = workflow; action = carousel(card(), card('Accounts'))
    document = store_workflow(client, path, document, headers, [action]); original = deepcopy(document)
    assert save(client, path, document, headers).json()['revision'] == document['revision']
    document['nodes'][0]['children'][0]['config']['translations']['en']['cards'].reverse()
    saved = save(client, path, document, headers).json()
    old_cards = original['nodes'][0]['children'][0]['config']['translations']['en']['cards']
    new_cards = saved['nodes'][0]['children'][0]['config']['translations']['en']['cards']
    assert new_cards == list(reversed(old_cards))
    assert saved['nodes'][0]['children'][0]['number'] == original['nodes'][0]['children'][0]['number']


def test_carousel_turn_limit_and_existing_permissions_revisions(client, workflow, app):
    path, document, headers = workflow
    document = store_workflow(client, path, document, headers, [carousel() for _ in range(10)])
    assert len(event(client, path, headers).json()['messages']) == 10
    stale = deepcopy(document)
    document = store_workflow(client, path, document, headers, [carousel() for _ in range(11)])
    response = event(client, path, headers); assert response.status_code == 422 and '10 carousels' in response.text
    assert save(client, path, stale, headers).status_code == 409
    assert save(client, path, document, {}).status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    assert save(client, path, document, headers).status_code == 403
