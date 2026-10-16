"""Jump control flow, reference integrity, and transactional API behavior."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.models import AgentConversation, AgentMessage
from app.workflows.actions.jump.config import JumpConfig
from app.workflows.actions.registry import ACTIONS
from app.workflows.defaults import default_nodes
from app.workflows.numbering import assign_numbers
from app.workflows.runtime import execute
from app.workflows.service import validate_nodes
from test_workflows import workflow, node, message, intent, save, event, contents


def jump(target, **kwargs):
    return node("action", "jump", config={"target_node_id": target["id"]}, **kwargs)


def setup_tree():
    agent = SimpleNamespace(id=uuid.uuid4(), name="Petrix", languages=["en", "sw"], default_language="en")
    document = SimpleNamespace(nodes=default_nodes(agent.id), revision=1)
    document.nodes[0]["children"] = []
    return agent, document


def prepare(agent, document):
    validate_nodes(document.nodes, agent.id)
    assign_numbers(document.nodes, [], 1)


def run(agent, document, state=None, event_type="chat_started", content=""):
    return execute(document, agent, state or {}, {"event": event_type, "content": content})


def texts(result):
    return [item["content"] for item in result[1]]


@pytest.mark.parametrize("config", [{}, {"target_node_id": ""}, {"target_node_id": "#12"},
    {"target_node_id": str(uuid.uuid4()), "target_number": 12}])
def test_jump_requires_a_uuid_target_and_rejects_unknown_settings(config):
    with pytest.raises(ValidationError):
        JumpConfig.model_validate(config)


def test_jump_catalogue_and_config_use_stable_uuid_identity():
    target = str(uuid.uuid4())
    assert ACTIONS["jump"]["available"]
    assert JumpConfig(target_node_id=target.upper()).model_dump(mode="json") == {"target_node_id": target}


@pytest.mark.parametrize("invalid", ["missing", "foreign_agent", "self", "disabled_target", "disabled_parent"])
def test_invalid_jump_targets_are_rejected(invalid):
    agent, document = setup_tree()
    target = message("Menu")
    owner = intent("menu", [target])
    transfer = jump(target)
    document.nodes[0]["children"] = [transfer]
    if invalid == "self":
        transfer["config"]["target_node_id"] = transfer["id"]
    elif invalid == "foreign_agent":
        transfer["config"]["target_node_id"] = default_nodes(uuid.uuid4())[0]["children"][0]["id"]
    elif invalid != "missing":
        document.nodes.append(owner)
        if invalid == "disabled_target": target["enabled"] = False
        if invalid == "disabled_parent": owner["enabled"] = False
    with pytest.raises(HTTPException) as error:
        validate_nodes(document.nodes, agent.id)
    assert error.value.status_code == 422 and "Jump" in error.value.detail


def test_disabled_jump_can_be_retained_with_a_disabled_target():
    agent, document = setup_tree()
    target = message("Menu", enabled=False)
    document.nodes[0]["children"] = [jump(target, enabled=False), target, message("Done")]
    prepare(agent, document)
    assert texts(run(agent, document)) == ["Done"]


def test_jump_to_action_skips_preceding_destination_actions_and_abandons_source():
    agent, document = setup_tree()
    target = message("Main menu", children=[message("Menu details")])
    document.nodes.append(intent("menu", [message("Do not replay"), target, message("Menu footer")]))
    transfer = jump(target, children=[message("Unreachable Jump child")])
    document.nodes[0]["children"] = [message("Complaint logged"), transfer, message("Abandoned")]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ["Complaint logged", "Main menu", "Menu details", "Menu footer"]
    assert not result[0]["stack"] and result[3]
    trace = next(step for step in result[2] if step["type"] == "jump")
    assert trace["outcome"] == "jumped" and trace["target_node_id"] == target["id"]
    assert trace["target_node_number"] == target["number"]
    assert all(item["source"] == "send_message" for item in result[1])


def test_jump_to_trigger_enters_its_actions_without_matching_the_current_event():
    agent, document = setup_tree()
    alternative = intent("other", [message("Wrong alternative")])
    target = intent("not the current input", [message("Selected branch")])
    container = message("Do not replay parent", children=[target, alternative])
    document.nodes.append(intent("container", [container, message("Destination continuation")]))
    document.nodes[0]["children"] = [jump(target), message("Abandoned")]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ["Selected branch", "Destination continuation"]
    assert next(step for step in result[2] if step["node_id"] == target["id"])["outcome"] == "jumped_to"
    assert not result[0]["stack"]


def test_jump_preserves_destination_waits_and_ancestor_continuations_only():
    agent, document = setup_tree()
    target = message("Choose yes", children=[intent("yes", [message("Confirmed")])])
    wrapper = message("Do not replay wrapper", children=[target, message("Inner footer")])
    document.nodes.append(intent("menu", [wrapper, message("Outer footer")]))
    document.nodes[0]["children"] = [message("Source wrapper", children=[jump(target)]), message("Abandoned")]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(first) == ["Source wrapper", "Choose yes"] and first[0]["stack"]
    second = run(agent, document, first[0], "message_received", "yes")
    assert texts(second) == ["Confirmed", "Inner footer", "Outer footer"] and not second[0]["stack"]


def test_return_to_menu_can_repeat_after_waiting_for_new_input():
    agent, document = setup_tree()
    target = message("Main menu")
    target["children"] = [intent("complaint", [message("Complaint logged"), jump(target)])]
    document.nodes[0]["children"] = [target]
    prepare(agent, document)
    result = run(agent, document)
    for _ in range(3):
        result = run(agent, document, result[0], "message_received", "complaint")
        assert texts(result) == ["Complaint logged", "Main menu"] and result[0]["stack"]


def test_jump_chains_preserve_variables_and_do_not_create_control_messages():
    agent, document = setup_tree()
    target = message("Hello {name}")
    second = jump(target)
    first = jump(second)
    document.nodes.append(intent("menu", [second, target]))
    document.nodes[0]["children"] = [node("action", "set_variable", config={"variable_name": "name", "chat_script": '"Chris"'}), first]
    prepare(agent, document)
    result = run(agent, document)
    assert texts(result) == ["Hello Chris"] and result[0]["variables"] == {"name": "Chris"}
    assert len([step for step in result[2] if step["type"] == "jump"]) == 2


def test_forward_targets_survive_reordering_renaming_and_duplicate_names():
    agent, document = setup_tree()
    wrong = message("Wrong", name="Menu")
    target = message("Right", name="Menu")
    transfer = jump(target)
    document.nodes[0]["children"] = [transfer]
    root = intent("menu", [wrong, target])
    document.nodes.append(root)
    prepare(agent, document)
    original = deepcopy(document.nodes)
    old_number = target["number"]
    root["children"].reverse(); target["name"] = "Main menu"
    assign_numbers(document.nodes, original, 100)
    result = run(agent, document)
    assert texts(result) == ["Right", "Wrong"]
    assert result[2][1]["target_node_number"] == old_number


@pytest.mark.parametrize("corruption", ["missing", "disabled", "disabled_parent"])
def test_runtime_checks_targets_even_if_a_saved_document_is_corrupted(corruption):
    agent, document = setup_tree()
    target = message("Main menu")
    owner = intent("menu", [target])
    document.nodes.append(owner)
    transfer = jump(target)
    document.nodes[0]["children"] = [transfer]
    prepare(agent, document)
    if corruption == "missing": transfer["config"]["target_node_id"] = str(uuid.uuid4())
    elif corruption == "disabled": target["enabled"] = False
    else: owner["enabled"] = False
    with pytest.raises(HTTPException) as error:
        run(agent, document)
    assert error.value.status_code == 422 and "missing or disabled" in error.value.detail


def test_immediate_jump_cycle_fails_with_a_useful_error_and_does_not_mutate_state():
    agent, document = setup_tree()
    first, second = node("action", "jump"), node("action", "jump")
    first["config"] = {"target_node_id": second["id"]}
    second["config"] = {"target_node_id": first["id"]}
    document.nodes[0]["children"] = [node("action", "set_variable", config={"variable_name": "attempts", "chat_script": '2'}), first, second]
    prepare(agent, document)
    previous = {"variables": {"attempts": 1}, "workflow_revision": 1, "stack": []}
    expected = deepcopy(previous)
    with pytest.raises(HTTPException) as error:
        run(agent, document, previous)
    assert error.value.status_code == 422 and "Jump loop detected" in error.value.detail
    assert previous == expected


def test_target_deletion_is_blocked_until_incoming_jumps_are_removed():
    agent, document = setup_tree()
    target = message("Menu")
    document.nodes[0]["children"] = [jump(target), target]
    prepare(agent, document)
    document.nodes[0]["children"].pop()
    with pytest.raises(HTTPException) as error:
        validate_nodes(document.nodes, agent.id)
    assert "before deleting its target" in error.value.detail
    document.nodes[0]["children"] = []
    validate_nodes(document.nodes, agent.id)


def test_jump_api_saves_and_executes_a_forward_target_and_preserves_numbers(client, workflow):
    path, document, csrf = workflow
    menu = message("Main menu")
    menu["children"] = [intent("complaint", [message("Complaint logged"), jump(menu)])]
    document["nodes"][0]["children"] = [menu]
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    document = response.json()
    first = event(client, path, csrf)
    assert contents(first) == ["Main menu"]
    result = event(client, path, csrf, "message_received", first.json()["conversation_id"], content="complaint")
    assert contents(result) == ["Complaint logged", "Main menu"] and result.json()["waiting"]
    saved_menu = document["nodes"][0]["children"][0]
    saved_menu["name"] = "Renamed menu"
    number = saved_menu["number"]
    document = save(client, path, document, csrf).json()
    assert document["nodes"][0]["children"][0]["number"] == number
    assert client.get(path + "/workflows").json()["nodes"] == document["nodes"]


@pytest.mark.parametrize("mutation", ["delete", "disable", "foreign"])
def test_jump_api_rejects_invalid_references_without_changing_revision(client, workflow, mutation):
    path, document, csrf = workflow
    target = document["nodes"][0]["children"][0]
    document["nodes"].append(intent("menu", [jump(target)]))
    document = save(client, path, document, csrf).json()
    before = deepcopy(document)
    if mutation == "delete": document["nodes"][0]["children"] = []
    elif mutation == "disable": document["nodes"][0]["children"][0]["enabled"] = False
    else: document["nodes"][-1]["children"][0]["config"]["target_node_id"] = str(uuid.uuid4())
    assert save(client, path, document, csrf).status_code == 422
    assert client.get(path + "/workflows").json()["nodes"] == before["nodes"]
    assert client.get(path + "/workflows").json()["revision"] == before["revision"]


def test_jump_cycle_rolls_back_the_api_turn_messages_and_variables(client, workflow, app):
    path, document, csrf = workflow
    initial = node("action", "set_variable", config={"variable_name": "attempts", "chat_script": '0'})
    document["nodes"][0]["children"] = [initial]
    first, second = node("action", "jump"), node("action", "jump")
    first["config"] = {"target_node_id": second["id"]}
    second["config"] = {"target_node_id": first["id"]}
    increment = node("action", "set_variable", config={"variable_name": "attempts", "chat_script": 'attempts + 1'})
    document["nodes"].append(intent("loop", [increment, message("Never committed"), first, second]))
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    response = event(client, path, csrf, "message_received", conversation, content="loop")
    assert response.status_code == 422 and "Jump loop" in response.json()["detail"]
    with app.state.session_factory() as db:
        stored = db.get(AgentConversation, uuid.UUID(conversation))
        assert stored.workflow_state["variables"] == {"attempts": 0}
        assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.conversation_id == stored.id)) == 0
