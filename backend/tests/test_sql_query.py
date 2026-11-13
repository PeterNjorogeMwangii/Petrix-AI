"""SELECT validation, parameter binding, read-only execution and workflow paths."""
import os
import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from pydantic import ValidationError
import psycopg
import pytest
from sqlalchemy import text

from app.database_connections.client import DatabaseClient, QueryResult, cell
from app.database_connections.queries import prepare_query
from app.workflows.actions.sql_query.config import SQLQueryConfig
from app.workflows.actions.sql_query.handler import execute as lookup
from app.workflows.context import ActionContext
from app.workflows.runtime import execute
from test_branch import branch, variable
from test_jump import jump, prepare, setup_tree, texts
from test_workflows import message, node


def sql(success=None, error=None, timeout=None, **settings):
    config = dict(connection_id=str(uuid.uuid4()), query='SELECT status FROM complaints WHERE ref = {complaint_ref}',
        query_mode='template', timeout_seconds=30, variable_name='query_results',
        **{key + '_path_id': str(uuid.uuid4()) for key in ('success', 'error', 'timeout')})
    config.update(settings)
    result = node('action', 'sql_query', name='Database lookup', config=config)
    result['paths'] = [dict(id=config[key + '_path_id'], children=children or []) for key, children in
        [('success', success), ('error', error), ('timeout', timeout)]]
    return result


class StubSQL:
    def __init__(self, result=None):
        self.result = result or QueryResult('success', [{'status': 'resolved'}], ['status'])
        self.calls = []

    def query(self, *args):
        self.calls.append(args)
        return self.result


@pytest.mark.parametrize('query', [
    'DELETE FROM complaints', 'UPDATE complaints SET status=1', 'INSERT INTO complaints VALUES (1)',
    'SELECT 1; DELETE FROM complaints', 'SELECT 1; SELECT 2', 'SELECT 1 INTO new_table',
    'WITH changed AS (DELETE FROM complaints RETURNING *) SELECT * FROM changed',
    'SELECT * FROM complaints FOR UPDATE', 'SELECT * FROM complaints FOR SHARE',
    "SELECT set_config('transaction_read_only', 'off', true)",
    "SELECT pg_catalog.\"set_config\"('statement_timeout', '0', true)",
    "SELECT U&\"set\\005fconfig\"('statement_timeout', '0', true)",
    "SELECT nextval('sequence')", "SELECT pg_notify('x','y')", "SELECT lo_export(1,'/tmp/file')",
    "SELECT dblink_exec('db', 'DELETE FROM x')", "SELECT pg_advisory_lock(1)",
    "SELECT 'unclosed", 'SELECT /* unclosed', 'SELECT $$ unclosed', 'SELECT "unclosed',
    "SELECT 'prefix-{complaint_ref}'", 'SELECT $tag${complaint_ref}$tag$', 'SELECT {broken path}',
])
def test_only_single_read_only_selects_are_accepted(query):
    with pytest.raises(ValueError): prepare_query(query, 'template')


@pytest.mark.parametrize('query', [
    'SELECT 1', ' SELECT 1; -- trailing comment', "SELECT '; DELETE' AS status", 'SELECT $$; DELETE$$ AS status',
    'SELECT $tag$semi;colon$tag$ AS status', '/* outer /* nested */ comment */ SELECT 1',
    'SELECT (SELECT count(*) FROM complaints) AS total', 'SELECT 1 UNION SELECT 2',
    'SELECT "status" FROM "complaints"', "SELECT '100%' AS percentage", 'SELECT 10 % 3 AS remainder',
])
def test_legitimate_selects_comments_literals_and_subqueries_are_supported(query):
    assert prepare_query(query, 'template')[0]


@pytest.mark.parametrize('template', [
    'SELECT status FROM complaints WHERE ref = {complaint_ref}',
    'SELECT status FROM complaints WHERE ref = {{complaint_ref}}',
    "SELECT status FROM complaints WHERE ref = '{{complaint_ref}}'",
    'SELECT status FROM complaints WHERE ref = {complaint_ref.text}',
])
def test_user_values_are_bound_never_inserted_into_the_sql(template):
    agent, _ = setup_tree()
    attack = "x' OR 1=1; DELETE FROM complaints; -- {agent_name}"
    context = ActionContext(agent, 'en', {}, {'variables': {'complaint_ref': attack}})
    query, values = prepare_query(template, 'template', context)
    assert query == 'SELECT status FROM complaints WHERE ref = %s' and values == [attack]
    assert attack not in query


