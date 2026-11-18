"""Team matching in real workflows: all outcomes, survey inputs and post-close paths."""
from copy import deepcopy
from types import SimpleNamespace
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import select, text
from app.models import AgentConversation, SupportTeam, Workspace
from app.teams.matching import match_team
from app.workflows.actions.auto_team_match.config import AutoTeamMatchConfig
from app.workflows.actions.auto_team_match.handler import execute as match
from app.workflows.context import ActionContext
from test_ask_survey import field, survey
from test_branch import variable
from test_chat_closed import save_closed, close
from test_close_chat import configure, close_action
from test_collect_feedback import form, submit
from test_jump import setup_tree, prepare, jump
from test_teams import create_team, team_body
from test_workflows import workflow, node, message, intent, save, event, contents


def team_action(found=None, no_match=None, error=None, **settings):
    config = {'input':'{financial_institution}', 'input_mode':'template', 'variable_name':'matched_team',
        'match_path_id':str(uuid.uuid4()), 'no_match_path_id':str(uuid.uuid4()), 'error_path_id':str(uuid.uuid4()), **settings}
    action = node('action', 'auto_team_match', name='Identify support team', config=config)
    action['paths'] = [{'id':config[key+'_path_id'], 'children':children or []}
        for key, children in [('match', found), ('no_match', no_match), ('error', error)]]
    return action


@pytest.mark.parametrize('settings', [{'input':''}, {'input':' '}, {'input':'x'*16001}, {'input_mode':'python'},
    {'input':'__import__("os")', 'input_mode':'expression'}, {'input':'x'*2001, 'input_mode':'expression'},
    {'variable_name':'1bad'}, {'variable_name':'bad-name'}, {'variable_name':'class'}, {'variable_name':'str'},
    {'variable_name':'system'}, {'variable_name':'language'}, {'variable_name':'message'}, {'variable_name':'x'*65}, {'extra':True}])
def test_action_settings_reject_invalid_names_and_scripts(settings):
    with pytest.raises(ValidationError): AutoTeamMatchConfig.model_validate(team_action(**settings)['config'])


@pytest.mark.parametrize('change', ['missing','extra','duplicate','children','collision','same_ids'])
def test_three_distinct_outcomes_are_required(change):
    agent, document = setup_tree(); action = team_action(found=[message('Good')]); document.nodes[0]['children'] = [action]
    if change == 'missing': action['paths'].pop()
    elif change == 'extra': action['paths'].append({'id':str(uuid.uuid4()),'children':[]})
    elif change == 'duplicate': action['paths'][1] = deepcopy(action['paths'][0])
    elif change == 'children': action['children'] = [message('Wrong')]
    elif change == 'collision': action['paths'][0]['children'][0]['id'] = action['paths'][0]['id']
    else: action['config']['no_match_path_id'] = action['config']['match_path_id']
    with pytest.raises(HTTPException): prepare(agent, document)


@pytest.mark.parametrize('mode,source', [('template','{financial_institution}'),('expression','financial_institution.text')])
def test_survey_match_branch_jump_and_saved_team_result(client, workflow, app, mode, source):
    path, document, headers = workflow; _, team = create_team(client, path, headers)
    menu = message('Menu')
    action = team_action(found=[message('Matched {matched_team.name}'), jump(menu)], input=source, input_mode=mode)
    document['nodes'][0]['children'] = [survey(field('financial_institution'), children=[action])]
    document['nodes'].append(intent('menu', [menu]))
    response = save(client, path, document, headers); assert response.status_code == 200, response.text
    asked = event(client, path, headers).json()
    result = event(client, path, headers, 'message_received', asked['conversation_id'], content='bot', survey_answer_token=asked['survey']['answer_token'])
    assert contents(result) == ['Matched Bank of Tanzania', 'Menu']
    payload = result.json(); assert payload['variables']['matched_team'] == {'id':team['id'], 'name':team['name']}
    trace = next(step for step in payload['trace'] if step['type']=='auto_team_match')
    assert trace['outcome']=='match_found' and trace['match_reason']=='exact' and trace['team_name']==team['name']
    with app.state.session_factory() as db:
        conversation = db.get(AgentConversation, uuid.UUID(payload['conversation_id']))
        assert conversation.workflow_state['variables']['matched_team']['id'] == team['id']
        from app.models import UsageEvent
        from app.workspace_settings.usage import record_actions
        before=list(db.scalars(select(UsageEvent).where(UsageEvent.workspace_id==conversation.workspace_id,UsageEvent.category=='auto_team_match')))
        assert len(before)==1
        event_id=uuid.uuid4()
        record_actions(db,conversation.workspace_id,conversation.agent_id,conversation.user_id,payload,event_id)
        record_actions(db,conversation.workspace_id,conversation.agent_id,conversation.user_id,payload,event_id)
        assert len(list(db.scalars(select(UsageEvent).where(UsageEvent.workspace_id==conversation.workspace_id,UsageEvent.category=='auto_team_match'))))==2


@pytest.mark.parametrize('source,mode,expected', [('Unknown','template','no_match'), ('BOT and NMB','template','no_match'),
    ('{missing}','template','error'), ('missing.text','expression','error'), ('123','expression','error'),
    ('None','expression','error'), ('""','expression','no_match'), ('{empty}','template','no_match')])
