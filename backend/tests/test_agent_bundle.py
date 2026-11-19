"""The delivered fixture imports and runs; invalid bundles cannot leave partial agents."""
import base64
import io
import json
import uuid
from pathlib import Path

import pytest
from pypdf import PdfReader
from sqlalchemy import func, select

from app.models import AgentKnowledgeSource, AgentChange
from app.workflows.defaults import SYSTEM_TRIGGERS, node_id
from app.workflows.numbering import walk
from app.workflows.actions.llm_processing.client import ProcessingResult
from test_agents import agent_workspace
from test_workflows import event, contents, message, node
from test_branch import branch

SAMPLE = Path(__file__).resolve().parents[2] / 'examples/workflow-pdf-test-bot.petrix.json'
DEFINITION = 'KCB M-PESA is a loans and savings product exclusively offered by KCB Bank Kenya Ltd to Safaricom M-PESA customers.'
CODE = 'KB-KCB-7214'


def package():
    return json.loads(SAMPLE.read_text(encoding='utf-8'))


def upload(client, base, headers, value=None):
    return client.post(base + '/import', files={'file': ('demo.json', json.dumps(value or package()).encode(), 'application/json')}, headers=headers)


class GroundingProbe:
    available = True
    def __init__(self): self.calls = []
    def process(self, model, messages, temperature, timeout):
        self.calls.append(messages)
        reference = json.loads(messages[1]['content'].split('\n', 1)[1])
        assert CODE in reference[0]['text']
        assert DEFINITION in ' '.join(reference[0]['text'].split())
        question = messages[-1]['content']
        text = CODE if 'verification' in question else DEFINITION if question == 'What is KCB M-PESA?' else 'The test PDF does not contain that information.'
        return ProcessingResult('success', text=text)


def test_delivered_bot_imports_pdf_and_routes_workflows_and_unmatched_questions(client, agent_workspace, app):
    base, original, headers = agent_workspace
    response = upload(client, base, headers)
    assert response.status_code == 201, response.text
    agent = response.json(); path = base + '/' + agent['id']
    document = client.get(path + '/workflows').json()
    assert len(document['nodes']) == 7
    assert all(root['id'] == node_id(agent['id'], root['type']) for root in document['nodes'] if root['type'] in SYSTEM_TRIGGERS)
    records = client.get(path + '/knowledge/sources').json()['items']
    assert len(records) == 1 and records[0]['status'] == 'ready'
    detail = client.get(path + '/knowledge/sources/' + records[0]['id']).json()
    assert CODE in detail['content'] and detail['chunk_count'] > 0
    download = client.get(path + '/knowledge/sources/' + records[0]['id'] + '/download')
    assert download.content == base64.b64decode(package()['knowledge_files'][0]['data_base64'])
    assert DEFINITION in ' '.join(PdfReader(io.BytesIO(download.content)).pages[0].extract_text().split())
    assert CODE not in json.dumps(package()['workflows']) and CODE not in agent['persona']
    provider = GroundingProbe(); app.state.llm_processing_client = provider
    started = event(client, path, headers)
    conversation = started.json()['conversation_id']
    assert 'test bot' in contents(started)[0]
    contact = event(client, path, headers, 'message_received', conversation, content='Contact support')
    assert 'support@example.invalid' in contents(contact)[0] and contact.json()['matching']['method'] == 'exact'
    assert not provider.calls
    complaint = event(client, path, headers, 'message_received', conversation, content='Log demo complaint').json()
    assert complaint['survey']['variable_name'] == 'demo_name'
    name = event(client, path, headers, 'message_received', conversation, content='Demo Tester', survey_answer_token=complaint['survey']['answer_token']).json()
    issue = event(client, path, headers, 'message_received', conversation, content='Demo issue', survey_answer_token=name['survey']['answer_token'])
    assert 'Demo Tester' in contents(issue)[0] and 'DEMO-1001' in contents(issue)[0] and not provider.calls
    for question, expected in [('What is KCB M-PESA?', DEFINITION), ('What is the document verification code?', CODE), ('What is the loan interest rate?', 'The test PDF does not contain that information.')]:
        answer = event(client, path, headers, 'message_received', conversation, content=question)
        assert contents(answer) == [expected]
        assert answer.json()['matching']['method'] == 'fallback'
        step = next(step for step in answer.json()['trace'] if step['type'] == 'llm_processing')
        assert step['knowledge_sources'] == 1 and step['outcome'] == 'success'
    assert len(provider.calls) == 3
    assert client.get(base).json()['usage']['agent_limit'] is None
    assert client.get(base + '/' + original['id'] + '/workflows').json()['revision'] == 1


