"""Encrypted credentials, tenancy, verification lifecycle and migration safety."""
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, text

from app.database_connections.credentials import decrypt, encrypt
from app.models import DatabaseConnection, Membership, Workspace
from test_workflows import contents, event, message, save, workflow
from test_sql_query import pg_details, sql


def test_dedicated_encryption_key_round_trip_and_wrong_key(app):
    original = app.state.settings
    settings = original.model_copy(update={'database_connections_encryption_key': original.session_signing_secret.__class__(Fernet.generate_key().decode())})
    value = encrypt(' a password with spaces ', settings)
    assert decrypt(value, settings) == ' a password with spaces '
    with pytest.raises(ValueError, match='credentials_unavailable'): decrypt(value, original)


@pytest.fixture
def connections(client, workflow):
    path, document, headers = workflow
    workspace_id = path.split('/')[3]
    return '/api/workspaces/' + workspace_id + '/database-connections', path, document, headers


def form(**values):
    return dict(name='complaints_db', type='postgresql', host='127.0.0.1', port=5432, database='postgres',
        username='petrix_reader', password='sensitive-password', ssl_mode='disable', **values)


@pytest.fixture
def verified_connection(client, connections, app):
    base, path, document, headers = connections
    app.state.database_client.verify = lambda _: None
    result = client.post(base, json=form(), headers=headers)
    assert result.status_code == 201, result.text
    return result.json()


def test_password_is_encrypted_not_returned_and_names_are_unique(client, connections, verified_connection, app):
    base, _, _, headers = connections
    value = verified_connection
    assert value['status'] == 'verified' and value['verified_at'] and value['revision'] == 1
    assert 'password' not in str(value) and 'sensitive-password' not in str(value)
    result = client.get(base)
    assert result.status_code == 200 and result.json()['connections'][0]['id'] == value['id']
    assert 'sensitive-password' not in result.text and 'password_encrypted' not in result.text
    with app.state.session_factory() as db:
        row = db.get(DatabaseConnection, uuid.UUID(value['id']))
        assert row.password_encrypted != 'sensitive-password'
        assert decrypt(row.password_encrypted, app.state.settings) == 'sensitive-password'
    assert client.post(base, json=form(), headers=headers).status_code == 409
    duplicate = form(); duplicate['name'] = 'COMPLAINTS_DB'
    assert client.post(base, json=duplicate, headers=headers).status_code == 409


def test_failed_connection_is_stored_excluded_until_verified(client, connections, app):
    base, _, _, headers = connections
    app.state.database_client.verify = lambda _: 'connection_failed'
    value = client.post(base, json=form(), headers=headers).json()
    assert value['status'] == 'error' and value['verified_at'] is None
    assert client.get(base + '/options').json()['connections'] == []
    app.state.database_client.verify = lambda _: None
    checked = client.post(base + '/' + value['id'] + '/verify', json={'expected_revision': 1}, headers=headers).json()
    assert checked['status'] == 'verified' and checked['revision'] == 2
    assert client.get(base + '/options').json()['connections'][0]['id'] == value['id']


def test_edit_keeps_password_and_failed_reverification_disables_connector(client, connections, verified_connection, app):
    base, _, _, headers = connections
    seen = []
    app.state.database_client.verify = lambda details: seen.append(details) or 'connection_failed'
    values = form(); values.update(name='Changed', password=None, expected_revision=1)
    result = client.post(base + '/' + verified_connection['id'], json=values, headers=headers)
    assert result.status_code == 200, result.text
    assert seen[-1]['password'] == 'sensitive-password'
    assert result.json()['status'] == 'error' and result.json()['revision'] == 2
    assert not client.get(base + '/options').json()['connections']
    assert client.post(base + '/' + verified_connection['id'], json=values, headers=headers).status_code == 409
    assert client.post(base + '/' + verified_connection['id'] + '/delete', json={'expected_revision': 1}, headers=headers).status_code == 409


def test_mutations_require_csrf_and_options_require_agent_permission(client, connections, verified_connection, app):
    base, _, _, headers = connections
    identifier = verified_connection['id']
    assert client.post(base, json=form()).status_code == 403
    for suffix in ['', '/verify', '/delete']:
        data = dict(form(), expected_revision=1) if not suffix else {'expected_revision': 1}
        assert client.post(base + '/' + identifier + suffix, json=data).status_code == 403
    with app.state.session_factory() as db:
        member = db.scalar(select(Membership)); member.role = 'member'; db.commit()
    assert client.get(base).status_code == 403
    assert client.post(base, json=form(), headers=headers).status_code == 403
    assert client.get(base + '/options').status_code == 403


