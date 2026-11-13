"""Agent knowledge isolation, revision-safe editing and migration preservation."""
from pathlib import Path
import json
import uuid

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select, text

from app.knowledge import SaveKnowledge
from app.models import AgentChange, AgentKnowledge, Membership, RolePermission
from test_llm_processing import StubProvider, llm
from test_workflows import workflow, event, save, message, contents


def source(name='Complaints policy', content='Refunds take 5 business days.', enabled=True):
    return {'id': str(uuid.uuid4()), 'name': name, 'content': content, 'enabled': enabled}


def write(client, path, headers, sources, revision=1):
    return client.post(path+'/knowledge', json={'expected_revision': revision, 'sources': sources}, headers=headers)


def test_loading_is_read_only_and_saves_audited_revisioned_sources(client, workflow, app):
    path, document, headers = workflow
    initial = client.get(path+'/knowledge').json()
    assert initial == {'agent_id': document['agent_id'], 'revision': 1, 'sources': [], 'updated_at': None}
    with app.state.session_factory() as db: assert db.scalar(select(func.count()).select_from(AgentKnowledge)) == 0
    item = source(); saved = write(client,path,headers,[item]); assert saved.status_code == 200, saved.text
    value = saved.json(); assert value['revision'] == 2 and value['sources'] == [item] and value['updated_at']
    assert client.get(path+'/knowledge').json() == value
    unchanged = write(client,path,headers,[item],2); assert unchanged.json()['revision'] == 2
    conflict = write(client,path,headers,[source(content='Stale overwrite')]); assert conflict.status_code == 409
    assert client.get(path+'/knowledge').json() == value
    with app.state.session_factory() as db:
        changes = db.scalars(select(AgentChange).where(AgentChange.action == 'knowledge_saved')).all()
        assert len(changes) == 1 and item['content'] not in changes[0].summary
    cleared = write(client,path,headers,[],2); assert cleared.json()['revision'] == 3 and cleared.json()['sources'] == []


@pytest.mark.parametrize('case', ['empty_title','empty_text','long_title','long_text','bad_id','bad_boolean','duplicate','too_many','bytes','extra'])
def test_knowledge_limits_and_schema_are_enforced(case):
    sources = [source()]
    if case == 'empty_title': sources[0]['name'] = ' '
    elif case == 'empty_text': sources[0]['content'] = ' '
    elif case == 'long_title': sources[0]['name'] = 'x'*101
    elif case == 'long_text': sources[0]['content'] = 'x'*20001
    elif case == 'bad_id': sources[0]['id'] = 'other'
    elif case == 'bad_boolean': sources[0]['enabled'] = 'true'
    elif case == 'duplicate': sources.append(dict(sources[0]))
    elif case == 'too_many': sources = [source() for _ in range(21)]
    elif case == 'bytes': sources = [source(content='你'*20000), source(content='x'*10000)]
    else: sources[0]['endpoint'] = 'https://example.com'
    with pytest.raises(ValidationError): SaveKnowledge.model_validate({'expected_revision': 1, 'sources': sources})


def test_knowledge_auth_csrf_permissions_and_workspace_scope(client, workflow, app):
    path, document, headers = workflow
    assert client.post(path+'/knowledge', json={'expected_revision': 1, 'sources': [source()]}).status_code == 403
    foreign = path.replace(document['agent_id'], str(uuid.uuid4()))
    assert client.get(foreign+'/knowledge').status_code == 404
    assert write(client,foreign,headers,[]).status_code == 404
    foreign_workspace = '/api/workspaces/'+str(uuid.uuid4())+'/agents/'+document['agent_id']
    assert client.get(foreign_workspace+'/knowledge').status_code == 404
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    assert client.get(path+'/knowledge').status_code == 200
    assert write(client,path,headers,[source()]).status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(RolePermission).where(RolePermission.role=='member')).manage_ai_agents = True; db.commit()
    assert write(client,path,headers,[source()]).status_code == 200
    client.cookies.clear()
    assert client.get(path+'/knowledge').status_code == 401


