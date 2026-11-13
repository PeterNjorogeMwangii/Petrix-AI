"""Seven graph types, typed data, immutable message snapshots and safe continuations."""
from copy import deepcopy
import json
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import select
from app.models import AgentConversation, AgentMessage
from app.workflows.actions.send_graph.config import SendGraphConfig
from app.workflows.actions.send_graph.data import evaluate_source, validate_rows
from app.workflows.actions.send_graph.handler import GraphSkipped, execute as send_graph
from app.workflows.context import ActionContext
from app.workflows.chat_script import ScriptError
from app.workflows.runtime import execute
from test_ask_survey import survey, field
from test_branch import branch, variable
from test_chat_closed import close, save_closed
from test_close_chat import configure, close_action
from test_collect_feedback import form, submit
from test_jump import setup_tree, prepare, jump
from test_send_api import api, StubAPI, APIResult
from test_sql_query import sql, StubSQL, QueryResult
from test_workflows import workflow, node, intent, message, event, save, contents

ROWS=[{'x':'Jan','y':1200},{'x':'Feb','y':0},{'x':'Mar','y':-980.5}]
MULTI=[{'x':'Jan','y':1200,'series':'Deposits'},{'x':'Jan','y':800,'series':'Withdrawals'},
    {'x':'Feb','y':1450,'series':'Deposits'},{'x':'Feb','y':-180,'series':'Withdrawals'}]
KINDS=['bar','grouped_bar','stacked_bar','line','multi_line','pie','combo']


def graph(kind='bar', **settings):
    rows=MULTI if kind in {'grouped_bar','stacked_bar','multi_line'} else [{'x':'Food','y':40},{'x':'Bills','y':60}] if kind=='pie' else ROWS
    config={'graph_type':kind,'data':json.dumps(rows),'title':'Transaction summary','x_axis_label':'Month','y_axis_label':'Amount',**settings}
    if kind=='combo':config.update(bar_data=settings.get('bar_data',json.dumps(ROWS)),line_data=settings.get('line_data',json.dumps([{'x':'Jan','y':12},{'x':'Mar','y':18}])) )
    return node('action','send_graph',name='Spending chart',config=config)


@pytest.mark.parametrize('kind',KINDS)
def test_each_type_is_one_persisted_message_and_continues(client,workflow,app,kind):
    path,document,headers=workflow
    action=graph(kind);action['children']=[message('Nested next')]
    document['nodes'][0]['children']=[action,message('After chart')]
    saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    result=event(client,path,headers);assert result.status_code==200,result.text
    messages=result.json()['messages'];assert [m['source'] for m in messages]==['send_graph','send_message','send_message']
    assert messages[0]['content']=='' and messages[0]['graph']['graph_type']==kind
    assert [m['content'] for m in messages[1:]]==['Nested next','After chart']
    payload=messages[0]['graph'];assert payload['version']==1 and payload['title']=='Transaction summary'
    if kind in {'bar','line'}:assert not payload['show_legend']
    if kind=='pie':assert not payload['show_grid']
    if kind=='combo':assert payload['bar_label']=='Bar' and payload['line_label']=='Line' and payload['y_axis_label']==''
    with app.state.session_factory() as db:
        row=db.get(AgentMessage,uuid.UUID(messages[0]['id']))
        assert row.message_data['graph']==payload and row.message_data['source']=='send_graph'


@pytest.mark.parametrize('kind,rows,issue',[
    ('bar',[],'empty'),('bar',{},'array'),('bar',[None],'Row 1'),('bar',[{'x':'Jan'}],'y: Field required'),
    ('bar',[{'y':1}],'x: Field required'),('bar',[{'x':'Jan','y':'10'}],'y:'),('bar',[{'x':'Jan','y':True}],'y:'),
    ('bar',[{'x':True,'y':1}],'x:'),('bar',[{'x':'','y':1}],'x:'),('bar',[{'x':'x'*121,'y':1}],'x:'),
    ('bar',[{'x':'Jan','y':1,'series':'A'}],'series:'),('line',[{'x':'Jan','y':1},{'x':' Jan ','y':2}],'Row 2'),
    ('bar',[{'x':1,'y':1},{'x':'1','y':2}],'duplicate'),('pie',[{'x':'Jan','y':-1}],'negative'),
    ('pie',[{'x':'Jan','y':1,'series':'A'}],'series:'),('grouped_bar',[{'x':'Jan','y':1}],'series: Field required'),
    ('stacked_bar',[{'x':'Jan','y':1,'series':''}],'series:'),('multi_line',[{'x':'Jan','y':1,'series':2}],'series:'),
    ('grouped_bar',[{'x':'Jan','y':1,'series':'A'},{'x':'Jan','y':2,'series':' A '}],'duplicate'),
    ('bar',[{'x':str(i),'y':1} for i in range(101)],'100 rows'),
    ('multi_line',[{'x':'Jan','y':1,'series':str(i)} for i in range(21)],'20 series'),
    ('bar',[{'x':'Jan','y':10**1000}],'finite'),('bar',[{'x':10**1000,'y':1}],'finite'),
    ('bar',[{'x':'Jan','y':float('inf')}],'finite'),('bar',[{'x':'Jan','y':float('nan')}],'finite')])