def test_workspace_scope_and_members_with_agent_permission_see_only_safe_options(client, connections, verified_connection, app):
    from app.models import RolePermission
    base, _, _, headers = connections
    with app.state.session_factory() as db:
        member = db.scalar(select(Membership)); member.role = 'member'
        permission = db.scalar(select(RolePermission).where(RolePermission.role == 'member'))
        permission.manage_ai_agents = True; db.commit()
    assert client.get(base).status_code == 403
    value = client.get(base + '/options')
    assert value.status_code == 200 and value.json()['connections'] == [{key: verified_connection[key] for key in ('id', 'name', 'type')}]
    other = '/api/workspaces/' + str(uuid.uuid4()) + '/database-connections'
    assert client.get(other).status_code == 404
    assert client.get(other + '/options').status_code == 404


def test_referenced_connection_cannot_be_deleted_and_workflows_require_verified_same_workspace(client, connections, verified_connection, app):
    base, path, document, headers = connections
    action = sql([message('Status: {query_results.rows.0.status}')], connection_id=verified_connection['id'], query='SELECT 1 AS status')
    document['nodes'][0]['children'] = [action]
    result = save(client, path, document, headers)
    assert result.status_code == 200, result.text
    assert client.post(base + '/' + verified_connection['id'] + '/delete', json={'expected_revision': 1}, headers=headers).status_code == 409
    action['config']['connection_id'] = str(uuid.uuid4())
    document['revision'] = result.json()['revision']
    assert save(client, path, document, headers).status_code == 422
    action['config']['connection_id'] = verified_connection['id']
    with app.state.session_factory() as db:
        row = db.get(DatabaseConnection, uuid.UUID(verified_connection['id'])); row.status = 'error'; db.commit()
    assert save(client, path, document, headers).status_code == 422


def test_real_connector_to_sql_query_to_message(client, connections, pg_details, app):
    base, path, document, headers = connections
    values = form(); values.update(pg_details)
    result = client.post(base, json=values, headers=headers)
    assert result.status_code == 201 and result.json()['status'] == 'verified', result.text
    action = sql([message('Your balance is {query_results.rows.0.balance}')], connection_id=result.json()['id'], query='SELECT 1002 AS balance')
    document['nodes'][0]['children'] = [action]
    assert save(client, path, document, headers).status_code == 200
    reply = event(client, path, headers)
    assert contents(reply) == ['Your balance is 1002']
    assert reply.json()['trace'][1]['outcome'] == 'success'
    assert reply.json()['variables']['query_results']['row_count'] == 1


def test_runtime_rechecks_verification_and_routes_error_without_querying(client, connections, verified_connection, app):
    base, path, document, headers = connections
    action = sql(error=[message('Connection unavailable')], connection_id=verified_connection['id'], query='SELECT 1')
    document['nodes'][0]['children'] = [action]
    assert save(client, path, document, headers).status_code == 200
    with app.state.session_factory() as db:
        db.get(DatabaseConnection, uuid.UUID(verified_connection['id'])).status = 'error'; db.commit()
    app.state.database_client.query = lambda *_: pytest.fail('Unverified connections must not execute')
    result = event(client, path, headers)
    assert contents(result) == ['Connection unavailable']
    assert result.json()['variables']['query_results']['error'] == 'connection_unavailable'


def test_unreferenced_connection_can_be_deleted(client, connections, verified_connection):
    base, _, _, headers = connections
    assert client.post(base + '/' + verified_connection['id'] + '/delete', json={'expected_revision': 1}, headers=headers).status_code == 200
    assert client.get(base).json()['connections'] == []


def test_0006_preserves_existing_workflow_data(database_engine):
    schema = 'petrix_sql_upgrade_' + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with database_engine.connect() as connection:
        original = connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "' + schema + '"')); connection.commit()
        try:
            connection.execute(text('SET search_path TO "' + schema + '"')); connection.commit()
            config.attributes['connection'] = connection
            with connection.begin(): command.upgrade(config, '0005')
            workspace_id, agent_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {'id':workspace_id})
            connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing',false,'','[\"en\"]','en',false,'',1,1,now(),now())"), {'id':agent_id,'workspace':workspace_id})
            connection.execute(text("INSERT INTO agent_workflows (agent_id,revision,next_node_number,nodes,updated_at) VALUES (:id,8,57,'[]',now())"), {'id':agent_id}); connection.commit()
            with connection.begin(): command.upgrade(config, 'head')
            assert connection.execute(text('SELECT revision,next_node_number,nodes FROM agent_workflows WHERE agent_id=:id'), {'id':agent_id}).one() == (8,57,[])
            assert connection.execute(text('SELECT count(*) FROM database_connections')).scalar() == 0
            connection.commit()
        finally:
            connection.rollback(); connection.execute(text('SET search_path TO "' + original + '"'))
            connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE')); connection.commit()
