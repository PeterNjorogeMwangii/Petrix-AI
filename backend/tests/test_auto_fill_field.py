"""Constrained classifications, two paths, durable chat values and shared model limits."""
from copy import deepcopy
import json
import uuid
from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import select, text
from app.custom_fields.service import ticket_field_snapshot
from app.models import AgentConversation, ConversationField, Membership, Workspace
from app.workflows.actions.auto_fill_field.config import AutoFillFieldConfig
from app.workflows.actions.auto_fill_field.handler import execute as fill
from app.workflows.actions.auto_fill_field.service import AutoFillService, FillResult
from app.workflows.actions.llm_processing.client import ProcessingResult
from app.workflows.context import ActionContext
from test_ask_survey import field as survey_field, survey
from test_branch import branch, variable
from test_chat_closed import closed_root,close,save_closed
from test_close_chat import close_action,configure
from test_collect_feedback import form,submit
from test_custom_fields import create_field,body
from test_jump import jump,prepare,setup_tree
from test_llm_processing import llm
from test_workflows import workflow,node,message,intent,save,event,contents


def autofill(ids,success=None,error=None,**settings):
    config={'field_ids':ids,'variable_name':'matched_enquiry','success_path_id':str(uuid.uuid4()),'error_path_id':str(uuid.uuid4()),**settings}
    action=node('action','auto_fill_field',name='Categorise enquiry',config=config)
    action['paths']=[{'id':config[outcome+'_path_id'],'children':children or []} for outcome,children in [('success',success),('error',error)]]
    return action


class Provider:
    available=True
    def __init__(self,assignments=None,result=None):self.assignments=assignments;self.result=result;self.calls=[]
    def process(self,*args):
        self.calls.append(args)
        if self.result:return self.result
        payload=json.loads(args[1][-1]['content'])
        assignments=self.assignments if self.assignments is not None else [{'field_id':field['field_id'],'value':'mobile_banking'} for field in payload['fields']]
        return ProcessingResult('success',text=json.dumps({'assignments':assignments}))


@pytest.mark.parametrize('settings',[{'field_ids':[]},{'field_ids':['bad']},{'variable_name':'language'},{'variable_name':'system'},
    {'variable_name':'1bad'},{'field_ids':[str(uuid.uuid4()) for _ in range(21)]},{'timeout':3},{'variable_name':'class'}])
def test_invalid_action_settings_are_rejected(settings):
    config=autofill([str(uuid.uuid4())])['config'];config.update(settings)
    with pytest.raises(ValidationError):AutoFillFieldConfig.model_validate(config)


@pytest.mark.parametrize('change',['missing','extra','duplicate','children','collision'])
def test_two_outcome_paths_are_required(change):
    agent,document=setup_tree();action=autofill([str(uuid.uuid4())],success=[message('Good')]);document.nodes[0]['children']=[action]
    if change=='missing':action['paths'].pop()
    elif change=='extra':action['paths'].append({'id':str(uuid.uuid4()),'children':[]})
    elif change=='duplicate':action['paths'][1]=deepcopy(action['paths'][0])
    elif change=='children':action['children']=[message('Wrong')]
    else:action['paths'][0]['children'][0]['id']=action['paths'][0]['id']
    with pytest.raises(HTTPException):prepare(agent,document)


def test_survey_context_match_branches_jumps_and_persists_independently(client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers);provider=Provider();app.state.llm_processing_client=provider
    menu=message('Main menu')
    route=branch(('matched_enquiry.ok and matched_enquiry.fields.enquiry_type.value == "mobile_banking"',
        [message('Category: {system.custom_fields.enquiry_type.label}'),jump(menu)]),otherwise=[message('No category')])
    document['nodes'][0]['children']=[survey(survey_field('description')),autofill([field['id']],success=[route]),message('Abandoned')]
    document['nodes'].append(intent('menu',[menu]));saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    first=event(client,path,headers).json()
    response=event(client,path,headers,'message_received',first['conversation_id'],content='My mobile banking transfer failed.',survey_answer_token=first['survey']['answer_token'])
    assert contents(response)==['Category: Mobile banking','Main menu'];result=response.json()
    assert result['variables']['matched_enquiry']=={'ok':True,'fields':{'enquiry_type':{'field_id':field['id'],'value':'mobile_banking','label':'Mobile banking','group':'Banking'}},'unmatched':[],'error':None}
    assert result['custom_fields']['enquiry_type']['source']=='ai'
    payload=json.loads(provider.calls[0][1][-1]['content']);assert payload['conversation'][-1]['content']=='My mobile banking transfer failed.'
    assert any('Question for description' in item['content'] for item in payload['conversation'])
    assert provider.calls[0][2]==0 and provider.calls[0][3]==30
    with app.state.session_factory() as db:
        row=db.get(AgentConversation,uuid.UUID(result['conversation_id']));assert row.custom_fields==result['custom_fields']
        snapshot=ticket_field_snapshot(row);snapshot['enquiry_type']['value']='changed';assert row.custom_fields['enquiry_type']['value']=='mobile_banking'
        row.workflow_state={};db.commit()
    after=event(client,path,headers,'message_received',result['conversation_id'],content='menu').json()
    assert after['custom_fields']==result['custom_fields'] and len(provider.calls)==1