def test_python_query_templates_and_numeric_survey_parameters():
    agent, _ = setup_tree()
    context = ActionContext(agent, 'en', {}, {'variables': {'score': 9, 'data': {'status': 'open'}}})
    query, values = prepare_query('"SELECT {score.norm.value} AS score, {data.status} AS status"', 'expression', context)
    assert query == 'SELECT %s AS score, %s AS status' and values == [9, 'open']
    for source in ['"SELECT " + str(score)', '__import__("os")', '42']:
        with pytest.raises(ValueError): prepare_query(source, 'expression', context)


@pytest.mark.parametrize('settings', [
    {'connection_id': 'missing'}, {'query': 'DROP TABLE users'}, {'query': 'SELECT 1;SELECT 2'},
    {'variable_name': 'language'}, {'variable_name': 'system'}, {'variable_name': '9result'}, {'variable_name': 'a.b'},
    {'timeout_seconds': 0}, {'timeout_seconds': 121}, {'timeout_seconds': True}, {'timeout_seconds': 1.5},
    {'query_mode': 'javascript'}, {'query_mode': 'expression', 'query': '"SELECT " + user_input'},
    {'extra': True},
])
def test_invalid_sql_configuration_is_rejected(settings):
    with pytest.raises(ValidationError): SQLQueryConfig.model_validate(sql(**settings)['config'])


@pytest.mark.parametrize('result,expected', [
    (QueryResult('success', [{'status': 'resolved'}], ['status']), 'Success'),
    (QueryResult('success', [], ['status']), 'Success'),
    (QueryResult('error', error='query_failed'), 'Error'), (QueryResult('timeout', error='timeout'), 'Timeout'),
])
def test_one_outcome_runs_then_shared_actions_continue(result, expected):
    agent, document = setup_tree()
    action = sql([message('Success')], [message('Error')], [message('Timeout')])
    document.nodes[0]['children'] = [variable('complaint_ref', '"REF-123"'), action, message('After SQL')]
    prepare(agent, document)
    client = StubSQL(result)
    output = execute(document, agent, {}, {'event': 'chat_started'}, sql_client=client)
    assert texts(output) == [expected, 'After SQL'] and len(client.calls) == 1
    assert output[0]['variables']['query_results'] == result.variable()
    assert output[2][2]['outcome'] == result.outcome


def test_query_result_drives_branch_message_and_jump():
    agent, document = setup_tree()
    menu = message('Menu')
    choice = branch(('query_results.row_count > 0 and query_results.rows[0].status == "resolved"',
        [message('Status: {query_results.rows.0.status}'), jump(menu)]), otherwise=[message('Not found')])
    document.nodes[0]['children'] = [variable('complaint_ref', '"REF-123"'), sql([choice]), message('Abandoned')]
    document.nodes[-1]['children'] = [menu]
    document.nodes[-1]['enabled'] = True
    prepare(agent, document)
    output = execute(document, agent, {}, {'event': 'chat_started'}, sql_client=StubSQL())
    assert texts(output) == ['Status: resolved', 'Menu']


def test_missing_parameters_and_query_limit_do_not_access_database():
    agent, _ = setup_tree()
    client = StubSQL()
    context = ActionContext(agent, 'en', {}, {}, sql_client=client)
    config = SQLQueryConfig.model_validate(sql()['config']).model_dump(mode='json')
    assert lookup(config, context).error == 'invalid_query' and client.calls == []
    context.assign('complaint_ref', '123'); context.sql_calls = 10
    assert lookup(config, context).error == 'query_limit' and client.calls == []


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'children', 'collision'])
def test_outcome_path_integrity(mutation):
    agent, document = setup_tree()
    action = sql([message('Good')])
    document.nodes[0]['children'] = [action]
    if mutation == 'missing': action['paths'].pop()
    elif mutation == 'extra': action['paths'].append({'id': str(uuid.uuid4()), 'children': []})
    elif mutation == 'duplicate': action['paths'][1] = deepcopy(action['paths'][0])
    elif mutation == 'children': action['children'] = [message('Wrong')]
    else: action['paths'][0]['children'][0]['id'] = action['paths'][0]['id']
    with pytest.raises(HTTPException): prepare(agent, document)


