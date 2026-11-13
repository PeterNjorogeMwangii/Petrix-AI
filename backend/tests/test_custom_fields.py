"""Conversation field settings, scoped references, typed defaults and preservation."""
from copy import deepcopy
from pathlib import Path
import uuid
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import select, text
from app.custom_fields.schemas import FieldCreate
from app.custom_fields.service import ticket_field_snapshot
from app.models import AgentConversation, ConversationField, Membership, RolePermission
from test_workflows import workflow, event, save, message


def groups(value='mobile_banking'):
    return [{'name':'Banking','options':[{'value':value,'label':'Mobile banking','description':'Mobile application, transfers and payments.'},
        {'value':'accounts','label':'Accounts','description':'Account opening or account details.'}]}]


def body(name='Enquiry type',type='single_select_grouped',**extra):
    return dict(scope='conversation',type=type,name=name,description='Categorise the customer enquiry.',options=groups() if type=='single_select_grouped' else [],default_value=None,**extra)


def create_field(client,path,headers,settings=None):
    base=path.rsplit('/agents/',1)[0]+'/custom-fields'
    response=client.post(base,json=settings or body(),headers=headers)
    assert response.status_code==201,response.text
    return base,response.json()


def test_all_field_types_defaults_and_grouped_only_options(client,workflow,app):
    path,_,headers=workflow
    settings=[body('Greeting','text'),body('Score','number'),body('Follow up','toggle'),body('Priority','dropdown'),body('Due date','date'),body()]
    settings[0]['default_value']='Hello';settings[1]['default_value']=0;settings[2]['default_value']=False
    settings[3].update(options=[{'value':'normal','label':'Normal'}],default_value='normal')
    settings[4]['default_value']='2026-11-13';settings[5]['default_value']='accounts'
    created=[create_field(client,path,headers,setting)[1] for setting in settings]
    base=path.rsplit('/agents/',1)[0]+'/custom-fields'
    listed=client.get(base).json();assert listed['can_manage'] and len(listed['fields'])==6
    assert client.get(base+'/options').json()['fields']==[created[-1]]
    result=event(client,path,headers).json();values=result['custom_fields']
    assert values['score']['value']==0 and values['follow_up']['value'] is False and values['due_date']['value']=='2026-11-13'
    assert values['enquiry_type']['label']=='Accounts' and values['enquiry_type']['source']=='default'
    with app.state.session_factory() as db:
        conversation=db.get(AgentConversation,uuid.UUID(result['conversation_id']))
        assert conversation.custom_fields==values
        copied=ticket_field_snapshot(conversation);copied['score']['value']=99
        assert conversation.custom_fields['score']['value']==0
    field=created[-1];update={**settings[-1],'expected_revision':field['revision']}
    same=client.post(base+'/'+field['id'],json=update,headers=headers);assert same.json()['revision']==1
    changed=client.post(base+'/'+field['id'],json={**update,'name':'Enquiry category'},headers=headers)
    assert changed.status_code==200 and changed.json()['key']=='enquiry_type' and changed.json()['revision']==2
    assert client.post(base+'/'+field['id'],json=update,headers=headers).status_code==409
    assert client.get(base+'/options').json()['fields'][0]['name']=='Enquiry category'


@pytest.mark.parametrize('change',['name','scope','type','extra','empty_groups','duplicate_group','duplicate_value','blank_label','foreign_default',
    'wrong_text','long_text','wrong_number','infinite','huge','wrong_toggle','wrong_date','options_for_text','oversized','many_groups'])
def test_settings_reject_invalid_data(change):
    value=body()
    if change=='name':value['name']=' '
    elif change=='scope':value['scope']='people'
    elif change=='type':value['type']='script'
    elif change=='extra':value['private']=True
    elif change=='empty_groups':value['options']=[]
    elif change=='duplicate_group':value['options'].append(deepcopy(value['options'][0]))
    elif change=='duplicate_value':value['options'][0]['options'][1]['value']='mobile_banking'
    elif change=='blank_label':value['options'][0]['options'][0]['label']=' '
    elif change=='foreign_default':value['default_value']='unknown'
    elif change=='wrong_text':value.update(type='text',options=[],default_value=3)
    elif change=='long_text':value.update(type='text',options=[],default_value='x'*4001)
    elif change=='wrong_number':value.update(type='number',options=[],default_value=True)
    elif change=='infinite':value.update(type='number',options=[],default_value=float('inf'))
    elif change=='huge':value.update(type='number',options=[],default_value=10**1000)
    elif change=='wrong_toggle':value.update(type='toggle',options=[],default_value='true')
    elif change=='wrong_date':value.update(type='date',options=[],default_value='2026-02-30')
    elif change=='options_for_text':value['type']='text'
    elif change=='many_groups':value['options']=[{'name':str(i),'options':[{'value':str(i),'label':'Value'}]} for i in range(21)]
    else:value['options']=[{'name':'Group','options':[{'value':str(i),'label':'Value','description':'你'*1000} for i in range(30)]}]
    with pytest.raises(ValidationError):FieldCreate.model_validate(value)


