import uuid

import pytest
from sqlalchemy import func, select

from app.models import AgentConversation, AgentMessage
from test_workflows import workflow, node, message, intent, save, event, contents


def variable(name, script, **kwargs):
    return node("action", "set_variable", name="Set " + name,
        config={"variable_name": name, "chat_script": script}, **kwargs)


def test_complaint_reference_example_executes_in_order_and_is_persisted(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [
        variable("financial_institution", '"PetrixBank"'), variable("ticket_number", '"12345"'),
        variable("complaint_ref", 'financial_institution + "-" + ticket_number'),
        message("Your complaint reference is {complaint_ref}.")]
    assert save(client, path, document, csrf).status_code == 200
    response = event(client, path, csrf)
    assert contents(response) == ["Your complaint reference is PetrixBank-12345."]
    result = response.json()
    assert result["variables"]["complaint_ref"] == "PetrixBank-12345"
    assert [step["type"] for step in result["trace"]] == ["chat_started", "set_variable", "set_variable", "set_variable", "send_message"]
    with app.state.session_factory() as db:
        conversation = db.get(AgentConversation, uuid.UUID(result["conversation_id"]))
        assert conversation.workflow_state["variables"]["ticket_number"] == "12345"
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 1


def test_data_only_action_creates_no_assistant_message(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("flag", 'True')]
    assert save(client, path, document, csrf).status_code == 200
    response = event(client, path, csrf)
    assert contents(response) == []
    assert response.json()["variables"] == {"flag": True} and response.json()["notice"] == ""
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 0


def test_variables_persist_across_turns_update_and_reset_per_conversation(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("attempts", '0')]
    document["nodes"].append(intent("again", [variable("attempts", 'attempts + 1'), message("Attempt {attempts}")]))
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf).json()
    first_id = first["conversation_id"]
    assert contents(event(client, path, csrf, "message_received", first_id, content="again")) == ["Attempt 1"]
    assert contents(event(client, path, csrf, "message_received", first_id, content="again")) == ["Attempt 2"]
    restarted = event(client, path, csrf).json()
    assert restarted["conversation_id"] != first_id and restarted["variables"] == {"attempts": 0}
    assert contents(event(client, path, csrf, "message_received", first_id, content="again")) == ["Attempt 3"]


def test_typed_values_computations_and_system_reads(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("amount", '1250'), variable("vat", 'round(amount * 0.16, 2)'),
        variable("approved", 'vat < 300'), variable("nullable", 'None'),
        variable("customer", '{"name": agent_name, "items": [1, 2]}'),
        variable("chat_ref", 'conversation_id'), message("{customer.name}: {vat}, {approved}")]
    assert save(client, path, document, csrf).status_code == 200
    response = event(client, path, csrf)
    assert contents(response) == ["Default AI Agent: 200.0, true"]
    result = response.json()
    assert result["variables"]["amount"] == 1250
    assert result["variables"]["approved"] is True and result["variables"]["nullable"] is None
    assert result["variables"]["chat_ref"] == result["conversation_id"]


def test_event_values_and_variables_survive_nested_waits_and_revisions(client, workflow):
    path, document, csrf = workflow
    document["nodes"].append(intent("hello", [variable("captured", 'message.upper()'),
        message("Continue", children=[intent("yes", [variable("confirmed", 'True'), message("{captured}")])])]))
    document = save(client, path, document, csrf).json()
    conversation = event(client, path, csrf).json()["conversation_id"]
    first = event(client, path, csrf, "message_received", conversation, content="hello")
    assert first.json()["waiting"] and first.json()["variables"]["captured"] == "HELLO"
    assert contents(event(client, path, csrf, "message_received", conversation, content="yes")) == ["HELLO"]
    document["nodes"].append(intent("show", [message("Still {captured}")]))
    assert save(client, path, document, csrf).status_code == 200
    assert contents(event(client, path, csrf, "message_received", conversation, content="show")) == ["Still HELLO"]


