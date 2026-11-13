"""Branch configuration, ordered decisions, continuations, and API transactions."""
from copy import deepcopy
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.models import AgentConversation, AgentMessage
from app.workflows.actions.branch.config import BranchConfig
from app.workflows.numbering import assign_numbers, walk
from app.workflows.service import validate_nodes
from test_ask_survey import field, survey
from test_jump import jump, prepare, run, setup_tree, texts
from test_workflows import contents, event, intent, message, node, save, workflow


def branch(*cases, otherwise=None, **kwargs):
    conditions, paths = [], []
    for index, case in enumerate(cases):
        path_id = str(uuid.uuid4())
        conditions.append({"id": path_id, "name": case[2] if len(case) > 2 else "Condition " + str(index + 1), "chat_script": case[0]})
        paths.append({"id": path_id, "children": case[1]})
    else_id = str(uuid.uuid4())
    paths.append({"id": else_id, "children": otherwise or []})
    result = node("action", "branch", config={"conditions": conditions, "else_path_id": else_id}, **kwargs)
    result["paths"] = paths
    return result


def variable(name, script):
    return node("action", "set_variable", config={"variable_name": name, "chat_script": script})


@pytest.mark.parametrize("mutation", ["empty", "eleven", "blank_name", "long_name", "blank_script", "long_script",
    "assignment", "javascript", "private", "import", "unknown_function", "duplicate_id", "else_collision", "bad_id", "extra"])
def test_invalid_branch_settings_are_rejected(mutation):
    config = branch(('True', []))["config"]
    condition = config["conditions"][0]
    if mutation == "empty": config["conditions"] = []
    elif mutation == "eleven": config["conditions"] = [dict(condition, id=str(uuid.uuid4())) for _ in range(11)]
    elif mutation == "blank_name": condition["name"] = " "
    elif mutation == "long_name": condition["name"] = "a" * 101
    elif mutation == "blank_script": condition["chat_script"] = " "
    elif mutation == "long_script": condition["chat_script"] = "x" * 2001
    elif mutation == "assignment": condition["chat_script"] = "status = 'resolved'"
    elif mutation == "javascript": condition["chat_script"] = 'score > 5 && active'
    elif mutation == "private": condition["chat_script"] = 'status.__class__'
    elif mutation == "import": condition["chat_script"] = '__import__("os")'
    elif mutation == "unknown_function": condition["chat_script"] = 'status.execute()'
    elif mutation == "duplicate_id": config["conditions"].append(deepcopy(condition))
    elif mutation == "else_collision": config["else_path_id"] = condition["id"]
    elif mutation == "bad_id": condition["id"] = "#12"
    else: condition["unknown"] = True
    with pytest.raises(ValidationError): BranchConfig.model_validate(config)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "foreign", "children", "normal_paths", "node_collision", "path_collision", "duplicate_node", "invalid_path_action", "nested_system", "duplicate_fallback", "action_after_trigger"])
def test_branch_paths_follow_tree_integrity_rules(mutation):
    agent, document = setup_tree()
    choice = branch(('True', [message("If")]), otherwise=[message("Else")])
    document.nodes[0]["children"] = [choice]
    if mutation == "missing": choice["paths"].pop()
    elif mutation == "extra": choice["paths"].append({"id": str(uuid.uuid4()), "children": []})
    elif mutation == "duplicate": choice["paths"][1] = deepcopy(choice["paths"][0])
    elif mutation == "foreign": choice["paths"][0]["id"] = str(uuid.uuid4())
    elif mutation == "children": choice["children"] = [message("Ambiguous")]
    elif mutation == "normal_paths": document.nodes[0]["paths"] = choice["paths"]
    elif mutation == "node_collision": choice["paths"][0]["children"][0]["id"] = choice["paths"][0]["id"]
    elif mutation == "path_collision":
        choice["config"]["conditions"][0]["id"] = choice["id"]
        choice["paths"][0]["id"] = choice["id"]
    elif mutation == "duplicate_node": choice["paths"][1]["children"] = deepcopy(choice["paths"][0]["children"])
    elif mutation == "invalid_path_action": choice["paths"][1]["children"] = [node("action", "send_api")]
    elif mutation == "nested_system": choice["paths"][1]["children"] = [document.nodes.pop(1)]
    elif mutation == "duplicate_fallback": choice["paths"][0]["children"] = [node("trigger", "fallback"), node("trigger", "fallback")]
    else: choice["paths"][0]["children"] = [intent("yes"), message("Wrong order")]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert error.value.status_code == 422