@pytest.fixture
def pg_details(database_engine):
    from sqlalchemy.engine import make_url
    url = make_url(os.environ['SQL_QUERY_TEST_DATABASE_URL']) if os.environ.get('SQL_QUERY_TEST_DATABASE_URL') else database_engine.url
    return dict(host=url.host, port=url.port, database=url.database, username=url.username,
        password=url.password or 'test-only', ssl_mode='disable')


def test_real_postgres_query_is_read_only_parameterized_and_preserves_types(pg_details):
    client = DatabaseClient()
    with client.connect(pg_details, 5) as connection:
        connection.read_only = True
        with connection.transaction(force_rollback=True):
            assert connection.execute("SELECT current_setting('transaction_read_only'), 1").fetchone() == ('on', 1)
    assert client.verify(pg_details) is None
    result = client.query(pg_details, "SELECT %s::text AS reference, 12.34::numeric AS amount, DATE '2026-10-19' AS day, current_setting('transaction_read_only') AS mode", ["O'Brien"], 5)
    assert result.outcome == 'success', result
    assert result.rows == [{'reference': "O'Brien", 'amount': '12.34', 'day': '2026-10-19', 'mode': 'on'}]
    assert result.columns == ['reference', 'amount', 'day', 'mode']


def test_server_read_only_transaction_blocks_function_writes(pg_details):
    name = 'petrix_guard_' + uuid.uuid4().hex
    def connect_admin():
        return psycopg.connect(**dict(host=pg_details['host'], port=pg_details['port'], dbname=pg_details['database'],
            user=pg_details['username'], password=pg_details['password'], sslmode='disable'), autocommit=True)
    with connect_admin() as admin:
        admin.execute('CREATE TABLE ' + name + ' (id integer)')
        admin.execute('CREATE FUNCTION ' + name + "() RETURNS integer LANGUAGE plpgsql AS $$ BEGIN INSERT INTO " + name + ' VALUES (1); RETURN 1; END $$')
    try:
        # A seemingly benign custom SELECT function still cannot write.
        result = DatabaseClient().query(pg_details, 'SELECT ' + name + '()', [], 5)
        assert result.outcome == 'error' and result.error == 'query_failed'
        with connect_admin() as admin:
            assert admin.execute('SELECT count(*) FROM ' + name).fetchone()[0] == 0
    finally:
        with connect_admin() as admin:
            admin.execute('DROP FUNCTION ' + name + '()')
            admin.execute('DROP TABLE ' + name)


@pytest.mark.parametrize('query,expected,error', [
    ('SELECT 1 AS a WHERE false', 'success', None),
    ('SELECT 1 AS a, 2 AS a', 'error', 'invalid_columns'),
    ("SELECT repeat('x', 20000) AS large", 'error', 'result_too_large'),
    ('SELECT nonexistent FROM nonexistent_table', 'error', 'query_failed'),
])
def test_real_postgres_empty_duplicate_large_and_error_results(pg_details, query, expected, error):
    result = DatabaseClient().query(pg_details, query, [], 5)
    assert result.outcome == expected and result.error == error


def test_real_postgres_row_limit_reports_truncation(pg_details):
    result = DatabaseClient().query(pg_details, 'SELECT generate_series(1, 75) AS number', [], 5)
    assert result.outcome == 'success' and len(result.rows) == 50 and result.truncated
    assert result.rows[-1]['number'] == 50


def test_real_postgres_query_timeout_uses_timeout_outcome(pg_details):
    result = DatabaseClient().query(pg_details, 'SELECT pg_sleep(1.2) AS waited', [], 1)
    assert result.outcome == 'timeout' and result.error == 'timeout'


def test_database_errors_are_redacted_and_timeout_has_own_outcome(monkeypatch):
    client = DatabaseClient()
    def fail(*_): raise psycopg.OperationalError('password=do-not-return-this')
    monkeypatch.setattr(client, 'connect', fail)
    assert client.verify({}) == 'connection_failed'
    assert client.query({}, 'SELECT 1', [], 1).variable()['error'] == 'query_failed'
    def timeout(*_): raise psycopg.errors.QueryCanceled('sensitive SQL')
    monkeypatch.setattr(client, 'connect', timeout)
    assert client.query({}, 'SELECT 1', [], 1).outcome == 'timeout'