def test_no_match_preserves_defaults_and_existing_values_and_follows_success(client,workflow,app):
    path,document,headers=workflow;settings=body();settings['default_value']='accounts';_,field=create_field(client,path,headers,settings)
    provider=Provider(assignments=[{'field_id':field['id'],'value':None}]);app.state.llm_processing_client=provider
    document['nodes'][0]['children']=[autofill([field['id']],success=[message('Success')],error=[message('Error')]),message('After')]
    assert save(client,path,document,headers).status_code==200
    response=event(client,path,headers);assert contents(response)==['Success','After'];result=response.json()
    assert result['custom_fields']['enquiry_type']['value']=='accounts' and result['custom_fields']['enquiry_type']['source']=='default'
    assert result['variables']['matched_enquiry']=={'ok':True,'fields':{},'unmatched':['enquiry_type'],'error':None}
    trace=next(step for step in result['trace'] if step['type']=='auto_fill_field');assert trace['assigned_count']==0 and trace['unmatched_count']==1


@pytest.mark.parametrize('mode',['timeout','error','unknown_field','unknown_option','incomplete','duplicate','extra_key','invalid_json'])
def test_failed_classification_is_atomic_and_uses_error_branch(mode,client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers);_,other=create_field(client,path,headers,body('Other'))
    assignments=[{'field_id':field['id'],'value':'mobile_banking'},{'field_id':other['id'],'value':None}]
    if mode=='unknown_field':assignments[1]['field_id']=str(uuid.uuid4())
    elif mode=='unknown_option':assignments[1]['value']='invented option'
    elif mode=='incomplete':assignments.pop()
    elif mode=='duplicate':assignments[1]=dict(assignments[0])
    elif mode=='extra_key':assignments[1]['explanation']='not allowed'
    result=ProcessingResult('timeout',error='timeout') if mode=='timeout' else ProcessingResult('error',error='provider_error') if mode=='error' else ProcessingResult('success',text='not JSON') if mode=='invalid_json' else None
    provider=Provider(assignments,result);app.state.llm_processing_client=provider
    document['nodes'][0]['children']=[autofill([field['id'],other['id']],success=[message('Wrong')],error=[message('Unable to categorise.')]),message('After')]
    assert save(client,path,document,headers).status_code==200
    response=event(client,path,headers);assert contents(response)==['Unable to categorise.','After'];value=response.json()
    assert value['custom_fields']=={} and not value['variables']['matched_enquiry']['ok']
    assert value['variables']['matched_enquiry']['error']==('timeout' if mode=='timeout' else 'provider_error' if mode=='error' else 'invalid_response')


def test_reference_validation_rejects_wrong_type_foreign_or_deleted_fields(client,workflow,app):
    path,document,headers=workflow;_,text_field=create_field(client,path,headers,body('Text','text'))
    from app.core.security import utcnow
    with app.state.session_factory() as db:
        foreign=Workspace(name='Foreign',setup_completed_at=utcnow());db.add(foreign);db.flush()
        row=ConversationField(workspace_id=foreign.id,key='foreign',**body('Foreign'));db.add(row);db.flush();foreign_id=str(row.id);db.commit()
    for identifier in [text_field['id'],str(uuid.uuid4()),foreign_id]:
        document['nodes'][0]['children']=[autofill([identifier])]
        assert save(client,path,document,headers).status_code==422
    base,grouped=create_field(client,path,headers)
    document['nodes'][0]['children']=[autofill([grouped['id']],error=[message('Missing field')])]
    assert save(client,path,document,headers).status_code==200
    with app.state.session_factory() as db:
        db.get(ConversationField,uuid.UUID(grouped['id'])).archived=True;db.commit()
    provider=Provider();app.state.llm_processing_client=provider
    assert contents(event(client,path,headers))==['Missing field'] and not provider.calls