def test_ten_conditions_are_allowed_and_tenth_path_can_match():
    agent, document = setup_tree()
    choice = branch(*[(str(index == 9), [message(str(index))]) for index in range(10)], otherwise=[message("Else")])
    document.nodes[0]["children"] = [choice]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ["9"]
    assert result[2][1]["condition_name"] == "Condition 10"


@pytest.mark.parametrize("conditions,expected", [
    (['True', 'True', 'missing'], 'First'), (['False', 'True', 'missing'], 'Second'),
    (['False', 'False', 'True'], 'Third'), (['False', 'False', 'False'], 'Else'),
    (['0', '[]', '"ready"'], 'Third')])
def test_first_truthy_condition_runs_only_its_path(conditions, expected):
    agent, document = setup_tree()
    choice = branch(*[(expression, [message(name)], name) for expression, name in zip(conditions, ['First', 'Second', 'Third'])],
        otherwise=[message("Else")])
    document.nodes[0]["children"] = [message("Before"), choice, message("After")]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ["Before", expected, "After"]
    selected = next(item for item in result[2] if item["type"] == "branch")
    assert selected["outcome"] == "branched" and selected["condition_name"] == expected
    assert not result[0]["stack"]


def test_empty_selected_path_and_disabled_branch_continue_without_other_paths():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [branch(('True', []), otherwise=[message("Wrong")]),
        branch(('missing', [message("Disabled")]), otherwise=[message("Wrong")], enabled=False), message("After")]
    prepare(agent, document)
    assert texts(run(agent, document)) == ["After"]


def test_nested_branch_runs_in_order_and_keeps_enclosing_continuations():
    agent, document = setup_tree()
    nested = branch(('False', [message("Wrong")]), otherwise=[message("Inner Else")])
    outer = branch(('True', [message("Outer If"), nested, message("Outer end")]), otherwise=[message("Wrong")])
    document.nodes[0]["children"] = [outer, message("After all")]
    prepare(agent, document)
    assert texts(run(agent, document)) == ["Outer If", "Inner Else", "Outer end", "After all"]


@pytest.mark.parametrize("status,score,expected", [("resolved", "2", "Resolved"), ("open", "9", "Priority"), ("open", "2", "Else")])
def test_survey_text_and_number_views_route_without_changing_stored_values(status, score, expected):
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field("complaint_status"), field("score", "number")),
        branch(('complaint_status.text == "resolved"', [message("Resolved")]),
            ('score.norm.value >= 5', [message("Priority")]), otherwise=[message("Else")]),
        message("{complaint_status}/{score}")]
    prepare(agent, document)
    first = run(agent, document)
    second = run(agent, document, first[0], "message_received", status)
    result = run(agent, document, second[0], "message_received", score)
    assert texts(result) == [expected, status + '/' + score]
    assert result[0]["variables"] == {"complaint_status": status, "score": int(score)}


def test_set_variable_direct_names_and_json_fields_work_in_branch():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [variable('status', '"active"'), variable('api_response', '{"customer": {"tier": "gold"}}'),
        branch(('status == "active" and api_response.customer.tier == "gold"', [message("Gold")]), otherwise=[message("Else")])]
    prepare(agent, document)
    assert texts(run(agent, document)) == ["Gold"]


def test_skipped_survey_answer_can_be_guarded_before_accessing_its_value():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field("score", "number", enable_skip=True)),
        branch(('score is not None and score.norm.value >= 5', [message("High")]), otherwise=[message("Skipped or low")])]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(run(agent, document, first[0], "message_received", "skip")) == ["Skipped or low"]


@pytest.mark.parametrize("expression", ['missing.text == "resolved"', '1 / 0', 'score.norm.value > 5'])
def test_condition_errors_fail_the_turn_instead_of_silently_using_else(expression):
    agent, document = setup_tree()
    document.nodes[0]["children"] = [variable('score', '"DO_NOT_ECHO_THIS_VALUE"'),
        branch((expression, [message("If")], "Resolution check"), otherwise=[message("Else")], name="Complaint routing")]
    prepare(agent, document)
    previous = {"variables": {"score": 1}, "workflow_revision": 1, "stack": []}
    expected = deepcopy(previous)
    with pytest.raises(HTTPException) as error: run(agent, document, previous)
    assert error.value.status_code == 422
    assert "Complaint routing" in error.value.detail and "Resolution check" in error.value.detail
    assert "DO_NOT_ECHO_THIS_VALUE" not in error.value.detail and previous == expected