def test_row_validation_identifies_field_and_row(kind,rows,issue):
    with pytest.raises(ValueError,match=issue):validate_rows(rows,kind)


@pytest.mark.parametrize('settings',[{'graph_type':'scatter'},{'data':'[]'},{'data':'not json'},{'data':'[{"x":"Jan","y":1,"y":2}]'},
    {'data':'[{"x":"Jan","y":NaN}]'}, {'data_mode':'python'}, {'data':'__import__("os")','data_mode':'expression'},
    {'data':'x'*2001,'data_mode':'expression'},{'data':'x'*32001}, {'color_scheme':'rainbow'}, {'show_grid':'yes'},
    {'show_legend':1},{'title':'x'*201},{'extra':True}])
def test_invalid_literal_config_and_scripts_cannot_be_saved(settings):
    with pytest.raises(ValidationError):SendGraphConfig.model_validate(graph(**settings)['config'])


def test_combo_validation_is_specific_and_inactive_inputs_are_canonicalized():
    for settings,issue in [({'bar_data':'[]'},'Bar data input'),({'line_data':'[]'},'Line data input'),({'bar_label':'x'},'cannot be x'),
        ({'bar_data':'[{"x":"A","y":1,"series":"A"}]'},'series')]:
        with pytest.raises(ValidationError,match=issue):SendGraphConfig.model_validate(graph('combo',**settings)['config'])
    value=SendGraphConfig.model_validate(graph('combo',bar_label='Revenue',line_label='x')['config']).model_dump(mode='json')
    assert value['line_label']=='x' and value['data']=='' and value['y_axis_label']==''
    single=SendGraphConfig.model_validate(graph(bar_data='stale',line_data='stale')['config']).model_dump(mode='json')
    assert single['bar_data']==single['line_data']==''


@pytest.mark.parametrize('mode,source',[('template','{chart_rows}'),('template','{{ chart_rows }}'),('expression','chart_rows'),
    ('template','{response.rows}'),('expression','response.rows'),('template','{response.body}')])
def test_variable_and_api_sql_shapes(mode,source):
    agent,_=setup_tree();context=ActionContext(agent,'en',{}, {'variables':deepcopy({'chart_rows':ROWS,'response':{'rows':ROWS,'body':json.dumps(ROWS)}})})
    config=SendGraphConfig.model_validate(graph(data=source,data_mode=mode)['config']).model_dump(mode='json')
    output=send_graph(config,context)
    assert output['graph']['data']==ROWS
    context.variables['chart_rows'][0]['y']=99
    assert output['graph']['data'][0]['y']==1200


def test_typed_json_templates_are_single_pass_and_do_not_reinterpret_customer_text():
    agent,_=setup_tree();variables={'category':'A "quoted" name {private}', 'amount':12.5,'private':'MUST NOT APPEAR'}
    context=ActionContext(agent,'en',{}, {'variables':variables})
    for source in ['[{"x":"{category}","y":{amount}}]','[{"x":{category},"y":"{amount}"}]']:
        assert evaluate_source(source,'template',context)==[{'x':variables['category'],'y':12.5}]
    source='[{"x":"Month {category}","y":0}]'
    assert evaluate_source(source,'template',context)[0]['x']=='Month '+variables['category']
    assert SendGraphConfig.model_validate(graph(data=source)['config'])


@pytest.mark.parametrize('source',['{missing}','missing.rows','[{"x":"A","y":"bad"}]','None'])
def test_invalid_dynamic_input_is_a_step_error_without_partial_message(client,workflow,app,source):
    path,document,headers=workflow
    document['nodes'][0]['children']=[message('Before'),graph(data=source,data_mode='expression' if source in {'missing.rows','None'} else 'template')]
    saved=save(client,path,document,headers)
    if source.startswith('['):assert saved.status_code==422;return
    assert saved.status_code==200,saved.text
    result=event(client,path,headers);assert result.status_code==422 and 'Spending chart' in result.json()['detail']
    with app.state.session_factory() as db:assert not db.scalars(select(AgentConversation)).all()


@pytest.mark.parametrize('empty',[None,[], ''])
def test_combo_empty_input_skips_and_continues_even_after_graph_capacity(empty):
    agent,_=setup_tree();context=ActionContext(agent,'en',{}, {'variables':{'empty':empty}},graph_calls=10)
    config=SendGraphConfig.model_validate(graph('combo',line_data='{empty}')['config']).model_dump(mode='json')
    assert isinstance(send_graph(config,context),GraphSkipped)
    assert context.graph_calls==10


def test_empty_combo_skips_with_trace_and_preserves_next_actions(client,workflow):
    path,document,headers=workflow
    action=graph('combo',line_data='{empty}');action['children']=[message('Child')]
    document['nodes'][0]['children']=[variable('empty','[]'),action,message('Next')]
    assert save(client,path,document,headers).status_code==200
    result=event(client,path,headers)
    assert contents(result)==['Child','Next']
    assert next(step for step in result.json()['trace'] if step['type']=='send_graph')['outcome']=='skipped'


