"""Workspace-owned teams, name collisions, permissions and an additive upgrade."""
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
import pytest
from sqlalchemy import select, text
from app.models import AgentChange, Membership, RolePermission, SupportTeam, Workspace
from app.teams.matching import match_team
from app.teams.schemas import TeamCreate
from test_workflows import workflow


def team_body(name='Bank of Tanzania', alternatives=None):
    return {'name': name, 'description': 'Central bank support.', 'alternative_names': alternatives if alternatives is not None else ['BOT', 'Benki Kuu ya Tanzania']}


def create_team(client, path, headers, body=None):
    base = path.rsplit('/agents/', 1)[0] + '/teams'
    response = client.post(base, json=body or team_body(), headers=headers)
    assert response.status_code == 201, response.text
    return base, response.json()


@pytest.mark.parametrize('body', [team_body(' '), team_body('!!!'), team_body('x'*101),
    team_body(alternatives=[' ']), team_body(alternatives=['!!!']), team_body(alternatives=['x'*101]),
    team_body(alternatives=['BOT', 'b.o.t', 'bot']), team_body(alternatives=['Bank-of-Tanzania']),
    team_body(alternatives=['alias'+str(i) for i in range(51)]), {**team_body(), 'private': True},
    {**team_body(), 'alternative_names': 'BOT'}, team_body(alternatives=[None])])
def test_invalid_team_settings(body):
    with pytest.raises(ValidationError):
        TeamCreate.model_validate(body)


def test_team_crud_revision_uniqueness_and_audit(client, workflow, app):
    path, _, headers = workflow
    base, team = create_team(client, path, headers)
    listed = client.get(base).json()
    assert listed['can_manage'] and listed['teams'] == [team]
    for body in [team_body('bank-of-tanzania', []), team_body('Other', ['bot']), team_body('BOT', [])]:
        assert client.post(base, json=body, headers=headers).status_code == 409
    unchanged = client.post(base+'/'+team['id'], json={**team_body(), 'expected_revision': 1}, headers=headers)
    assert unchanged.status_code == 200 and unchanged.json()['revision'] == 1
    renamed = team_body('Central Bank', ['BOT', 'Benki Kuu'])
    updated = client.post(base+'/'+team['id'], json={**renamed, 'expected_revision': 1}, headers=headers)
    assert updated.status_code == 200 and updated.json()['revision'] == 2
    assert client.post(base+'/'+team['id'], json={**renamed, 'expected_revision': 1}, headers=headers).status_code == 409
    assert client.post(base+'/'+team['id']+'/delete', json={'expected_revision': 1}, headers=headers).status_code == 409
    assert client.post(base+'/'+team['id']+'/delete', json={'expected_revision': 2}, headers=headers).status_code == 204
    assert client.get(base).json()['teams'] == []
    assert client.post(base+'/'+team['id'], json={**renamed, 'expected_revision': 3}, headers=headers).status_code == 404
    assert create_team(client, path, headers)[1]['id'] != team['id']
    with app.state.session_factory() as db:
        assert db.get(SupportTeam, uuid.UUID(team['id'])).archived
        assert set(db.scalars(select(AgentChange.action).where(AgentChange.action.like('team_%')))) == {'team_created', 'team_updated', 'team_deleted'}


def test_workspace_permissions_csrf_and_foreign_team_are_enforced(client, workflow, app):
    path, _, headers = workflow
    base, team = create_team(client, path, headers)
    assert client.post(base, json=team_body('Missing CSRF', [])).status_code == 403
    with app.state.session_factory() as db:
        other = Workspace(name='Other'); db.add(other); db.flush()
        foreign = SupportTeam(workspace_id=other.id, name='Foreign', alternative_names=['Outside'])
        db.add(foreign); db.flush(); foreign_id, workspace_id = str(foreign.id), str(other.id); db.commit()
    assert client.get('/api/workspaces/'+workspace_id+'/teams').status_code == 404
    assert client.post(base+'/'+foreign_id, json={**team_body(), 'expected_revision': 1}, headers=headers).status_code == 404
    assert client.post(base+'/'+foreign_id+'/delete', json={'expected_revision': 1}, headers=headers).status_code == 404
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role = 'member'; db.commit()
    assert not client.get(base).json()['can_manage']
    assert client.post(base, json=team_body('Member', []), headers=headers).status_code == 403
    assert client.post(base+'/'+team['id']+'/delete', json={'expected_revision': 1}, headers=headers).status_code == 403
    with app.state.session_factory() as db:
        db.scalar(select(RolePermission).where(RolePermission.role=='member')).manage_ai_agents = True; db.commit()
    assert client.post(base, json=team_body('Member', []), headers=headers).status_code == 201
    client.cookies.clear()
    assert client.get(base).status_code == 401


TEAMS = [{'id': 'a', 'name': 'Bank of Tanzania', 'alternative_names': ['BOT', 'Benki Kuu']},
    {'id': 'b', 'name': 'NMB Bank', 'alternative_names': ['NMB']},
    {'id': 'c', 'name': 'Straße', 'alternative_names': ['Équipe']}]


@pytest.mark.parametrize('value,identifier,reason', [('bot', 'a', 'exact'), (' BANK--OF   TANZANIA!!', 'a', 'exact'),
    ('I need help with Benki Kuu, please.', 'a', 'mention'), ('My bank is NMB.', 'b', 'mention'),
    ('BOT and Bank of Tanzania', 'a', 'mention'), ('STRASSE', 'c', 'exact'), ('ＢＯＴ', 'a', 'exact'),
    ('équipe', 'c', 'exact'), ('BOT and NMB', None, 'ambiguous'), ('robot', None, 'no_match'),
    ('NMBank', None, 'no_match'), ('BoTt', None, 'no_match'), ('', None, 'no_match'), ('!!!', None, 'no_match')])
def test_matching_names_mentions_unicode_and_ambiguity(value, identifier, reason):
    result = match_team(value, TEAMS)
    assert (result.team or {}).get('id') == identifier and result.reason == reason


def test_duplicate_legacy_aliases_do_not_choose_arbitrary_team():
    result = match_team('BOT', TEAMS + [{'id':'other', 'name':'Other', 'alternative_names':['bot']}])
    assert result.team is None and result.reason == 'ambiguous'


def test_migration_preserves_custom_fields_chats_and_workflows(database_engine):
    schema = 'petrix_teams_upgrade_' + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    with database_engine.connect() as connection:
        original = connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes['connection'] = connection
            with connection.begin(): command.upgrade(config, '0009')
            workspace, field = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {'id':workspace})
            connection.execute(text("INSERT INTO conversation_fields (id,workspace_id,key,scope,type,name,description,options,revision,archived,created_at,updated_at) VALUES (:id,:workspace,'enquiry','conversation','text','Enquiry','','[]',4,false,now(),now())"), {'id':field, 'workspace':workspace})
            connection.commit()
            with connection.begin(): command.upgrade(config, 'head')
            with connection.begin(): command.upgrade(config, 'head')
            assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()==ScriptDirectory.from_config(config).get_current_head()
            assert connection.execute(text('SELECT name,revision FROM conversation_fields WHERE id=:id'), {'id':field}).one() == ('Enquiry',4)
            assert connection.execute(text('SELECT count(*) FROM support_teams')).scalar() == 0
            connection.commit()
            with connection.begin(): command.downgrade(config, '0009')
            assert connection.execute(text('SELECT name,revision FROM conversation_fields WHERE id=:id'), {'id':field}).one() == ('Enquiry',4)
            connection.commit()
        finally:
            connection.rollback(); connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