def test_survey_inside_selected_path_resumes_that_path_then_outer_siblings():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [branch(('True', [survey(field('name')), message("Path: {name}")]),
        otherwise=[message("Wrong path")]), message("After branch")]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(first) == ["Question for name"]
    result = run(agent, document, first[0], "message_received", "Chris")
    assert texts(result) == ["Path: Chris", "After branch"] and not result[0]["stack"]


def test_nested_triggers_wait_and_resume_inside_selected_path_only():
    agent, document = setup_tree()
    choice = branch(('True', [message("Selected"), intent('yes', [message('Accepted')]),
        node('trigger', 'fallback', children=[message('Try again')])]), otherwise=[intent('yes', [message('Wrong path')])])
    document.nodes[0]["children"] = [choice, message("After branch")]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(first) == ["Selected"] and first[0]["stack"]
    result = run(agent, document, first[0], "message_received", "yes")
    assert texts(result) == ["Accepted", "After branch"] and not result[0]["stack"]


def test_each_path_has_independent_fallback_trigger_scope():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [branch(('False', [node('trigger', 'fallback', children=[message('Wrong')])]),
        otherwise=[node('trigger', 'fallback', children=[message('Default reply')])]), message('After')]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(run(agent, document, first[0], "message_received", "anything")) == ['Default reply', 'After']


def test_jump_into_unselected_path_bypasses_conditions_and_joins_destination_continuation():
    agent, document = setup_tree()
    target = message('Target', children=[message('Target child')])
    choice = branch(('missing', [message('Before target'), target, message('Path end')]), otherwise=[message('Other path')])
    document.nodes.append(intent('destination', [choice, message('Destination end')]))
    document.nodes[0]["children"] = [jump(target), message('Abandoned source')]
    prepare(agent, document)
    assert texts(run(agent, document)) == ['Target', 'Target child', 'Path end', 'Destination end']


def test_jump_to_branch_evaluates_it_and_jump_out_abandons_remaining_path():
    agent, document = setup_tree()
    menu = message('Menu')
    choice = branch(('True', [message('Selected'), jump(menu), message('Abandoned path')]), otherwise=[message('Other path')])
    document.nodes.append(intent('destination', [choice, message('Abandoned destination end')]))
    document.nodes.append(intent('menu', [menu]))
    document.nodes[0]["children"] = [jump(choice), message('Abandoned source')]
    prepare(agent, document)
    assert texts(run(agent, document)) == ['Selected', 'Menu']


def test_jump_to_trigger_in_a_branch_does_not_enter_sibling_alternatives():
    agent, document = setup_tree()
    target = intent('yes', [message('Accepted')])
    choice = branch(('False', [target, intent('no', [message('Wrong')])]), otherwise=[message('Wrong Else')])
    document.nodes.append(intent('destination', [choice, message('After')]))
    document.nodes[0]["children"] = [jump(target)]
    prepare(agent, document)
    assert texts(run(agent, document)) == ['Accepted', 'After']


@pytest.mark.parametrize('mutation', ['delete_path', 'disable_branch', 'path_as_target'])
def test_incoming_jump_references_are_checked_across_paths(mutation):
    agent, document = setup_tree()
    target = message('Target')
    choice = branch(('True', [target]), otherwise=[])
    transfer = jump(target)
    document.nodes[0]["children"] = [transfer, choice]
    prepare(agent, document)
    if mutation == 'delete_path':
        choice['config']['conditions'][0] = {'id': str(uuid.uuid4()), 'name': 'Replacement', 'chat_script': 'True'}
        choice['paths'][0] = {'id': choice['config']['conditions'][0]['id'], 'children': []}
    elif mutation == 'disable_branch': choice['enabled'] = False
    else: transfer['config']['target_node_id'] = choice['paths'][0]['id']
    with pytest.raises(HTTPException) as error: validate_nodes(document.nodes, agent.id)
    assert 'Jump' in error.value.detail