def test_only_enabled_current_agent_sources_are_sent_with_language_and_task_separated(client, workflow, app):
    path, document, headers = workflow; provider = StubProvider(); app.state.llm_processing_client = provider
    active = source(content='CURRENT AGENT CONTEXT'); disabled = source(content='DISABLED SECRET', enabled=False)
    assert write(client,path,headers,[active,disabled]).status_code == 200
    base = path.rsplit('/',1)[0]
    other = client.post(base, data={'name':'Other agent','default_language':'en'},headers=headers).json()
    assert write(client,base+'/'+other['id'],headers,[source(content='OTHER AGENT SECRET')]).status_code == 200
    document['nodes'][0]['children'] = [llm([message('{complaint_details}')],input='CUSTOMER TEXT',system_prompt='TASK INSTRUCTIONS',use_knowledge_info=True)]
    assert save(client,path,document,headers).status_code == 200
    assert contents(event(client,path,headers)) == ['Customer requests a refund.']
    model, messages, temperature, timeout = provider.calls[0]
    encoded = json.dumps(messages)
    assert model == 'openai/gpt-oss-20b' and temperature == 1 and timeout == 30
    assert 'CURRENT AGENT CONTEXT' in encoded and 'DISABLED SECRET' not in encoded and 'OTHER AGENT SECRET' not in encoded
    assert 'TASK INSTRUCTIONS' in messages[0]['content'] and 'Active conversation language: en' in messages[0]['content']
    assert active['id'] in messages[1]['content'] and messages[-1] == {'role':'user','content':'CUSTOMER TEXT'}
    trace = event(client,path,headers).json()['trace']
    assert any(step.get('knowledge_sources') == 1 for step in trace)
    assert write(client,path,headers,[],2).status_code == 200
    result = event(client,path,headers).json()
    assert any(step.get('error') == 'knowledge_unavailable' for step in result['trace']) and len(provider.calls) == 2


def test_knowledge_deleted_with_agent_and_separate_from_settings_exports(client, workflow, app):
    path, document, headers = workflow
    assert write(client,path,headers,[source()]).status_code == 200
    assert 'knowledge' not in client.get(path+'/export').json()
    agent = client.get(path).json()
    result = client.post(path+'/delete', json={'expected_revision':agent['revision'],'confirmation_name':agent['name']},headers=headers)
    assert result.status_code == 204
    with app.state.session_factory() as db: assert not db.scalars(select(AgentKnowledge)).all()


def test_migration_0008_preserves_prior_workflows_and_is_repeatable(database_engine):
    schema = 'petrix_knowledge_upgrade_' + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with database_engine.connect() as connection:
        original = connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes['connection'] = connection
            with connection.begin(): command.upgrade(config,'0007')
            workspace_id, agent_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {'id':workspace_id})
            connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing',true,'','[\"en\"]','en',false,'',1,1,now(),now())"), {'id':agent_id,'workspace':workspace_id})
            connection.execute(text("INSERT INTO agent_workflows (agent_id,revision,next_node_number,nodes,updated_at) VALUES (:id,9,42,'[]',now())"), {'id':agent_id}); connection.commit()
            with connection.begin(): command.upgrade(config,'head')
            with connection.begin(): command.upgrade(config,'head')
            assert connection.execute(text('SELECT revision,next_node_number,nodes FROM agent_workflows')).one() == (9,42,[])
            assert connection.execute(text('SELECT count(*) FROM agent_knowledge')).scalar() == 0
            connection.commit()
            with connection.begin(): command.downgrade(config,'0007')
            assert connection.execute(text('SELECT revision,next_node_number,nodes FROM agent_workflows')).one() == (9,42,[])
            connection.commit()
        finally:
            connection.rollback(); connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
