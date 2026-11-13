import io
import json
import uuid
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from PIL import Image
from sqlalchemy import func, select, text

from app.models import Agent, AgentChange, AgentConnection, AgentConversation, AgentMessage, AgentVersion, Membership, Workspace


@pytest.fixture
def agent_workspace(client, signed_in, csrf):
    workspace_id = signed_in['workspace']['id']
    result = client.post('/api/workspaces/{}/setup'.format(workspace_id), data={'name': 'Petrix Lab', 'country_code': 'KE'}, headers=csrf)
    assert result.status_code == 200
    base = '/api/workspaces/{}/agents'.format(workspace_id)
    response = client.get(base)
    assert response.status_code == 200
    return base, response.json()['agents'][0], csrf


def image_bytes(format='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (450, 250), 'orange').save(output, format=format)
    return output.getvalue()


def create(client, base, csrf, name='Banking assistant', language='en', avatar=None):
    kwargs = {'data': {'name': name, 'default_language': language}, 'headers': csrf}
    if avatar:
        kwargs['files'] = {'avatar': ('avatar.png', avatar, 'image/png')}
    return client.post(base, **kwargs)


def test_default_agent_is_created_once(client, app, agent_workspace):
    base, agent, _ = agent_workspace
    assert agent['name'] == 'Default AI Agent' and agent['revision'] == 1
    assert agent['languages'] == ['en'] and agent['default_language'] == 'en'
    assert client.get(base).json()['usage']['count'] == 1
    assert client.get(base).json()['usage']['agent_limit'] == 3
    assert client.get(base + '/' + agent['id'] + '/versions').json()['versions'][0]['number'] == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Agent)) == 1


def test_agents_require_auth_and_onboarding(client, signed_in, csrf):
    base = '/api/workspaces/{}/agents'.format(signed_in['workspace']['id'])
    assert client.get(base).status_code == 409
    assert client.post(base, data={'name': 'Test'}, headers=csrf).status_code == 409
    client.cookies.clear()
    assert client.get(base).status_code == 401


def test_create_and_select_agents_persist(client, agent_workspace):
    base, first, csrf = agent_workspace
    response = create(client, base, csrf, language='sw')
    assert response.status_code == 201
    agent = response.json()
    assert agent['default_language'] == 'sw' and agent['languages'] == ['sw']
    assert client.get(base + '/' + agent['id']).json()['name'] == 'Banking assistant'
    assert {value['id'] for value in client.get(base).json()['agents']} == {first['id'], agent['id']}


@pytest.mark.parametrize('name,language', [(' ', 'en'), ('x'*101, 'en'), ('Good name', 'invalid')])
def test_invalid_create_is_rejected(client, agent_workspace, name, language):
    base, _, csrf = agent_workspace
    assert create(client, base, csrf, name, language).status_code == 422
    assert client.get(base).json()['usage']['count'] == 1


def test_plan_capacity_is_enforced_for_create_and_import(client, agent_workspace):
    base, original, csrf = agent_workspace
    assert create(client, base, csrf, 'Second').status_code == 201
    assert create(client, base, csrf, 'Third').status_code == 201
    assert create(client, base, csrf, 'Fourth').status_code == 409
    export = client.get(base + '/' + original['id'] + '/export')
    result = client.post(base + '/import', files={'file': ('agent.json', export.content, 'application/json')}, headers=csrf)
    assert result.status_code == 409 and client.get(base).json()['usage']['count'] == 3


def test_csrf_required_for_mutations(client, agent_workspace):
    base, agent, _ = agent_workspace
    assert client.post(base, data={'name': 'Test'}).status_code == 403
    assert client.post(base + '/' + agent['id'] + '/personalization', json={'expected_revision': 1, 'persona': 'Test', 'llm_enabled': True}).status_code == 403
    assert client.post(base + '/' + agent['id'] + '/delete', json={'expected_revision': 1, 'confirmation_name': agent['name']}).status_code == 403


