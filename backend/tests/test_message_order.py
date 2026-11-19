"""Message chronology must not depend on wall-clock precision or random UUIDs."""
from datetime import timedelta
import uuid
import pytest
from sqlalchemy import event

from app.core.security import utcnow
from app.models import AgentMessage
from test_agents import agent_workspace
from test_webchat import webchat, visitor, start, save, send
from test_workflows import node, message


def ordered_workflow(client, f):
    document = client.get(f['path'] + '/workflows').json()
    nodes = document['nodes']
    next(n for n in nodes if n['type'] == 'chat_started')['children'] = [message('Welcome')]
    nodes.append(node('trigger', 'message_received', config={'examples': {'en': ['hi', 'again']}, 'match_mode': 'exact', 'threshold': 1},
                      children=[message('First reply'), message('Second reply')]))
    response = client.post(f['path'] + '/workflows', json={'expected_revision': document['revision'], 'nodes': nodes}, headers=f['h'])
    assert response.status_code == 200, response.text
    save(client, f)


@pytest.mark.parametrize('preview', [False, True])
def test_webchat_keeps_user_and_multiple_replies_in_order_when_timestamps_tie(client, webchat, preview):
    f = webchat
    ordered_workflow(client, f)
    if preview:
        from urllib.parse import parse_qs, urlsplit
        result = client.post(f['root']+'/'+f['channel']['id']+'/preview', json={'config': f['channel']['config']}, headers=f['h'])
        assert result.status_code == 201, result.text
        token = parse_qs(urlsplit(result.json()['url']).query)['preview'][0]
        headers = visitor(client, f, 'http://localhost:8000', token)
    else:
        headers = visitor(client, f)
    frozen = utcnow() + timedelta(seconds=1)
    identifiers = iter([uuid.UUID(int=n) for n in [0, 2**128-1, 2, 1, 2**128-2, 4, 3]])
    def same_tick(_mapper, _connection, message):
        message.created_at = frozen
        message.id = next(identifiers)
    event.listen(AgentMessage, 'before_insert', same_tick)
    try:
        chat = start(client, f, headers)
        expected = ['Welcome']
        for content in ['hi', 'again']:
            result = send(client, f, headers, chat, content)
            assert result.status_code == 200, result.text
            expected += [content, 'First reply', 'Second reply']
            assert [m['content'] for m in result.json()['messages']] == expected
            assert [int(m['sequence']) for m in result.json()['messages']] == list(range(1, len(expected)+1))
            reopened = client.get(f['public'] + '/conversations/' + chat['conversation_id'], headers=headers)
            assert [m['content'] for m in reopened.json()['messages']] == expected
            polled = client.post(f['public'] + '/conversations/' + chat['conversation_id'] + '/poll', json={}, headers=headers)
            assert [m['content'] for m in polled.json()['messages']] == expected
    finally:
        event.remove(AgentMessage, 'before_insert', same_tick)


def test_public_pagination_keeps_order_when_clock_goes_backwards(client, webchat):
    f = webchat
    headers = visitor(client, f)
    chat = start(client, f, headers)
    older = utcnow() - timedelta(days=1)
    expected = [m['content'] for m in chat['messages']]
    with f['app'].state.session_factory() as db:
        for i in range(105):
            db.add(AgentMessage(conversation_id=uuid.UUID(chat['conversation_id']), role='internal', content='PRIVATE', created_at=older))
            db.add(AgentMessage(conversation_id=uuid.UUID(chat['conversation_id']), role='user', content='Turn '+str(i), created_at=older))
            expected.append('Turn '+str(i))
        db.commit()
    path = f['public']+'/conversations/'+chat['conversation_id']
    latest = client.get(path, headers=headers).json()
    assert len(latest['messages']) == 100 and latest['has_more']
    first = client.get(path+'?before='+latest['messages'][0]['id'], headers=headers).json()
    assert not first['has_more']
    combined = first['messages']+latest['messages']
    assert [m['content'] for m in combined] == expected
    assert [int(m['sequence']) for m in combined] == sorted(int(m['sequence']) for m in combined)
    assert len({m['id'] for m in combined}) == len(combined)


def test_tester_reopened_history_uses_the_same_stable_order(client, webchat):
    f = webchat
    ordered_workflow(client, f)
    frozen = utcnow()
    def same_tick(_mapper, _connection, message):
        message.created_at = frozen
        message.id = uuid.UUID(int=2**128-1) if message.role == 'user' else uuid.uuid4()
    event.listen(AgentMessage, 'before_insert', same_tick)
    try:
        begun = client.post(f['path']+'/tester/events', json={'event':'chat_started'}, headers=f['h'])
        assert begun.status_code == 200, begun.text
        chat = begun.json()['conversation_id']
        sent = client.post(f['path']+'/tester/events', json={'event':'message_received','content':'hi','conversation_id':chat}, headers=f['h'])
        assert sent.status_code == 200, sent.text
        assert [m['content'] for m in sent.json()['messages']] == ['First reply','Second reply']
        reopened = client.get(f['path']+'/tester/conversations/'+chat)
        assert [m['content'] for m in reopened.json()['messages']] == ['Welcome','hi','First reply','Second reply']
    finally:
        event.remove(AgentMessage, 'before_insert', same_tick)