def test_no_match_error_and_empty_inputs_clear_stale_result(client, workflow, source, mode, expected):
    path, document, headers = workflow; create_team(client, path, headers)
    create_team(client, path, headers, team_body('NMB Bank', ['NMB']))
    document['nodes'][0]['children'] = [variable('matched_team','{"id":"old","name":"Old team"}'), variable('empty','""'),
        team_action(found=[message('Found')], no_match=[message('Clarify')], error=[message('Failed')], input=source, input_mode=mode), message('Continue')]
    response = save(client, path, document, headers); assert response.status_code == 200, response.text
    result = event(client, path, headers)
    assert contents(result) == [('Clarify' if expected=='no_match' else 'Failed'), 'Continue']
    assert result.json()['variables']['matched_team'] is None
    trace = next(step for step in result.json()['trace'] if step['type']=='auto_team_match')
    assert trace['outcome'] == expected
    if source=='BOT and NMB': assert trace['match_reason']=='ambiguous'


def test_names_are_scoped_and_archived_teams_stop_matching(client, workflow, app):
    path, document, headers = workflow; base, team = create_team(client, path, headers)
    with app.state.session_factory() as db:
        other=Workspace(name='Foreign'); db.add(other); db.flush()
        db.add(SupportTeam(workspace_id=other.id, name='Outside', alternative_names=['Unknown'])); db.commit()
    action = team_action(no_match=[message('No match')], found=[message('Found')], input='Unknown')
    document['nodes'][0]['children']=[action]
    document=save(client,path,document,headers).json()
    assert contents(event(client,path,headers))==['No match']
    action['config']['input']='BOT';document['nodes'][0]['children']=[action]
    document=save(client,path,document,headers).json()
    assert contents(event(client,path,headers))==['Found']
    updated=client.post(base+'/'+team['id'],json={**team_body(alternatives=['Central']), 'expected_revision':1},headers=headers)
    assert updated.status_code==200
    assert contents(event(client,path,headers))==['No match']
    action['config']['input']='Central';document['nodes'][0]['children']=[action]
    save(client,path,document,headers)
    assert contents(event(client,path,headers))==['Found']
    assert client.post(base+'/'+team['id']+'/delete',json={'expected_revision':2},headers=headers).status_code==204
    assert contents(event(client,path,headers))==['No match']


def test_close_chat_and_feedback_continuation_run_team_paths(client, workflow):
    path, document, headers = workflow; _, team=create_team(client,path,headers)
    after_close=team_action(found=[variable('closed_team','matched_team.id')],input='BOT')
    configure(client,path,document,headers,[close_action()],[after_close])
    result=event(client,path,headers).json()
    assert result['closed'] and result['variables']['closed_team']==team['id']
    assert result['messages']==[]
    document=client.get(path+'/workflows').json(); document['nodes'][0]['children']=[]
    feedback=form();key=feedback['config']['fields'][0]['id']
    save_closed(client,path,document,headers,[feedback,team_action(input='{feedback_comment}',found=[variable('feedback_team','matched_team.id')])])
    requested=close(client,path,headers)
    submitted=submit(client,path,headers,requested,answers={key:'I contacted BOT'})
    assert submitted.status_code==200,submitted.text
    assert submitted.json()['closed'] and submitted.json()['variables']['feedback_team']==team['id']


def test_database_lookup_failure_uses_error_path_without_losing_prior_actions(client,workflow,app,monkeypatch):
    from app.teams import service
    path,document,headers=workflow
    original=service.definitions
    def fail_read(db, workspace_id):
        db.execute(text('SELECT * FROM deliberately_missing_team_table'))
        return original(db, workspace_id)
    monkeypatch.setattr(service,'definitions',fail_read)
    document['nodes'][0]['children']=[variable('before','42'),team_action(input='BOT',error=[message('Lookup unavailable')])]
    saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    result=event(client,path,headers)
    assert contents(result)==['Lookup unavailable'] and result.json()['variables']['before']==42
    assert next(step for step in result.json()['trace'] if step['type']=='auto_team_match')['error']=='teams_unavailable'


class Matcher:
    def __init__(self): self.calls=0
    def match(self,text):
        self.calls+=1
        return match_team(text,[{'id':'a','name':'Bank','alternative_names':['BOT']}])


def test_script_reuses_output_input_and_variable_limits_prevent_lookup():
    agent,_=setup_tree();matcher=Matcher();config=team_action(input='matched_team.text',input_mode='expression')['config']
    context=ActionContext(agent,'en',{}, {'variables':{'matched_team':'BOT'}},team_match_client=matcher)
    assert match(config,context).outcome=='match_found' and context.variables['matched_team']=={'id':'a','name':'Bank'}
    context.state['variables']={str(i):0 for i in range(100)}
    config['input']='"BOT"'
    assert match(config,context).error=='variable_limit' and matcher.calls==1
    context.state['variables']={'matched_team':{'id':'stale'}};context.team_match_client=None
    assert match(config,context).error=='teams_unavailable' and context.variables['matched_team'] is None


def test_disabled_action_leaves_variables_and_subsequent_failures_roll_back(client,workflow,app):
    path,document,headers=workflow;create_team(client,path,headers)
    action=team_action(input='BOT');action['enabled']=False
    document['nodes'][0]['children']=[variable('matched_team','"kept"'),action,message('Done')]
    document=save(client,path,document,headers).json()
    result=event(client,path,headers);assert contents(result)==['Done'] and result.json()['variables']['matched_team']=='kept'
    action['enabled']=True;document['nodes'][0]['children']=[action,variable('bad','missing_variable')]
    assert save(client,path,document,headers).status_code==200
    with app.state.session_factory() as db:before=len(db.scalars(select(AgentConversation)).all())
    assert event(client,path,headers).status_code==422
    with app.state.session_factory() as db:assert len(db.scalars(select(AgentConversation)).all())==before