def test_section_saves_are_versioned_and_stale_edits_rejected(client, agent_workspace):
    base, agent, csrf = agent_workspace
    path = base + '/' + agent['id']
    response = client.post(path + '/personalization', json={'expected_revision': 1, 'persona': 'Professional banking support', 'llm_enabled': False}, headers=csrf)
    assert response.status_code == 200 and response.json()['revision'] == 2
    assert client.post(path + '/languages', json={'expected_revision': 1, 'languages': ['en','sw'], 'default_language': 'sw'}, headers=csrf).status_code == 409
    assert client.get(path).json()['languages'] == ['en']
    assert client.post(path + '/languages', json={'expected_revision': 2, 'languages': ['en','sw'], 'default_language': 'sw'}, headers=csrf).json()['revision'] == 3
    assert client.get(path).json()['persona'] == 'Professional banking support'
    versions = client.get(path + '/versions').json()['versions']
    assert [version['number'] for version in versions] == [3, 2, 1]
    assert client.get(path + '/versions/1').json()['configuration']['languages'] == ['en']
    assert len(client.get(path + '/changes').json()['changes']) == 3


def test_unchanged_save_does_not_add_versions(client, agent_workspace):
    base, agent, csrf = agent_workspace
    response = client.post(base + '/' + agent['id'] + '/personalization', json={'expected_revision': 1, 'persona': agent['persona'], 'llm_enabled': True}, headers=csrf)
    assert response.status_code == 200 and response.json()['revision'] == 1


@pytest.mark.parametrize('languages,default', [([], 'en'), (['en','en'], 'en'), (['en'], 'sw'), (['bad'], 'bad'), (['en','sw','fr','pt'], 'en')])
def test_language_validation_and_plan_limit(client, agent_workspace, languages, default):
    base, agent, csrf = agent_workspace
    assert client.post(base + '/' + agent['id'] + '/languages', json={'expected_revision': 1, 'languages': languages, 'default_language': default}, headers=csrf).status_code == 422
    assert client.get(base + '/' + agent['id']).json()['revision'] == 1


@pytest.mark.parametrize('format', ['PNG','JPEG','GIF'])
def test_avatar_formats_reencoded_and_removed(client, agent_workspace, format):
    base, _, csrf = agent_workspace
    response = create(client, base, csrf, avatar=image_bytes(format))
    assert response.status_code == 201
    agent = response.json()
    avatar = client.get(agent['avatar_url'])
    assert avatar.headers['content-type'] == 'image/png'
    assert Image.open(io.BytesIO(avatar.content)).size == (256,256)
    path = base + '/' + agent['id']
    result = client.post(path + '/identity', data={'name': 'Updated name', 'expected_revision': 1, 'remove_avatar': 'true'}, headers=csrf)
    assert result.status_code == 200 and result.json()['avatar_url'] is None
    assert client.get(path + '/avatar').status_code == 404


def test_invalid_and_oversized_avatar_do_not_create_agents(client, agent_workspace):
    base, _, csrf = agent_workspace
    assert create(client, base, csrf, avatar=b'<svg></svg>').status_code == 422
    assert create(client, base, csrf, avatar=b'0'*(10*1024*1024+1)).status_code == 422
    assert client.get(base).json()['usage']['count'] == 1


def test_export_import_roundtrip_and_no_private_ids(client, agent_workspace):
    base, _, csrf = agent_workspace
    agent = create(client, base, csrf, avatar=image_bytes('GIF')).json()
    path = base + '/' + agent['id']
    client.post(path + '/personalization', json={'expected_revision': 1, 'persona': 'Be clear', 'llm_enabled': False}, headers=csrf)
    export = client.get(path + '/export')
    package = export.json()
    assert package['format'] == 'petrix-agent' and package['schema_version'] == 1
    assert 'id' not in package['agent'] and 'workspace_id' not in package['agent']
    result = client.post(base + '/import', files={'file': ('agent.json', export.content, 'application/json')}, headers=csrf)
    assert result.status_code == 201
    imported = result.json()
    assert imported['id'] != agent['id'] and imported['revision'] == 1
    assert imported['persona'] == 'Be clear' and imported['llm_enabled'] is False
    assert client.get(imported['avatar_url']).status_code == 200