def test_names_keys_permissions_and_deletion_are_scoped(client,workflow,app):
    path,_,headers=workflow;base,field=create_field(client,path,headers)
    assert client.post(base,json=body(),headers=headers).status_code==409
    reserved=create_field(client,path,headers,body('Class'))[1]
    assert reserved['key']=='field_class'
    first=create_field(client,path,headers,body('42 '+('x'*90)))[1]
    second=create_field(client,path,headers,body('42-'+('x'*90)))[1]
    assert len(first['key'])<=64 and len(second['key'])<=64 and first['key']!=second['key']
    assert client.post(base,json=body('CSRF')).status_code==403
    foreign='/api/workspaces/'+str(uuid.uuid4())+'/custom-fields'
    assert client.get(foreign).status_code==404
    assert client.post(foreign,json=body(),headers=headers).status_code==404
    with app.state.session_factory() as db:
        db.scalar(select(Membership)).role='member';db.commit()
    assert client.get(base).status_code==200 and not client.get(base).json()['can_manage']
    assert client.post(base,json=body('Member'),headers=headers).status_code==403
    with app.state.session_factory() as db:
        db.scalar(select(RolePermission).where(RolePermission.role=='member')).manage_ai_agents=True;db.commit()
    assert client.post(base,json=body('Member'),headers=headers).status_code==201
    response=client.post(base+'/delete',json={'fields':[{'id':field['id'],'expected_revision':1}]},headers=headers)
    assert response.status_code==204
    assert all(item['id']!=field['id'] for item in client.get(base).json()['fields'])
    with app.state.session_factory() as db:assert db.get(ConversationField,uuid.UUID(field['id'])).archived
    client.cookies.clear();assert client.get(base).status_code==401


def test_bulk_delete_is_atomic_and_referenced_fields_cannot_change_type(client,workflow):
    from test_auto_fill_field import autofill
    path,document,headers=workflow;base,field=create_field(client,path,headers)
    other=create_field(client,path,headers,body('Other'))[1]
    action=autofill([field['id']]);action['enabled']=False;document['nodes'][0]['children']=[action]
    saved=save(client,path,document,headers);assert saved.status_code==200,saved.text
    deletion={'fields':[{'id':value['id'],'expected_revision':1} for value in (other,field)]}
    assert client.post(base+'/delete',json=deletion,headers=headers).status_code==409
    assert len(client.get(base).json()['fields'])==2
    settings=body(type='text');settings.update(options=[],expected_revision=1)
    assert client.post(base+'/'+field['id'],json=settings,headers=headers).status_code==409
    document=saved.json();document['nodes'][0]['children']=[];assert save(client,path,document,headers).status_code==200
    assert client.post(base+'/delete',json=deletion,headers=headers).status_code==204
    assert client.get(base).json()['fields']==[]


def test_migration_preserves_existing_chats_and_workflows(database_engine):
    schema='petrix_fields_upgrade_'+uuid.uuid4().hex;config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    with database_engine.connect() as connection:
        original=connection.execute(text('SELECT current_schema()')).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema)));connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema)));connection.commit();config.attributes['connection']=connection
            with connection.begin():command.upgrade(config,'0008')
            workspace,agent,user,conversation=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"),{'id':workspace})
            connection.execute(text("INSERT INTO users (id,email,created_at) VALUES (:id,'fields@example.com',now())"),{'id':user})
            connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing',true,'','[\"en\"]','en',false,'',1,1,now(),now())"),{'id':agent,'workspace':workspace})
            connection.execute(text("INSERT INTO agent_workflows (agent_id,revision,next_node_number,nodes,updated_at) VALUES (:id,9,42,'[]',now())"),{'id':agent})
            connection.execute(text("INSERT INTO agent_conversations (id,workspace_id,agent_id,user_id,agent_name,workflow_state,created_at) VALUES (:id,:workspace,:agent,:user,'Existing','{\"variables\":{\"ref\":\"REF-1\"}}',now())"),{'id':conversation,'workspace':workspace,'agent':agent,'user':user});connection.commit()
            with connection.begin():command.upgrade(config,'head')
            with connection.begin():command.upgrade(config,'head')
            assert connection.execute(text('SELECT custom_fields,workflow_state FROM agent_conversations')).one()==({}, {'variables':{'ref':'REF-1'}})
            assert connection.execute(text('SELECT revision,next_node_number FROM agent_workflows')).one()==(9,42)
            assert connection.execute(text('SELECT count(*) FROM conversation_fields')).scalar()==0;connection.commit()
            with connection.begin():command.downgrade(config,'0008')
            assert connection.execute(text('SELECT workflow_state FROM agent_conversations')).scalar()=={'variables':{'ref':'REF-1'}};connection.commit()
        finally:
            connection.rollback();connection.execute(text('SET search_path TO "{}"'.format(original)));connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema)));connection.commit()