def test_repeat_imports_have_independent_agent_node_path_and_source_ids(client, agent_workspace):
    base, _, headers = agent_workspace
    paths = []; sources = []; identities = []
    for _ in range(2):
        imported = upload(client, base, headers)
        assert imported.status_code == 201, imported.text
        path = base + '/' + imported.json()['id']; paths.append(path)
        nodes = client.get(path + '/workflows').json()['nodes']
        identities.append({item['id'] for item in walk(nodes)} | {p['id'] for item in walk(nodes) for p in item.get('paths', [])})
        sources.append(client.get(path + '/knowledge/sources').json()['items'][0]['id'])
    assert paths[0] != paths[1] and identities[0].isdisjoint(identities[1]) and sources[0] != sources[1]


@pytest.mark.parametrize('mutation', ['bad_base64', 'bad_pdf', 'scanned_pdf', 'filename', 'missing_system', 'duplicate_node', 'foreign_jump', 'bad_jump', 'workspace_action', 'duplicate_path', 'unknown_field', 'invalid_language'])
def test_invalid_bundle_rolls_back_every_new_record(client, agent_workspace, app, mutation):
    from test_agent_knowledge import pdf
    base, _, headers = agent_workspace; value = package()
    if mutation == 'bad_base64': value['knowledge_files'][0]['data_base64'] = 'invalid~'
    elif mutation == 'bad_pdf': value['knowledge_files'][0]['data_base64'] = base64.b64encode(b'%PDF-invalid').decode()
    elif mutation == 'scanned_pdf': value['knowledge_files'][0]['data_base64'] = base64.b64encode(pdf('')).decode()
    elif mutation == 'filename': value['knowledge_files'][0]['filename'] = '..\\secret.pdf'
    elif mutation == 'missing_system': value['workflows']['nodes'].pop(0)
    elif mutation == 'duplicate_node': value['workflows']['nodes'].append(value['workflows']['nodes'][4])
    elif mutation in {'foreign_jump', 'bad_jump'}:
        value['workflows']['nodes'][0]['children'] = [node('action', 'jump', config={'target_node_id': str(uuid.uuid4()) if mutation == 'foreign_jump' else []})]
    elif mutation == 'workspace_action': value['workflows']['nodes'][0]['children'] = [node('action', 'sql_query', config={})]
    elif mutation == 'duplicate_path': value['workflows']['nodes'][-1]['children'][0]['paths'][1] = value['workflows']['nodes'][-1]['children'][0]['paths'][0]
    elif mutation == 'unknown_field': value['unexpected'] = True
    else: value['agent']['languages'] = ['unsupported']
    with app.state.session_factory() as db:
        before = db.scalar(select(func.count()).select_from(AgentChange))
    response = upload(client, base, headers, value)
    assert response.status_code == 422, response.text
    assert client.get(base).json()['usage']['count'] == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentKnowledgeSource)) == 0
        assert db.scalar(select(func.count()).select_from(AgentChange)) == before


def test_import_rebinds_nested_branch_jump_and_system_targets(client, agent_workspace):
    base, _, headers = agent_workspace; value = package(); roots = value['workflows']['nodes']
    target = message('Rebound branch and Jump work.')
    jump = node('action', 'jump', config={'target_node_id': target['id']})
    choice = branch(('True', [jump]), otherwise=[message('Wrong path')])
    roots[0]['children'] = [choice]
    roots[4]['children'] = [target]
    result = upload(client, base, headers, value)
    assert result.status_code == 201, result.text
    assert contents(event(client, base + '/' + result.json()['id'], headers)) == ['Rebound branch and Jump work.']


def test_import_authorization_precedes_pdf_parsing(client, agent_workspace, app, monkeypatch):
    import app.agent_bundle as bundle
    base, _, headers = agent_workspace
    def must_not_parse(*args): raise AssertionError('Unauthorized PDF was parsed')
    monkeypatch.setattr(bundle, 'prepare_files', must_not_parse)
    assert upload(client, base, {}).status_code == 403
    client.cookies.clear()
    assert upload(client, base, headers).status_code == 401