@pytest.mark.parametrize('contents', [b'not-json', b'{"format":"proto-agent"}', b'{"format":"petrix-agent","schema_version":999,"agent":{}}'])
def test_invalid_import_is_rejected(client, agent_workspace, contents):
    base, _, csrf = agent_workspace
    assert client.post(base + '/import', files={'file': ('agent.json', contents, 'application/json')}, headers=csrf).status_code == 422
    assert client.get(base).json()['usage']['count'] == 1


def test_import_rejects_bad_avatar_and_foreign_fields(client, agent_workspace):
    base, agent, csrf = agent_workspace
    package = client.get(base + '/' + agent['id'] + '/export').json()
    package['agent']['avatar_base64'] = 'bad base64'
    assert client.post(base + '/import', files={'file': ('agent.json', json.dumps(package), 'application/json')}, headers=csrf).status_code == 422
    package['agent']['avatar_base64'] = None
    package['agent']['workspace_id'] = str(uuid.uuid4())
    assert client.post(base + '/import', files={'file': ('agent.json', json.dumps(package), 'application/json')}, headers=csrf).status_code == 422


def test_restore_preserves_history_and_restores_avatar(client, agent_workspace):
    base, _, csrf = agent_workspace
    agent = create(client, base, csrf, avatar=image_bytes()).json(); path = base + '/' + agent['id']
    changed = client.post(path + '/identity', data={'name': 'Renamed', 'expected_revision': 1, 'remove_avatar': 'true'}, headers=csrf).json()
    assert changed['revision'] == 2
    restored = client.post(path + '/versions/1/restore', json={'expected_revision': 2}, headers=csrf)
    assert restored.status_code == 200 and restored.json()['revision'] == 3
    assert restored.json()['name'] == agent['name'] and restored.json()['avatar_url']
    assert [v['number'] for v in client.get(path + '/versions').json()['versions']] == [3,2,1]


def test_member_permissions_can_be_granted_and_revoked(client, app, agent_workspace):
    base, agent, csrf = agent_workspace
    roles = base.rsplit('/agents',1)[0] + '/roles'
    with app.state.session_factory() as db:
        membership = db.scalar(select(Membership)); member_id = membership.id; membership.role = 'member'; db.commit()
    assert client.get(base).json()['can_manage'] is False
    assert create(client, base, csrf).status_code == 403
    assert client.get(base + '/' + agent['id'] + '/export').status_code == 403
    assert client.post(roles, json={'admin':True,'member':True}, headers=csrf).status_code == 403
    with app.state.session_factory() as db:
        db.get(Membership, member_id).role = 'owner'; db.commit()
    assert client.post(roles, json={'admin':True,'member':True}, headers=csrf).status_code == 200
    with app.state.session_factory() as db:
        db.get(Membership, member_id).role = 'member'; db.commit()
    assert create(client, base, csrf).status_code == 201
    assert client.get('/api/auth/me').json()['workspace']['permissions']['manage_ai_agents'] is True


def test_owner_permission_is_immutable_and_admin_can_edit_roles(client, app, agent_workspace):
    base, _, csrf = agent_workspace; roles = base.rsplit('/agents',1)[0] + '/roles'
    assert client.post(roles, json={'owner':False,'admin':False,'member':False}, headers=csrf).status_code == 422
    with app.state.session_factory() as db:
        membership = db.scalar(select(Membership)); membership.role = 'admin'; db.commit()
    assert client.post(roles, json={'admin':False,'member':True}, headers=csrf).status_code == 200
    assert create(client, base, csrf).status_code == 403
    assert client.get(roles).status_code == 200