def test_changed_definition_is_rejected_after_provider_response(client,workflow,app):
    from app.workflows.actions.llm_processing.service import LLMProcessingService
    path,_,headers=workflow;_,field=create_field(client,path,headers)
    with app.state.session_factory() as db:
        from app.models import Agent
        agent=db.get(Agent,uuid.UUID(path.rsplit('/',1)[1]));conversation=AgentConversation(workspace_id=agent.workspace_id,agent_id=agent.id,user_id=db.scalar(select(Membership)).user_id,agent_name=agent.name)
        db.add(conversation);db.flush()
        class UpdatingProvider(Provider):
            def process(self,*args):
                db.execute(text('UPDATE conversation_fields SET revision=revision+1 WHERE id=:id'),{'id':uuid.UUID(field['id'])})
                return super().process(*args)
        processing=LLMProcessingService(db,agent,app.state.settings,UpdatingProvider())
        service=AutoFillService(db,agent.workspace_id,conversation.id,processing)
        context=ActionContext(agent,'en',{'content':'Mobile banking'}, {})
        assert service.fill([field['id']],context).error=='fields_changed'


def test_post_close_and_feedback_continuation_assign_fields_without_message(client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers);provider=Provider();app.state.llm_processing_client=provider
    feedback=form();key=feedback['config']['fields'][0]['id']
    save_closed(client,path,document,headers,[feedback,autofill([field['id']],success=[variable('classified','True')])])
    requested=close(client,path,headers);assert not provider.calls
    response=submit(client,path,headers,requested,answers={key:'My mobile transfer failed'})
    assert response.status_code==200,response.text
    result=response.json();assert result['closed'] and result['messages']==[] and result['variables']['classified']
    assert result['custom_fields']['enquiry_type']['value']=='mobile_banking'
    payload=json.loads(provider.calls[0][1][-1]['content']);assert payload['feedback'][key]=='My mobile transfer failed'


def test_budget_shared_with_llm_processing_and_close_chat(client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers);provider=Provider();app.state.llm_processing_client=provider
    actions=[llm(input='{"fields":[]}')]+[autofill([field['id']]) for _ in range(2)]+[close_action()]
    # The generic LLM call's provider response is just JSON text; the next two calls classify fields.
    configure(client,path,document,headers,actions,[autofill([field['id']],error=[variable('limited','True')])])
    result=event(client,path,headers).json()
    assert result['closed'] and result['variables']['limited'] and len(provider.calls)==3
    assert any(step.get('error')=='processing_limit' for step in result['trace'])


def test_later_failure_rolls_back_assignments_and_disabled_action_makes_no_request(client,workflow,app):
    path,document,headers=workflow;_,field=create_field(client,path,headers);provider=Provider();app.state.llm_processing_client=provider
    document['nodes'][0]['children']=[autofill([field['id']]),variable('bad','missing_variable')]
    saved=save(client,path,document,headers);assert saved.status_code==200
    assert event(client,path,headers).status_code==422 and len(provider.calls)==1
    with app.state.session_factory() as db:assert not db.scalars(select(AgentConversation)).all()
    document=saved.json();action=autofill([field['id']]);action['enabled']=False;document['nodes'][0]['children']=[action,message('Done')]
    assert save(client,path,document,headers).status_code==200
    assert contents(event(client,path,headers))==['Done'] and len(provider.calls)==1


def test_variable_limit_prevents_requests_and_atomic_field_assignment():
    agent,_=setup_tree()
    class Stub:
        model='test';calls=0
        def fill(self,*args):self.calls+=1;return FillResult({'enquiry':{'field_id':str(uuid.uuid4()),'value':'a','label':'你'*8192,'group':'Group'}},[])
    client=Stub();context=ActionContext(agent,'en',{}, {'variables':{'v'+str(i):i for i in range(100)},'custom_fields':{'old':{'value':'kept'}}},auto_fill_client=client)
    config=AutoFillFieldConfig.model_validate(autofill([str(uuid.uuid4())])['config']).model_dump(mode='json')
    assert fill(config,context).error=='variable_limit' and client.calls==0
    context.state['variables']={}
    assert fill(config,context).error=='variable_limit' and context.state['custom_fields']=={'old':{'value':'kept'}}
