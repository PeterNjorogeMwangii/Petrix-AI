"""Complete post-close CSAT forms, persisted variables and safe continuation."""
from copy import deepcopy
import json
import uuid

from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app.models import AgentConversation, WorkflowDocument
from app.workflows.actions.collect_feedback.config import CollectFeedbackConfig, DEFAULT_SUBMISSION
from test_branch import branch, variable
from test_chat_closed import closed_root, close, save_closed
from test_jump import jump
from test_send_api import StubAPI, api
from test_workflows import event, intent, message, node, save, workflow


def field(reply_type='open_response', required=False, name='feedback_comment', question='Any other comments?', choices=None):
    key = str(uuid.uuid4())
    return ({'id': key, 'reply_type': reply_type, 'required': required, 'variable_name': name},
        {'question': question, 'placeholder': 'Please share your comments', 'choices': choices or []})


def form(rating=True, survey=True, fields=None, **settings):
    fields = fields if fields is not None else [field()] if survey else []
    config = {'rating_enabled': rating, 'rating_variable_name': 'feedback_rating', 'survey_enabled': survey,
        'fields': [item[0] for item in fields], 'translations': {'en': {'webchat_rating_title': 'How did we do?',
        'rating_request_message': 'How would you rate your conversation?', 'submission_text': DEFAULT_SUBMISSION,
        'fields': {item[0]['id']: item[1] for item in fields}}}}
    config.update(settings)
    return node('action', 'collect_feedback', config=config)


def submit(client, path, headers, requested, rating=4, answers=None):
    return event(client, path, headers, 'collect_feedback', requested['conversation_id'], rating=rating,
        feedback_token=requested['feedback']['token'], feedback_answers=answers or {})


@pytest.mark.parametrize('name', ['1bad', 'a-b', 'two words', '_hidden', 'class', 'system', 'language', 'str', 'x'*65])
@pytest.mark.parametrize('destination', ['rating', 'survey'])
def test_feedback_variable_names_reject_reserved_or_invalid_names(name, destination):
    value = form()['config']
    if destination == 'rating': value['rating_variable_name'] = name
    else: value['fields'][0]['variable_name'] = name
    with pytest.raises(ValidationError): CollectFeedbackConfig.model_validate(value)


def test_default_rating_variable_is_writable_and_legacy_config_gains_settings():
    value = CollectFeedbackConfig.model_validate({'rating_enabled': True, 'translations': {'en': {'webchat_rating_title': 'Rate us'}}})
    assert value.rating_variable_name == 'feedback_rating' and not value.survey_enabled
    assert value.translations['en'].rating_request_message and value.translations['en'].submission_text == DEFAULT_SUBMISSION


@pytest.mark.parametrize('mutation', ['empty_fields','duplicate_id','duplicate_name','rating_collision','unknown_translation_field','empty_question','empty_choices','duplicate_choices','too_many_choices','unknown_type','empty_request','empty_submission','bad_boolean'])
def test_incomplete_or_ambiguous_feedback_configuration_is_rejected(mutation):
    value = form()['config']; item = value['fields'][0]; text = value['translations']['en']; question = text['fields'][item['id']]
    if mutation == 'empty_fields': value['fields'] = []; text['fields'] = {}
    elif mutation == 'duplicate_id': value['fields'].append({**item, 'variable_name': 'other'})
    elif mutation == 'duplicate_name': value['fields'].append({**item, 'id': str(uuid.uuid4())})
    elif mutation == 'rating_collision': item['variable_name'] = 'feedback_rating'
    elif mutation == 'unknown_translation_field': text['fields'][str(uuid.uuid4())] = deepcopy(question)
    elif mutation == 'empty_question': question['question'] = ' '
    elif mutation == 'empty_choices': item['reply_type'] = 'choice'
    elif mutation == 'duplicate_choices': item['reply_type'] = 'choice'; question['choices'] = ['Yes','Yes']
    elif mutation == 'too_many_choices': question['choices'] = [str(i) for i in range(14)]
    elif mutation == 'unknown_type': item['reply_type'] = 'html'
    elif mutation == 'empty_request': text['rating_request_message'] = ' '
    elif mutation == 'empty_submission': text['submission_text'] = ''
    else: value['survey_enabled'] = 'true'
    with pytest.raises(ValidationError): CollectFeedbackConfig.model_validate(value)