def test_cross_workspace_resources_are_hidden(client, app, agent_workspace):
    base, original, _ = agent_workspace
    assert client.post('/api/auth/magic-link', json={'email':'other@gmail.com'}).status_code == 202
    other = client.post('/api/auth/verify', json={'token':app.state.mailer.token}).json()
    csrf = {'X-Petrix-CSRF':client.cookies.get('petrix_csrf')}
    client.post('/api/workspaces/{}/setup'.format(other['workspace']['id']), data={'name':'Other','country_code':'KE'}, headers=csrf)
    assert client.get(base).status_code == 404
    assert client.get(base + '/' + original['id'] + '/export').status_code == 404
    otherbase = '/api/workspaces/{}/agents'.format(other['workspace']['id'])
    own = client.get(otherbase).json()['agents'][0]
    assert client.get(otherbase + '/' + original['id']).status_code == 404
    assert client.post(otherbase + '/' + own['id'] + '/network', json={'target_id':original['id']}, headers=csrf).status_code == 404


def test_network_connections_and_deletion_cascade(client, app, agent_workspace):
    base, source, csrf = agent_workspace; target = create(client, base, csrf, 'Target').json()
    path = base + '/' + source['id']
    edge = client.post(path + '/network', json={'target_id':target['id']}, headers=csrf)
    assert edge.status_code == 201
    assert client.post(path + '/network', json={'target_id':target['id']}, headers=csrf).status_code == 409
    assert client.post(path + '/network', json={'target_id':source['id']}, headers=csrf).status_code == 422
    assert len(client.get(path + '/network').json()['edges']) == 1
    target_path = base + '/' + target['id']
    assert client.post(target_path + '/network/' + edge.json()['id'] + '/delete', json={}, headers=csrf).status_code == 404
    assert client.post(target_path + '/delete', json={'expected_revision':1,'confirmation_name':target['name']}, headers=csrf).status_code == 204
    assert client.get(path + '/network').json()['edges'] == []


def test_deletion_requires_name_and_never_recreates_default(client, agent_workspace):
    base, agent, csrf = agent_workspace; path = base + '/' + agent['id']
    assert client.post(path + '/delete', json={'expected_revision':1,'confirmation_name':'wrong'}, headers=csrf).status_code == 422
    assert client.post(path + '/delete', json={'expected_revision':1,'confirmation_name':agent['name']}, headers=csrf).status_code == 204
    assert client.get(base).json()['agents'] == []
    assert client.get(base).json()['agents'] == []
    assert client.get(path + '/versions').status_code == 404


def test_voice_preferences_saved_and_validated(client, agent_workspace):
    base, agent, csrf = agent_workspace; path = base + '/' + agent['id'] + '/voice'
    assert client.post(path, json={'expected_revision':1,'voice_enabled':True,'voice_name':'English','voice_speed':1.2}, headers=csrf).json()['revision'] == 2
    assert client.post(path, json={'expected_revision':2,'voice_enabled':True,'voice_name':'English','voice_speed':9}, headers=csrf).status_code == 422


class FakeTester:
    available = True
    def __init__(self): self.calls = []
    def reply(self, agent, history, language=None):
        self.calls.append((agent.name, history))
        return 'A real adapter would return the model response here.'


def test_tester_requires_provider_and_llm_toggle(client, app, agent_workspace):
    base, agent, csrf = agent_workspace; path = base + '/' + agent['id']
    assert client.post(path + '/tester/messages', json={'content':'Hi'}, headers=csrf).status_code == 503
    app.state.llm_tester = FakeTester()
    client.post(path + '/personalization', json={'expected_revision':1,'llm_enabled':False,'persona':'Test'}, headers=csrf)
    assert client.post(path + '/tester/messages', json={'content':'Hi'}, headers=csrf).status_code == 409


