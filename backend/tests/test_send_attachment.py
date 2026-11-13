"""Workflow uploads, delivery, localisation, ownership and immutable chat files."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import uuid
import zipfile

from alembic import command
from alembic.config import Config
from PIL import Image
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select, text

from app.models import Agent, AgentAttachment, AgentConversation, AgentMessage, Membership, User, WorkflowAsset
from app.workflows.actions.send_attachment.config import SendAttachmentConfig
from app.workflows.actions.send_attachment.files import FORMATS, MAX_FILE_BYTES, clean_name, link_metadata, validate_file
from test_ask_survey import field, survey, upload as survey_upload
from test_branch import branch, variable
from test_jump import jump
from test_switch_language import switch
from test_workflows import event, intent, message, node, save, workflow

PDF = b'%PDF-1.4\n% Example complaint form\n%%EOF\n'


def image_bytes(kind='PNG'):
    stream = BytesIO(); Image.new('RGB', (12, 8), '#e58a48').save(stream, format=kind)
    return stream.getvalue()


def translation(source='link', file_format='document', **kwargs):
    return dict(file_format=file_format, source=source, asset_id=None, url='https://example.com/form.pdf' if source == 'link' else '',
        chat_script="'https://example.com/form.pdf'" if source == 'script' else '', **kwargs)


def attachment(value=None, **kwargs):
    return node('action', 'send_attachment', config={'translations': {'en': value or translation()}}, **kwargs)


def upload(client, path, headers, name='complaint-form.pdf', data=PDF, file_format='document'):
    return client.post(path + '/workflow-assets', data={'file_format': file_format},
        files={'file': (name, data, 'application/octet-stream')}, headers=headers)


def uploaded(client, path, headers, **kwargs):
    response = upload(client, path, headers, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def store_workflow(client, path, document, headers, actions):
    document['nodes'][0]['children'] = actions
    response = save(client, path, document, headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('mutation', ['empty', 'language', 'format', 'source', 'missing_asset', 'bad_asset', 'blank_url',
    'javascript', 'credentials', 'fragment', 'placeholder', 'wrong_extension', 'long_url', 'blank_script', 'statement', 'import', 'extra', 'boolean'])
def test_config_rejects_invalid_fields(mutation):
    value = translation(); config = {'translations': {'en': value}}
    if mutation == 'empty': config['translations'] = {}
    elif mutation == 'language': config['translations'] = {'zz': value}
    elif mutation == 'format': value['file_format'] = 'executable'
    elif mutation == 'source': value['source'] = 'local_path'
    elif mutation == 'missing_asset': value.update(source='upload')
    elif mutation == 'bad_asset': value.update(source='upload', asset_id='not-uuid')
    elif mutation == 'blank_url': value['url'] = ''
    elif mutation == 'javascript': value['url'] = 'javascript:alert(1)'
    elif mutation == 'credentials': value['url'] = 'https://user:password@example.com/a.pdf'
    elif mutation == 'fragment': value['url'] += '#secret'
    elif mutation == 'placeholder': value['url'] = 'https://example.com/{form}.pdf'
    elif mutation == 'wrong_extension': value['url'] = 'https://example.com/a.png'
    elif mutation == 'long_url': value['url'] += '?x=' + 'a' * 2048
    elif mutation == 'blank_script': value.update(source='script', chat_script='')
    elif mutation == 'statement': value.update(source='script', chat_script='url = "x"')
    elif mutation == 'import': value.update(source='script', chat_script='__import__("os")')
    elif mutation == 'extra': value['bytes'] = 'untrusted'
    elif mutation == 'boolean': value['url'] = True
    with pytest.raises(ValidationError): SendAttachmentConfig.model_validate(config)


@pytest.mark.parametrize('url', ['https://cdn.example.com/a.pdf?token=abc', 'http://localhost:8000/file', 'https://example.com', 'https://example.com/download.php?file=123', 'https://例え.テスト/form.pdf'])
def test_links_support_query_strings_and_extensionless_endpoints(url):
    file = link_metadata(url, 'document')
    assert file['url'] == url and file['size_bytes'] is None and file['external']


@pytest.mark.parametrize('suffix,kind', [(suffix, kind) for kind, entries in FORMATS.items() for suffix in entries])
def test_every_supported_upload_extension(suffix, kind):
    if kind == 'image': data = image_bytes({'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'gif': 'GIF'}[suffix])
    elif suffix == 'pdf': data = PDF
    elif suffix == 'csv': data = b'id,name\n1,Chris\n'
    elif suffix in {'doc', 'xls', 'ppt'}: data = bytes.fromhex('d0cf11e0a1b11ae1') + b'\x00' * 32
    elif suffix in {'docx', 'xlsx', 'pptx'}:
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr({'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml', 'pptx': 'ppt/presentation.xml'}[suffix], '<document/>')
        data = stream.getvalue()
    elif suffix == 'wav': data = b'RIFF\x00\x00\x00\x00WAVEdata'
    elif suffix == 'avi': data = b'RIFF\x00\x00\x00\x00AVI data'
    elif suffix == 'mp3': data = b'ID3\x00\x00\x00\x00'
    else: data = b'\x00\x00\x00\x10ftypisom\x00\x00\x00\x00'
    assert validate_file('file.' + suffix.upper(), data, kind) == FORMATS[kind][suffix]


@pytest.mark.parametrize('name,data,kind', [
    ('form.exe', b'X', 'document'), ('image.svg', b'<svg/>', 'image'), ('a.pdf', b'', 'document'),
    ('a.pdf', b'not pdf', 'document'), ('a.png', b'<html/>', 'image'), ('a.jpg', image_bytes(), 'image'),
    ('a.docx', b'not zip', 'document'), ('a.xlsx', b'PK\x03\x04', 'document'), ('a.csv', b'a\x00b', 'document'),
    ('a.mp3', b'fake', 'audio'), ('a.wav', b'RIFFfake', 'audio'), ('a.mp4', b'video', 'video'),
    ('a.mov', b'1234xxxx', 'video'), ('a.avi', b'RIFF0000WAVE', 'video'),
    ('a.pdf', b'%PDF-' + b'0' * MAX_FILE_BYTES, 'document')])
def test_invalid_file_bytes_and_size_are_rejected(name, data, kind):
    with pytest.raises(ValueError): validate_file(name, data, kind)


def test_filename_is_plain_basename():
    assert clean_name('C:\\private\\form\x00.pdf') == 'form.pdf'
    assert clean_name('../../form.pdf') == 'form.pdf'
    assert len(clean_name('a' * 300)) == 255


def test_upload_metadata_preview_and_range_download(client, workflow):
    path, document, headers = workflow
    file = uploaded(client, path, headers, name='../../form.pdf')
    assert file['name'] == 'form.pdf' and file['size_bytes'] == len(PDF) and file['content_type'] == 'application/pdf'
    assert 'data' not in file and client.get(path + '/workflow-assets/' + file['id']).json() == file
    assert client.get(file['url']).content == PDF
    assert client.get(file['url'] + '?inline=true').headers['content-type'] == 'application/octet-stream'
    partial = client.get(file['url'], headers={'Range': 'bytes=0-4'})
    assert partial.status_code == 206 and partial.content == b'%PDF-' and partial.headers['content-range'] == 'bytes 0-4/{}'.format(len(PDF))
    assert client.get(file['url'], headers={'Range': 'bytes=-4'}).content == PDF[-4:]
    for value in ['bytes=99999-', 'bytes=5-1', 'bytes=-0', 'bytes=0-1,3-4', 'invalid']:
        assert client.get(file['url'], headers={'Range': value}).status_code == 416
    picture = uploaded(client, path, headers, name='image.png', data=image_bytes(), file_format='image')
    preview = client.get(picture['url'] + '?inline=true')
    assert preview.headers['content-type'] == 'image/png' and preview.content == image_bytes()
    assert preview.headers['x-content-type-options'] == 'nosniff'


def test_larger_than_survey_limit_is_supported_for_outgoing_files(client, workflow):
    path, document, headers = workflow
    data = b'%PDF-' + b'0' * (10 * 1024 * 1024)
    file = uploaded(client, path, headers, data=data)
    assert client.get(file['url']).content == data
    store_workflow(client, path, document, headers, [attachment({**translation('upload'), 'asset_id': file['id']})])
    sent = event(client, path, headers).json()['messages'][0]['attachments'][0]
    assert client.get(sent['url']).content == data
    oversized = upload(client, path, headers, data=b'%PDF-' + b'0' * MAX_FILE_BYTES)
    assert oversized.status_code == 422 and '20 MiB' in oversized.text


def test_saved_upload_sends_exactly_one_file_and_continues(client, workflow, app):
    path, document, headers = workflow
    file = uploaded(client, path, headers)
    value = translation('upload'); value['asset_id'] = file['id']
    action = attachment(value, children=[message('Please complete this form.')])
    saved = store_workflow(client, path, document, headers, [message('Here is your form.'), action, message('Thank you.')])
    assert saved['nodes'][0]['children'][1]['config']['translations']['en']['asset_id'] == file['id']
    response = event(client, path, headers)
    assert response.status_code == 200, response.text
    messages = response.json()['messages']
    assert [item['content'] for item in messages] == ['Here is your form.', '', 'Please complete this form.', 'Thank you.']
    sent = messages[1]; assert sent['source'] == 'send_attachment' and sent['node_id'] == action['id']
    assert sent['quick_replies'] == [] and len(sent['attachments']) == 1
    delivered = sent['attachments'][0]
    assert delivered['id'] != file['id'] and delivered['name'] == file['name'] and not delivered['external']
    assert client.get(delivered['url']).content == PDF
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 1
        stored = db.scalar(select(AgentMessage).where(AgentMessage.content == '', AgentMessage.role == 'assistant'))
        assert stored.message_data['attachments'] == sent['attachments']


def test_replaced_asset_can_be_deleted_without_changing_previous_sent_file(client, workflow):
    path, document, headers = workflow
    file = uploaded(client, path, headers); value = translation('upload'); value['asset_id'] = file['id']
    document = store_workflow(client, path, document, headers, [attachment(value)])
    sent = event(client, path, headers).json()['messages'][0]['attachments'][0]
    deletion = path + '/workflow-assets/' + file['id'] + '/delete'
    assert client.post(deletion, headers=headers).status_code == 409
    document = store_workflow(client, path, document, headers, [attachment()])
    assert client.post(deletion, headers=headers).status_code == 200
    assert client.get(file['url']).status_code == 404
    assert client.get(sent['url']).content == PDF


@pytest.mark.parametrize('source', ['link', 'script'])
def test_hosted_urls_are_delivered_without_a_backend_request(client, workflow, source, monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs): raise AssertionError('The action must not fetch a hosted file.')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    path, document, headers = workflow
    actions = [variable('file_url', "'https://example.com/form.pdf'"), attachment(translation(source))]
    if source == 'script': actions[1]['config']['translations']['en']['chat_script'] = 'file_url'
    store_workflow(client, path, document, headers, actions)
    result = event(client, path, headers)
    assert result.status_code == 200, result.text
    file = result.json()['messages'][0]['attachments'][0]
    assert file['url'] == 'https://example.com/form.pdf' and file['external'] and file['size_bytes'] is None


@pytest.mark.parametrize('script', ['42', 'None', "{'url':'https://example.com/form.pdf'}", 'unknown_file', "'javascript:alert(1)'", "'https://example.com/a.png'", "'/etc/passwd'"])
def test_bad_expression_result_rolls_back_turn(client, workflow, app, script):
    path, document, headers = workflow
    value = translation('script'); value['chat_script'] = script
    store_workflow(client, path, document, headers, [message('Never committed'), attachment(value)])
    response = event(client, path, headers)
    assert response.status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 0
        assert db.scalar(select(func.count()).select_from(AgentConversation)) == 0


def test_switch_language_selects_localised_attachment_and_fallback(client, workflow):
    path, document, headers = workflow
    agent = client.get(path).json()
    assert client.post(path + '/languages', json={'expected_revision': agent['revision'], 'languages': ['en', 'fr'], 'default_language': 'en'}, headers=headers).status_code == 200
    action = attachment()
    action['config']['translations']['fr'] = {**translation(), 'url': 'https://example.com/formulaire.pdf'}
    document = store_workflow(client, path, document, headers, [switch(), action, attachment()])
    response = event(client, path, headers)
    assert response.status_code == 200, response.text
    assert response.json()['language'] == 'fr'
    assert [m['attachments'][0]['name'] for m in response.json()['messages']] == ['formulaire.pdf', 'form.pdf']


def test_two_localised_uploads_remain_independent(client, workflow):
    path, document, headers = workflow
    files = [uploaded(client, path, headers, name=name) for name in ['english.pdf', 'français.pdf']]
    action = attachment(); action['config']['translations'] = {
        code: {**translation('upload'), 'asset_id': file['id']} for code, file in zip(['en', 'fr'], files)}
    store_workflow(client, path, document, headers, [action])
    config = client.get(path + '/workflows').json()['nodes'][0]['children'][0]['config']
    assert config['translations']['en']['asset_id'] != config['translations']['fr']['asset_id']
    assert client.get(files[1]['url']).content == PDF


def test_branch_jump_disabled_and_nested_trigger_integration(client, workflow):
    path, document, headers = workflow
    target = attachment(children=[intent('done', [message('Completed')])])
    document['nodes'].append(intent('form', [message('Skip this'), target]))
    document = store_workflow(client, path, document, headers, [attachment(enabled=False), branch(('True', [jump(target)])), message('Abandoned')])
    first = event(client, path, headers).json()
    assert len(first['messages']) == 1 and first['messages'][0]['source'] == 'send_attachment' and first['waiting']
    second = event(client, path, headers, 'message_received', first['conversation_id'], content='done')
    assert [item['content'] for item in second.json()['messages']] == ['Completed']


def test_script_can_send_current_conversation_survey_file(client, workflow):
    path, document, headers = workflow
    value = translation('script'); value['chat_script'] = 'uploaded_form.url'
    store_workflow(client, path, document, headers, [survey(field('uploaded_form', 'attachment')), attachment(value), message('Received and returned.')])
    current = event(client, path, headers).json()
    response = survey_upload(client, path, headers, current, [('your-form.pdf', PDF, 'application/pdf')])
    assert response.status_code == 200, response.text
    returned = response.json()['messages'][0]['attachments'][0]
    assert returned['id'] == response.json()['variables']['uploaded_form']['id']
    assert client.get(returned['url']).content == PDF


def test_script_cannot_send_another_conversations_survey_file(client, workflow):
    path, document, headers = workflow
    file = uploaded(client, path, headers); value = {**translation('upload'), 'asset_id': file['id']}
    document = store_workflow(client, path, document, headers, [attachment(value)])
    sent = event(client, path, headers).json()['messages'][0]['attachments'][0]
    value = translation('script'); value['chat_script'] = repr(sent['url'])
    store_workflow(client, path, document, headers, [attachment(value)])
    response = event(client, path, headers)
    assert response.status_code == 422 and 'conversation' in response.text


def test_outgoing_snapshots_roll_back_when_later_action_fails(client, workflow, app):
    path, document, headers = workflow
    file = uploaded(client, path, headers)
    value = {**translation('upload'), 'asset_id': file['id']}
    store_workflow(client, path, document, headers, [attachment(value), message('{missing}')])
    assert event(client, path, headers).status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 0
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 0
        assert db.scalar(select(func.count()).select_from(WorkflowAsset)) == 1


def test_attachment_turn_limit_is_transactional(client, workflow):
    path, document, headers = workflow
    document = store_workflow(client, path, document, headers, [attachment() for _ in range(10)])
    assert len(event(client, path, headers).json()['messages']) == 10
    store_workflow(client, path, document, headers, [attachment() for _ in range(11)])
    response = event(client, path, headers)
    assert response.status_code == 422 and '10 attachments' in response.text


def test_upload_and_save_enforce_csrf_manage_permissions_and_revisions(client, workflow, app):
    path, document, headers = workflow
    assert upload(client, path, {}).status_code == 403
    file = uploaded(client, path, headers)
    value = {**translation('upload'), 'asset_id': file['id']}
    store_workflow(client, path, document, headers, [attachment(value)])
    assert save(client, path, document, headers).status_code == 409
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete').status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    assert upload(client, path, headers).status_code == 403
    assert client.post(path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 403


def test_assets_cannot_be_used_or_downloaded_by_another_agent(client, workflow):
    path, document, headers = workflow
    file = uploaded(client, path, headers)
    second = client.post(path.rsplit('/', 1)[0], data={'name': 'Other agent', 'default_language': 'en'}, headers=headers).json()
    other_path = path.rsplit('/', 1)[0] + '/' + second['id']
    assert client.get(other_path + '/workflow-assets/' + file['id']).status_code == 404
    assert client.get(other_path + '/workflow-assets/' + file['id'] + '/file').status_code == 404
    other = client.get(other_path + '/workflows').json()
    other['nodes'][0]['children'] = [attachment({**translation('upload'), 'asset_id': file['id']})]
    assert save(client, other_path, other, headers).status_code == 422
    assert client.post(other_path + '/workflow-assets/' + file['id'] + '/delete', headers=headers).status_code == 404


def test_download_requires_membership_and_sent_file_owner(client, workflow, app):
    path, document, headers = workflow
    file = uploaded(client, path, headers)
    store_workflow(client, path, document, headers, [attachment({**translation('upload'), 'asset_id': file['id']})])
    result = event(client, path, headers).json(); sent = result['messages'][0]['attachments'][0]
    with app.state.session_factory() as db:
        other = User(email='other-attachment@example.com'); db.add(other); db.flush()
        db.get(AgentConversation, uuid.UUID(result['conversation_id'])).user_id = other.id; db.commit()
    assert client.get(sent['url']).status_code == 404
    with app.state.session_factory() as db:
        db.delete(db.scalar(select(Membership))); db.commit()
    assert client.get(file['url']).status_code in {403, 404}


def test_missing_and_wrong_category_asset_references_are_rejected(client, workflow):
    path, document, headers = workflow
    value = {**translation('upload'), 'asset_id': str(uuid.uuid4())}
    document['nodes'][0]['children'] = [branch(('True', [attachment(value)]))]
    assert save(client, path, document, headers).status_code == 422
    file = uploaded(client, path, headers)
    value.update(asset_id=file['id'], file_format='image')
    document['nodes'][0]['children'] = [attachment(value, enabled=False)]
    assert save(client, path, document, headers).status_code == 422


def test_0007_preserves_workflow_and_existing_chat_files(database_engine):
    schema = 'petrix_attachment_upgrade_' + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with database_engine.connect() as connection:
        original = connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes['connection'] = connection
            with connection.begin(): command.upgrade(config, '0006')
            workspace_id, agent_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {'id': workspace_id})
            connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing',false,'','[\"en\"]','en',false,'',1,1,now(),now())"), {'id': agent_id, 'workspace': workspace_id})
            connection.execute(text("INSERT INTO agent_workflows (agent_id,revision,next_node_number,nodes,updated_at) VALUES (:id,7,42,'[]',now())"), {'id': agent_id})
            user_id, conversation_id, file_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO users (id,email,created_at) VALUES (:id,'migration@example.com',now())"), {'id': user_id})
            connection.execute(text("INSERT INTO agent_conversations (id,workspace_id,agent_id,user_id,agent_name,workflow_state,created_at) VALUES (:id,:workspace,:agent,:user,'Existing','{}',now())"), {'id': conversation_id, 'workspace': workspace_id, 'agent': agent_id, 'user': user_id})
            connection.execute(text("INSERT INTO agent_attachments (id,conversation_id,file_name,content_type,size_bytes,data,created_at) VALUES (:id,:conversation,'existing.pdf','application/pdf',:size,:data,now())"), {'id': file_id, 'conversation': conversation_id, 'size': len(PDF), 'data': PDF}); connection.commit()
            with connection.begin(): command.upgrade(config, '0007')
            assert connection.execute(text('SELECT revision,next_node_number,nodes FROM agent_workflows')).one() == (7, 42, [])
            assert connection.execute(text('SELECT count(*) FROM workflow_assets')).scalar() == 0
            assert connection.execute(text('SELECT data FROM agent_attachments WHERE id=:id'), {'id': file_id}).scalar() == PDF
            constraints = connection.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_agent_attachment_size' AND connamespace=current_schema()::regnamespace")).scalars().all()
            assert any('20971520' in value for value in constraints)
            connection.commit()
            with connection.begin(): command.downgrade(config, '0006')
            assert connection.execute(text('SELECT revision FROM agent_workflows')).scalar() == 7
            connection.commit()
        finally:
            connection.rollback(); connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()


def test_upload_quota_rejects_new_file_and_allows_recovery_after_unused_delete(client, workflow, app):
    path, _, headers = workflow
    first = uploaded(client, path, headers)
    with app.state.session_factory() as db:
        agent = db.get(Agent, uuid.UUID(path.rsplit('/', 1)[1]))
        db.add_all([WorkflowAsset(workspace_id=agent.workspace_id, agent_id=agent.id,
            file_name='form-{}.pdf'.format(index), file_format='document', content_type='application/pdf',
            size_bytes=len(PDF), data=PDF) for index in range(99)])
        db.commit()
    response = upload(client, path, headers)
    assert response.status_code == 422 and 'storage is full' in response.text
    assert client.post(path + '/workflow-assets/' + first['id'] + '/delete', headers=headers).status_code == 200
    assert upload(client, path, headers).status_code == 201