def test_language_system_variable_changes_following_actions_and_persists(client, workflow):
    path, document, csrf = workflow
    assert client.post(path + "/languages", json={"expected_revision": 1, "languages": ["en", "sw"], "default_language": "en"}, headers=csrf).status_code == 200
    reply = message("Hello")
    reply["config"]["translations"]["sw"] = {"messages": ["Karibu {language}"], "quick_replies": []}
    document["nodes"][0]["children"] = [variable("language", '"sw"'), reply]
    document["nodes"].append(intent("hello", [reply | {"id": str(uuid.uuid4())}]))
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf)
    assert contents(first) == ["Karibu sw"] and first.json()["language"] == "sw"
    assert "language" not in first.json()["variables"]
    assert contents(event(client, path, csrf, "message_received", first.json()["conversation_id"], content="hello")) == ["Karibu sw"]


@pytest.mark.parametrize("name,script", [
    ("1bad", '1'), ("_bad", '1'), ("bad-name", '1'), ("agent_name", '"override"'),
    ("class", '1'), ("str", '1'), ("ok", 'import os'), ("ok", 'open("file")'),
    ("ok", '__import__("os")'), ("ok", '"x".__class__'), ("ok", 'x = 1'),
])
def test_invalid_config_is_rejected_on_save_without_changing_the_definition(client, workflow, name, script):
    path, document, csrf = workflow
    document["nodes"][0]["children"].append(variable(name, script))
    response = save(client, path, document, csrf)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str) and "Step" in response.json()["detail"]
    assert client.get(path + "/workflows").json()["revision"] == 1


def test_failed_turn_rolls_back_previous_assignments_and_messages(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("attempts", '0')]
    document["nodes"].append(intent("fail", [variable("attempts", 'attempts + 1'), message("Never sent"), variable("bad", 'missing + 1')]))
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf).json()
    response = event(client, path, csrf, "message_received", first["conversation_id"], content="fail")
    assert response.status_code == 422 and "missing" in response.json()["detail"]
    with app.state.session_factory() as db:
        conversation = db.get(AgentConversation, uuid.UUID(first["conversation_id"]))
        assert conversation.workflow_state["variables"] == {"attempts": 0}
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 0


def test_failed_start_does_not_save_a_partial_conversation(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("token", '"DO_NOT_ECHO_THIS_VALUE"'), variable("bad", 'token / 0')]
    assert save(client, path, document, csrf).status_code == 200
    result = event(client, path, csrf)
    assert result.status_code == 422 and 'DO_NOT_ECHO_THIS_VALUE' not in result.text
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentConversation)) == 0


def test_disabled_variable_and_missing_message_placeholder_are_handled(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("name", '"ignored"', enabled=False)]
    assert save(client, path, document, csrf).status_code == 200
    first = event(client, path, csrf)
    assert contents(first) == [] and first.json()["variables"] == {}
    document = client.get(path + "/workflows").json()
    document["nodes"][0]["children"].append(message("Missing {name}"))
    assert save(client, path, document, csrf).status_code == 200
    assert event(client, path, csrf).status_code == 422


def test_quick_replies_can_use_variables_and_rendered_limits_are_checked(client, workflow):
    path, document, csrf = workflow
    reply = message("Choose {reference}")
    reply["config"]["translations"]["en"]["quick_replies"] = ["Track {reference}", "Help"]
    document["nodes"][0]["children"] = [variable("reference", '"REF-123"'), reply]
    document = save(client, path, document, csrf).json()
    first = event(client, path, csrf)
    assert first.json()["messages"][0]["quick_replies"] == ["Track REF-123", "Help"]
    document["nodes"][0]["children"][0]["config"]["chat_script"] = '"x" * 101'
    assert save(client, path, document, csrf).status_code == 200
    assert event(client, path, csrf).status_code == 422


def test_invalid_language_assignment_fails_without_committing(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [variable("language", '"fr"')]
    assert save(client, path, document, csrf).status_code == 200
    response = event(client, path, csrf)
    assert response.status_code == 422 and 'configured language' in response.json()["detail"]