def test_tester_history_is_preserved_after_agent_deletion(client, app, agent_workspace):
    base, agent, csrf = agent_workspace; path = base + '/' + agent['id']; tester = FakeTester(); app.state.llm_tester = tester
    first = client.post(path + '/tester/messages', json={'content':'Hi'}, headers=csrf)
    assert first.status_code == 200
    conversation_id = first.json()['conversation_id']
    second = client.post(path + '/tester/messages', json={'content':'Help','conversation_id':conversation_id}, headers=csrf)
    assert second.status_code == 200 and len(tester.calls[1][1]) == 3
    assert client.post(path + '/delete', json={'expected_revision':1,'confirmation_name':agent['name']}, headers=csrf).status_code == 204
    with app.state.session_factory() as db:
        conversation = db.get(AgentConversation, uuid.UUID(conversation_id))
        assert conversation.agent_id is None and conversation.agent_name == agent['name']
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 4
        assert db.scalar(select(func.count()).select_from(AgentVersion)) == 0
        assert db.scalar(select(func.count()).select_from(AgentChange)) >= 2


def test_tester_cannot_use_another_agents_conversation(client, app, agent_workspace):
    base, agent, csrf = agent_workspace; app.state.llm_tester = FakeTester()
    first = client.post(base + '/' + agent['id'] + '/tester/messages', json={'content':'Hi'}, headers=csrf).json()
    other = create(client, base, csrf).json()
    result = client.post(base + '/' + other['id'] + '/tester/messages', json={'content':'Hi','conversation_id':first['conversation_id']}, headers=csrf)
    assert result.status_code == 404


def test_llm_adapter_uses_persona_and_does_not_expose_errors(client, app, agent_workspace, monkeypatch):
    from app.llm_tester import LLMTester
    for key in ('ALL_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'http_proxy', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    base, agent, csrf = agent_workspace
    app.state.settings.llm_base_url = 'https://model.example/v1'
    app.state.settings.llm_model = 'test-model'
    captured = []
    original_post = httpx.Client.post
    fail = [False]
    def post(self, url, **kwargs):
        if str(url) != 'https://model.example/v1/chat/completions':
            return original_post(self, url, **kwargs)
        captured.append(kwargs['json'])
        if fail[0]:
            return httpx.Response(503, json={'error':'sensitive-provider-error'}, request=httpx.Request('POST',url))
        return httpx.Response(200, json={'choices':[{'message':{'content':'Hello from model'}}]}, request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    app.state.llm_tester = LLMTester(app.state.settings)
    response = client.post(base + '/' + agent['id'] + '/tester/messages', json={'content':'Hello'}, headers=csrf)
    assert response.status_code == 200 and response.json()['message']['content'] == 'Hello from model'
    assert captured[0]['messages'][0]['role'] == 'system' and agent['persona'] in captured[0]['messages'][0]['content']
    fail[0] = True
    failed = client.post(base + '/' + agent['id'] + '/tester/messages', json={'content':'Again'}, headers=csrf)
    assert failed.status_code == 503 and 'sensitive-provider-error' not in failed.text


def test_migration_backfills_an_existing_workspace_once(database_engine):
    schema = 'petrix_upgrade_' + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with database_engine.connect() as connection:
        original = connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes['connection'] = connection
            with connection.begin(): command.upgrade(config, '0001')
            workspace_id, user_id = uuid.uuid4(), uuid.uuid4()
            with connection.begin():
                connection.execute(text('INSERT INTO users (id,email,created_at) VALUES (:id,:email,now())'), {'id':user_id,'email':'existing@example.com'})
                connection.execute(text('INSERT INTO workspaces (id,name,created_at) VALUES (:id,:name,now())'), {'id':workspace_id,'name':'Existing'})
                connection.execute(text('INSERT INTO workspace_memberships (id,user_id,workspace_id,role,created_at) VALUES (:id,:user,:workspace,\'owner\',now())'), {'id':uuid.uuid4(),'user':user_id,'workspace':workspace_id})
            with connection.begin(): command.upgrade(config,'head')
            with connection.begin(): command.upgrade(config,'head')
            assert connection.execute(text('SELECT count(*) FROM ai_agents WHERE workspace_id=:id'), {'id':workspace_id}).scalar() == 1
            assert connection.execute(text('SELECT count(*) FROM agent_versions')).scalar() == 1
            assert connection.execute(text('SELECT count(*) FROM workspace_role_permissions')).scalar() == 3
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