def test_rating_and_optional_comment_are_saved_before_following_actions(client, workflow, app):
    path, document, headers = workflow; action = form(); key = action['config']['fields'][0]['id']
    action['children'] = [variable('inside_feedback', 'feedback_rating + 1')]
    save_closed(client, path, document, headers, [action, branch(('feedback_rating >= 4', [variable('outcome', '"happy"')])),
        variable('comment_copy', 'feedback_comment')])
    requested = close(client, path, headers)
    assert requested['closed'] and requested['messages'] == [] and 'outcome' not in requested['variables']
    assert all(private not in requested['feedback'] for private in ['continuation','workflow_revision','submission_template'])
    response = submit(client, path, headers, requested, 5, {key: 'Very helpful agent.'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['variables'] == {'feedback_rating': 5, 'feedback_comment': 'Very helpful agent.', 'inside_feedback': 6, 'outcome': 'happy', 'comment_copy': 'Very helpful agent.'}
    assert result['feedback_confirmation'] == DEFAULT_SUBMISSION and result['messages'] == [] and not result['waiting']
    with app.state.session_factory() as db:
        stored = db.get(AgentConversation, uuid.UUID(requested['conversation_id'])).workflow_state
        assert stored['close_automation']['status'] == 'completed' and stored['feedback_automation']['status'] == 'completed'
        assert stored['variables'] == result['variables'] and stored['feedback_submitted_at']
    assert submit(client, path, headers, requested, 3, {key:'duplicate'}).status_code == 409


@pytest.mark.parametrize('reply_type,text,expected', [('open_response','  Helpful  ','Helpful'),('number','12.50',12.5),('number','0',0),
    ('email','Alex@EXAMPLE.com','Alex@example.com'),('phone','+254 (712) 345-678','+254712345678'),('choice','Yes','Yes')])
def test_survey_only_forms_store_typed_answers_without_requiring_a_rating(client, workflow, reply_type, text, expected):
    path, document, headers = workflow; item = field(reply_type, True, choices=['Yes','No'] if reply_type=='choice' else [])
    action = form(False, True, [item]); save_closed(client, path, document, headers, [action])
    requested = close(client, path, headers); assert requested['feedback'] and not requested['feedback']['rating_enabled']
    response = submit(client, path, headers, requested, None, {item[0]['id']:text})
    assert response.status_code == 200, response.text
    result = response.json(); assert result['variables']['feedback_comment'] == expected
    assert result['csat_rating'] is None and 'feedback_rating' not in result['variables'] and result['feedback_submitted']


@pytest.mark.parametrize('reply_type,invalid,valid', [('open_response','','Thanks'),('number','NaN','10'),('number','1e999','1'),
    ('email','not-an-email','alex@example.com'),('phone','abc','0712345678'),('choice','Other','Yes')])
def test_invalid_answers_do_not_consume_the_token_or_partially_store_rating(client, workflow, app, reply_type, invalid, valid):
    path, document, headers = workflow; item = field(reply_type, True, choices=['Yes','No'] if reply_type=='choice' else [])
    save_closed(client, path, document, headers, [form(fields=[item])]); requested = close(client, path, headers)
    response = submit(client, path, headers, requested, 4, {item[0]['id']: invalid}); assert response.status_code == 422
    with app.state.session_factory() as db:
        state = db.get(AgentConversation, uuid.UUID(requested['conversation_id'])).workflow_state
        assert state['closed'] and not state.get('feedback_submitted') and 'feedback_rating' not in state['variables']
        assert state['feedback_request']['token'] == requested['feedback']['token']
    assert submit(client, path, headers, requested, 4, {item[0]['id']:valid}).status_code == 200


def test_optional_blank_comment_is_saved_as_none(client, workflow):
    path, document, headers = workflow; save_closed(client, path, document, headers, [form()])
    requested = close(client, path, headers); result = submit(client, path, headers, requested).json()
    assert result['variables']['feedback_rating'] == 4 and result['variables']['feedback_comment'] is None


def test_rating_only_and_both_disabled_forms(client, workflow):
    path, document, headers = workflow; document = save_closed(client, path, document, headers, [form(True, False)])
    requested = close(client, path, headers); assert requested['feedback']['fields'] == []
    assert submit(client, path, headers, requested).json()['variables']['feedback_rating'] == 4
    save_closed(client, path, document, headers, [form(False, False), variable('no_form', 'True')])
    result = close(client, path, headers); assert result['feedback'] is None and result['variables']['no_form']


@pytest.mark.parametrize('channel,typed,buttons', [('webchat',False,[]),('messaging',False,[1,2,3,4,5]),('bitrix24',True,[]),('africas_talking',True,[])])
def test_channel_preview_persists_and_exposes_appropriate_rating_controls(client, workflow, channel, typed, buttons):
    path, document, headers = workflow; save_closed(client, path, document, headers, [form(True,False)])
    start = event(client, path, headers, channel=channel).json(); requested = close(client, path, headers, start['conversation_id'])
    assert requested['channel'] == channel and requested['feedback']['channel'] == channel
    assert requested['feedback']['typed_rating'] == typed and requested['feedback']['rating_buttons'] == buttons
    assert requested['feedback']['request_message'] == 'How would you rate your conversation?'
    assert submit(client, path, headers, requested).status_code == 200
    assert event(client, path, headers, 'chat_closed', start['conversation_id'], channel='messaging' if channel!='messaging' else 'webchat').status_code == 409


def test_localisation_covers_rating_request_questions_placeholders_choices_and_confirmation(client, workflow):
    path, document, headers = workflow; agent = client.get(path).json()
    client.post(path+'/languages',headers=headers,json={'expected_revision':agent['revision'],'languages':['en','fr'],'default_language':'en'})
    item = field('choice',True,choices=['Yes','No']); action = form(fields=[item]); key=item[0]['id']
    action['config']['translations']['fr'] = {'webchat_rating_title':'Comment avons-nous fait ?', 'rating_request_message':'Évaluez votre conversation',
        'submission_text':'Merci ! Note : {feedback_rating}. Réponse : {feedback_comment}', 'fields':{key:{'question':'Recommandez-vous notre service ?', 'placeholder':'Choisissez une réponse', 'choices':['Oui','Non']}}}
    save_closed(client,path,document,headers,[action]); start=event(client,path,headers,language='fr').json(); requested=close(client,path,headers,start['conversation_id'])
    assert requested['feedback']['title']=='Comment avons-nous fait ?' and requested['feedback']['request_message']=='Évaluez votre conversation'
    question=requested['feedback']['fields'][0]; assert question['question']=='Recommandez-vous notre service ?' and question['choices']==['Oui','Non']
    assert question['placeholder']=='Choisissez une réponse'
    result=submit(client,path,headers,requested,5,{key:'Oui'}).json(); assert result['feedback_confirmation']=='Merci ! Note : 5. Réponse : Oui'


def test_missing_question_translation_falls_back_without_changing_field_identity(client,workflow):
    path,document,headers=workflow;agent=client.get(path).json()
    client.post(path+'/languages',headers=headers,json={'expected_revision':agent['revision'],'languages':['en','fr'],'default_language':'en'})
    action=form(); action['config']['translations']['fr']={**action['config']['translations']['en'],'webchat_rating_title':'Votre avis','fields':{}}
    save_closed(client,path,document,headers,[action]);start=event(client,path,headers,language='fr').json();requested=close(client,path,headers,start['conversation_id'])
    assert requested['feedback']['title']=='Votre avis' and requested['feedback']['fields'][0]['question']=='Any other comments?'
    assert requested['feedback']['fields'][0]['id']==action['config']['fields'][0]['id']


def test_send_api_after_feedback_receives_submitted_values_once(client,workflow,app):
    path,document,headers=workflow;fake=StubAPI();app.state.send_api_client=fake;action=form();key=action['config']['fields'][0]['id']
    request=api(method='POST',url='https://api.example.test/feedback',payload_mode='expression',payload='{"rating": feedback_rating, "comment": feedback_comment}')
    save_closed(client,path,document,headers,[action,request]);requested=close(client,path,headers);assert fake.calls==[]
    result=submit(client,path,headers,requested,2,{key:'Please improve response time.'});assert result.status_code==200
    assert json.loads(fake.calls[0]['body'])=={'rating':2,'comment':'Please improve response time.'}
    assert submit(client,path,headers,requested,1).status_code==409 and len(fake.calls)==1


def test_jump_continuation_retains_enclosing_paths_after_feedback(client,workflow):
    path,document,headers=workflow;action=form(True,False);shared=intent('shared audit',[variable('before','True'),action,variable('after','feedback_rating')])
    # Collect Feedback itself stays physically under Chat Closed; Jump reuses the later audit only.
    action['children']=[jump(shared['children'][2])];shared['children'].remove(action);document['nodes'].append(shared)
    save_closed(client,path,document,headers,[branch(('True',[action])),variable('abandoned','True')]);requested=close(client,path,headers)
    assert 'after' not in requested['variables'];result=submit(client,path,headers,requested,3).json()
    assert result['variables']['after']==3 and 'before' not in result['variables'] and 'abandoned' not in result['variables']


def test_followup_failure_keeps_rating_comment_and_consumes_request(client,workflow,app):
    path,document,headers=workflow;action=form();key=action['config']['fields'][0]['id']
    save_closed(client,path,document,headers,[action,variable('temporary','True'),variable('broken','missing_feedback_value')]);requested=close(client,path,headers)
    result=submit(client,path,headers,requested,3,{key:'Saved even when logging fails'}).json()
    assert result['closed'] and result['feedback_submitted'] and result['automation_error']
    assert result['variables']['feedback_rating']==3 and result['variables']['feedback_comment']=='Saved even when logging fails'
    assert 'temporary' not in result['variables'] and result['feedback'] is None
    assert submit(client,path,headers,requested).status_code==409


def test_workflow_changes_preserve_issued_questions_and_answers_but_block_stale_continuation(client,workflow):
    path,document,headers=workflow;action=form();key=action['config']['fields'][0]['id']
    document=save_closed(client,path,document,headers,[action,variable('old_continuation','True')]);requested=close(client,path,headers)
    closed_root(document)['children']=[form(True,False),variable('new_continuation','True')];assert save(client,path,document,headers).status_code==200
    result=submit(client,path,headers,requested,4,{key:'Answer to the original question'}).json()
    assert result['feedback_submitted'] and result['variables']['feedback_comment']=='Answer to the original question'
    assert result['automation_error'] and 'old_continuation' not in result['variables'] and 'new_continuation' not in result['variables']


@pytest.mark.parametrize('answers', [{'foreign':'no'}, {'__uuid__':'x'*4001}, {'__uuid__':{'nested':'bad'}}, {'__uuid__':True}, {'__foreign_uuid__':'unknown'}])
def test_unknown_or_oversized_answer_payloads_are_rejected(client,workflow,answers):
    path,document,headers=workflow;action=form();key=action['config']['fields'][0]['id'];save_closed(client,path,document,headers,[action]);requested=close(client,path,headers)
    values={key if name=='__uuid__' else str(uuid.uuid4()) if name=='__foreign_uuid__' else name:value for name,value in answers.items()}
    assert submit(client,path,headers,requested,4,values).status_code==422
    assert submit(client,path,headers,requested,4,{key:'valid'}).status_code==200


def test_unrequested_rating_is_rejected_for_survey_only_form(client,workflow):
    path,document,headers=workflow;save_closed(client,path,document,headers,[form(False,True)]);requested=close(client,path,headers)
    assert submit(client,path,headers,requested,4).status_code==422
    assert submit(client,path,headers,requested,None).status_code==200


def test_acceptance_checks_variable_capacity_before_consuming_feedback(client,workflow,app):
    path,document,headers=workflow;save_closed(client,path,document,headers,[form()]);requested=close(client,path,headers)
    with app.state.session_factory() as db:
        conversation=db.get(AgentConversation,uuid.UUID(requested['conversation_id']));state=deepcopy(conversation.workflow_state)
        state['variables']={'v'+str(i):i for i in range(100)};conversation.workflow_state=state;db.commit()
    assert submit(client,path,headers,requested).status_code==422
    with app.state.session_factory() as db:
        state=db.get(AgentConversation,uuid.UUID(requested['conversation_id'])).workflow_state
        assert not state.get('feedback_submitted') and state['feedback_request']['token']==requested['feedback']['token']


def test_existing_version_012_pending_rating_survives_without_replaying_old_actions(client,workflow,app):
    path,document,headers=workflow;save_closed(client,path,document,headers,[form(True,False),variable('should_not_replay','True')]);requested=close(client,path,headers)
    with app.state.session_factory() as db:
        conversation=db.get(AgentConversation,uuid.UUID(requested['conversation_id']));state=deepcopy(conversation.workflow_state)
        state['feedback_request']={key:value for key,value in state['feedback_request'].items() if key in {'token','title','node_id','node_number','language'}}
        state['variables']['old_actions_already_ran']=True;state['close_automation']={'status':'completed'};conversation.workflow_state=state;db.commit()
    result=submit(client,path,headers,requested,5).json()
    assert result['variables']['feedback_rating']==5 and result['variables']['old_actions_already_ran']
    assert 'should_not_replay' not in result['variables'] and result['feedback_confirmation']==DEFAULT_SUBMISSION


def test_custom_rating_variable_is_available_to_following_actions(client,workflow):
    path,document,headers=workflow;action=form(True,False,rating_variable_name='service_rating')
    save_closed(client,path,document,headers,[action,variable('rating_copy','service_rating')]);requested=close(client,path,headers)
    result=submit(client,path,headers,requested,2).json()
    assert result['variables']=={'service_rating':2,'rating_copy':2} and result['csat_rating']==2


def test_feedback_field_count_is_bounded_without_changing_existing_fields():
    value=form(fields=[field(name='reply_'+str(i)) for i in range(50)])['config']
    assert len(CollectFeedbackConfig.model_validate(value).fields)==50
    extra=field(name='one_more');value['fields'].append(extra[0]);value['translations']['en']['fields'][extra[0]['id']]=extra[1]
    with pytest.raises(ValidationError):CollectFeedbackConfig.model_validate(value)


@pytest.mark.parametrize('translations', [['invalid'], 'invalid', 3])
def test_malformed_legacy_translation_shapes_raise_validation_errors(translations):
    with pytest.raises(ValidationError): CollectFeedbackConfig.model_validate({'translations':translations})


def test_malformed_legacy_stored_feedback_fails_cleanly_after_closure(client,workflow,app):
    path,document,headers=workflow;closed_root(document)['children']=[form()]
    closed_root(document)['children'][0]['config']['translations']['en']['submission_text']=''
    with app.state.session_factory() as db:
        stored=db.get(WorkflowDocument,uuid.UUID(document['agent_id']));stored.nodes=document['nodes'];db.commit()
    result=close(client,path,headers)
    assert result['closed'] and result['automation_error'] and result['feedback'] is None and result['messages']==[]