def test_numbering_covers_all_paths_and_reordering_keeps_numbers_and_actions():
    agent, document = setup_tree()
    choice = branch(('False', [message('First')]), ('True', [message('Second')]), otherwise=[message('Else')])
    document.nodes[0]['children'] = [choice]
    prepare(agent, document)
    original = deepcopy(document.nodes)
    numbers = {value['id']: value['number'] for value in walk(original)}
    choice['config']['conditions'].reverse()
    choice['paths'].reverse()
    validate_nodes(document.nodes, agent.id)
    assign_numbers(document.nodes, original, 100)
    assert {value['id']: value['number'] for value in walk(document.nodes)} == numbers
    assert choice['paths'][0]['id'] == choice['config']['conditions'][0]['id']
    assert texts(run(agent, document)) == ['Second']


def test_branch_depth_and_node_limits_apply_inside_paths():
    agent, document = setup_tree()
    root = message('Leaf')
    for _ in range(13): root = branch(('True', [root]))
    document.nodes[0]['children'] = [root]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert '12 nested' in error.value.detail
    document.nodes[0]['children'] = [branch(('True', [message(str(i)) for i in range(100)])) for _ in range(5)]
    with pytest.raises(HTTPException) as error: prepare(agent, document)
    assert '500 workflow nodes' in error.value.detail


def test_branch_api_save_roundtrip_survey_routing_trace_and_noop_revision(client, workflow):
    path, document, csrf = workflow
    choice = branch(('complaint_status.text == "resolved"', [message('Resolved')], 'Resolved complaint'), otherwise=[message('Needs follow-up')])
    document['nodes'][0]['children'] = [survey(field('complaint_status')), choice, message('Done')]
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    document = response.json()
    assert document['revision'] == 2 and len(document['nodes'][0]['children'][1]['paths']) == 2
    assert save(client, path, document, csrf).json()['revision'] == 2
    first = event(client, path, csrf)
    result = event(client, path, csrf, 'message_received', first.json()['conversation_id'], content='resolved',
        survey_answer_token=first.json()['survey']['answer_token'])
    assert contents(result) == ['Resolved', 'Done']
    trace = next(item for item in result.json()['trace'] if item['type'] == 'branch')
    assert trace['condition_name'] == 'Resolved complaint' and trace['path_id'] == choice['config']['conditions'][0]['id']
    assert trace['node_number'] and result.json()['variables'] == {'complaint_status': 'resolved'}


def test_bad_branch_save_does_not_mutate_revision_or_number_counter(client, workflow, app):
    path, document, csrf = workflow
    original = deepcopy(document)
    document['nodes'][0]['children'] = [branch(('status == "open"', [message('Open')]))]
    document['nodes'][0]['children'][0]['paths'].pop()
    assert save(client, path, document, csrf).status_code == 422
    current = client.get(path + '/workflows').json()
    assert current['revision'] == original['revision'] and current['nodes'] == original['nodes']
    document['nodes'][0]['children'] = [branch(('True', [message('If')]))]
    current = save(client, path, document, csrf).json()
    assert current['nodes'][0]['children'][0]['number'] == 7
    assert current['nodes'][0]['children'][0]['paths'][0]['children'][0]['number'] == 8


def test_branch_error_rolls_back_survey_answer_messages_and_variables(client, workflow, app):
    path, document, csrf = workflow
    document['nodes'][0]['children'] = [survey(field('name')), message('Never committed'), variable('attempts', '1'),
        branch(('missing == 1', [message('If')]), otherwise=[message('Else')])]
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf).json()
    conversation = first['conversation_id']
    response = event(client, path, csrf, 'message_received', conversation, content='Chris',
        survey_answer_token=first['survey']['answer_token'])
    assert response.status_code == 422 and "Variable 'missing' is not set" in response.json()['detail']
    with app.state.session_factory() as db:
        stored = db.get(AgentConversation, uuid.UUID(conversation))
        assert stored.workflow_state.get('variables', {}) == {}
        assert stored.workflow_state['survey']['answer_token'] == first['survey']['answer_token']
        assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.conversation_id == stored.id)) == 1


def test_revision_change_discards_a_waiting_selected_path(client, workflow):
    path, document, csrf = workflow
    document['nodes'][0]['children'] = [branch(('True', [intent('yes', [message('Old path')])]))]
    document['nodes'].append(intent('yes', [message('Root path')]))
    document = save(client, path, document, csrf).json()
    first = event(client, path, csrf).json()
    document['nodes'][0]['children'][0]['config']['conditions'][0]['name'] = 'Renamed'
    assert save(client, path, document, csrf).status_code == 200
    response = event(client, path, csrf, 'message_received', first['conversation_id'], content='yes')
    assert contents(response) == ['Root path']