def test_ten_graphs_are_bounded_and_disabled_actions_are_skipped():
    agent,document=setup_tree();config=SendGraphConfig.model_validate(graph()['config']).model_dump(mode='json')
    context=ActionContext(agent,'en',{}, {})
    for _ in range(10):assert send_graph(config,context)['graph']
    with pytest.raises(ScriptError,match='10 graphs'):send_graph(config,context)
    disabled=graph();disabled['enabled']=False;document.nodes[0]['children']=[disabled,message('Next')];prepare(agent,document)
    assert [row['content'] for row in execute(document,agent,{}, {'event':'chat_started'})[1]]==['Next']


def test_survey_style_templates_branch_and_jump(client,workflow):
    path,document,headers=workflow;menu=message('Menu')
    action=graph(title='{customer_name} spending',data='[{"x":"Food","y":{amount}}]')
    action['children']=[jump(menu)]
    document['nodes'][0]['children']=[variable('amount','40'),survey(field('customer_name'),children=[action])]
    document['nodes'].append(intent('menu',[menu]))
    saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    asked=event(client,path,headers).json()
    result=event(client,path,headers,'message_received',asked['conversation_id'],content='Chris',survey_answer_token=asked['survey']['answer_token'])
    assert result.status_code==200,result.text
    assert result.json()['messages'][0]['graph']['title']=='Chris spending'
    assert result.json()['messages'][0]['graph']['data']==[{'x':'Food','y':40}]
    assert result.json()['messages'][1]['content']=='Menu'


def test_close_chat_and_feedback_can_send_graph_messages(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[message('Goodbye'),close_action()],[graph('pie')])
    result=event(client,path,headers).json();assert result['closed']
    assert [row['source'] for row in result['messages']]==['send_message','send_graph']
    document=client.get(path+'/workflows').json();document['nodes'][0]['children']=[]
    save_closed(client,path,document,headers,[form(survey=False),graph(data='[{"x":"Rating","y":{feedback_rating}}]')])
    requested=close(client,path,headers);submitted=submit(client,path,headers,requested,rating=5)
    assert submitted.status_code==200,submitted.text
    assert submitted.json()['closed'] and submitted.json()['messages'][0]['graph']['data']==[{'x':'Rating','y':5}]


def test_failed_post_close_graph_cannot_reopen_chat_or_lose_goodbye(client,workflow):
    path,document,headers=workflow
    configure(client,path,document,headers,[message('Goodbye'),close_action()],[graph(data='{missing}')])
    result=event(client,path,headers)
    assert result.status_code==200 and result.json()['closed'] and result.json()['automation_error']
    assert [row['content'] for row in result.json()['messages']]==['Goodbye']


def test_api_and_sql_results_feed_graphs_through_outcome_paths():
    agent,document=setup_tree()
    api_action=api(success=[graph(data='{metrics.body}')],url='https://api.example.test/metrics',variable_name='metrics')
    sql_action=sql(success=[branch(('query_results.ok', [graph(data='query_results.rows',data_mode='expression')]))],query='SELECT 1 AS y')
    document.nodes[0]['children']=[api_action,sql_action,message('Done')];prepare(agent,document)
    api_client=StubAPI(APIResult('success',200,ROWS));sql_client=StubSQL(QueryResult('success',ROWS,['x','y']))
    state,output,trace,matched=execute(document,agent,{}, {'event':'chat_started'},api_client=api_client,sql_client=sql_client)
    assert matched and len(api_client.calls)==len(sql_client.calls)==1
    assert [item['source'] for item in output]==['send_graph','send_graph','send_message']
    assert output[0]['graph']['data']==output[1]['graph']['data']==ROWS
    assert state['variables']['metrics']['ok'] and state['variables']['query_results']['row_count']==3


@pytest.mark.parametrize('empty_field',['bar_data','line_data'])
def test_empty_combo_always_skips_even_if_other_input_is_unavailable(empty_field):
    agent,_=setup_tree();context=ActionContext(agent,'en',{}, {'variables':{'empty':'   '}})
    inputs={'bar_data':'{missing}','line_data':'{missing}'};inputs[empty_field]='{empty}'
    config=SendGraphConfig.model_validate(graph('combo',**inputs)['config']).model_dump(mode='json')
    assert isinstance(send_graph(config,context),GraphSkipped)


def test_resolved_graph_byte_limit_and_dynamic_reserved_label():
    agent,_=setup_tree();context=ActionContext(agent,'en',{}, {'variables':{'label':'x'}})
    config=SendGraphConfig.model_validate(graph('combo',bar_label='{label}')['config']).model_dump(mode='json')
    with pytest.raises(ScriptError,match='cannot be x'):send_graph(config,context)
    rows=[{'x':str(i)+'🟠'*(120-len(str(i))),'y':1,'series':str(i%20)+'🔵'*(80-len(str(i%20)))} for i in range(100)]
    config=SendGraphConfig.model_validate(graph('grouped_bar',data=json.dumps(rows,ensure_ascii=False))['config']).model_dump(mode='json')
    with pytest.raises(ScriptError,match='64 KiB'):send_graph(config,context)
    assert context.graph_calls==0
