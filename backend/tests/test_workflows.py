import copy
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.models import AgentChange, AgentConversation, AgentMessage, Membership, WorkflowDocument


@pytest.fixture
def workflow(client, signed_in, csrf):
    workspace_id = signed_in["workspace"]["id"]
    assert client.post("/api/workspaces/{}/setup".format(workspace_id), data={"name": "Workflow Lab", "country_code": "KE"}, headers=csrf).status_code == 200
    base = "/api/workspaces/{}/agents".format(workspace_id)
    agent = client.get(base).json()["agents"][0]
    path = base + "/" + agent["id"]
    return path, client.get(path + "/workflows").json(), csrf


def node(kind, type, name=None, config=None, children=None, enabled=True):
    return {"id": str(uuid.uuid4()), "kind": kind, "type": type, "name": name or type,
        "description": "", "enabled": enabled, "config": config or {}, "children": children or []}


def message(content, **kwargs):
    return node("action", "send_message", config={"translations": {"en": {"messages": [content], "quick_replies": []}}}, **kwargs)


def intent(example, children=None, **kwargs):
    return node("trigger", "message_received", config={"examples": {"en": [example]}, "match_mode": "exact", "threshold": 0.65}, children=children, **kwargs)


def save(client, path, document, csrf):
    return client.post(path + "/workflows", json={"expected_revision": document["revision"], "nodes": document["nodes"]}, headers=csrf)


def event(client, path, csrf, type="chat_started", conversation=None, **kwargs):
    return client.post(path + "/tester/events", json={"event": type, "conversation_id": conversation, **kwargs}, headers=csrf)


def contents(response):
    assert response.status_code == 200, response.text
    return [item["content"] for item in response.json()["messages"]]


def test_defaults_are_saved_once_and_catalogue_is_explicit(client, workflow, app):
    path, document, csrf = workflow
    assert [root["type"] for root in document["nodes"]] == ["chat_started", "chat_closed", "chat_timeout", "ai_timeout", "fallback"]
    assert document["revision"] == 1
    assert len(document["actions"]) == 20
    assert [item["type"] for item in document["actions"] if item["available"]] == ["send_message", "send_attachment", "show_carousel", "ask_survey", "collect_feedback", "jump", "branch", "set_variable", "switch_language", "close_chat", "send_api", "llm_processing", "sql_query"]
    greeting = document["nodes"][0]["children"][0]
    assert "{_chatbot.name}" in greeting["config"]["translations"]["en"]["messages"][0]
    assert contents(event(client, path, csrf)) == ["Hi! I’m Default AI Agent. How can I assist you today?"]
    assert save(client, path, document, csrf).json()["revision"] == 1
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowDocument)) == 1


def test_greeting_can_be_changed_disabled_and_deleted_without_reseeding(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"] = [message("Welcome to {_chatbot.name}")]
    document = save(client, path, document, csrf).json()
    renamed = client.post(path + "/identity", data={"name": "Petrix Banking", "expected_revision": 1, "remove_avatar": "false"}, headers=csrf)
    assert renamed.status_code == 200
    assert contents(event(client, path, csrf)) == ["Welcome to Petrix Banking"]
    document["nodes"][0]["children"][0]["enabled"] = False
    document = save(client, path, document, csrf).json()
    assert contents(event(client, path, csrf)) == []
    document["nodes"][0]["children"] = []
    document = save(client, path, document, csrf).json()
    assert client.get(path + "/workflows").json()["nodes"][0]["children"] == []
    assert contents(event(client, path, csrf)) == []


def test_workflow_revision_is_atomic_independent_of_settings_and_audited(client, workflow, app):
    path, document, csrf = workflow
    stale = copy.deepcopy(document)
    document["nodes"][0]["children"] = [message("Saved")]
    assert save(client, path, document, csrf).json()["revision"] == 2
    assert save(client, path, stale, csrf).status_code == 409
    assert client.get(path).json()["revision"] == 1
    with app.state.session_factory() as db:
        audit = db.scalars(select(AgentChange).where(AgentChange.action == "workflows_saved")).all()
        assert len(audit) == 1 and "revision 2" in audit[0].summary


@pytest.mark.parametrize("change", ["delete_system", "disable_system", "nest_system", "replace_system_id", "duplicate_id", "duplicate_fallback", "unsupported_action", "action_root", "action_after_trigger", "invalid_quick_reply", "unknown_config"])
def test_invalid_trees_are_rejected_without_mutation(client, workflow, change):
    path, document, csrf = workflow
    start = document["nodes"][0]
    if change == "delete_system": document["nodes"].pop(1)
    elif change == "disable_system": start["enabled"] = False
    elif change == "nest_system": start["children"].append(document["nodes"].pop(1))
    elif change == "replace_system_id": start["id"] = str(uuid.uuid4())
    elif change == "duplicate_id": start["children"].append(copy.deepcopy(start["children"][0]))
    elif change == "duplicate_fallback": document["nodes"].append(node("trigger", "fallback"))
    elif change == "unsupported_action": start["children"].append(node("action", "send_api", enabled=False))
    elif change == "action_root": document["nodes"].append(message("Wrong level"))
    elif change == "action_after_trigger": start["children"] = [intent("a"), message("Wrong order")]
    elif change == "invalid_quick_reply": start["children"][0]["config"]["translations"]["en"]["quick_replies"] = ["a"] * 14
    elif change == "unknown_config": start["children"][0]["config"]["script"] = "forbidden"
    assert save(client, path, document, csrf).status_code == 422
    assert client.get(path + "/workflows").json()["revision"] == 1


def test_tree_depth_and_size_are_bounded(client, workflow):
    path, document, csrf = workflow
    root = document["nodes"][0]
    for _ in range(14):
        child = message("Nested"); root["children"] = [child]; root = child
    assert save(client, path, document, csrf).status_code == 422
    document = client.get(path + "/workflows").json()
    document["nodes"][0]["children"][0]["config"]["translations"]["en"]["messages"] = ["x" * 4001]
    assert save(client, path, document, csrf).status_code == 422


def test_actions_execute_in_top_middle_end_order_and_disabled_subtrees_are_skipped(client, workflow):
    path, document, csrf = workflow
    start = document["nodes"][0]
    start["children"] = [message("A"), message("C")]
    start["children"].insert(0, message("TOP"))
    start["children"].insert(2, message("B"))
    start["children"].append(message("END"))
    start["children"].insert(1, message("DISABLED", enabled=False, children=[message("ALSO DISABLED")]))
    assert save(client, path, document, csrf).status_code == 200
    assert contents(event(client, path, csrf)) == ["TOP", "A", "B", "C", "END"]
    # Reordering changes execution order without changing node IDs.
    document = client.get(path + "/workflows").json()
    children = document["nodes"][0]["children"]
    children[-1], children[0] = children[0], children[-1]
    assert save(client, path, document, csrf).status_code == 200
    assert contents(event(client, path, csrf))[0] == "END"


def test_nested_triggers_pause_and_resume_parent_continuation(client, workflow):
    path, document, csrf = workflow
    nested = intent("yes", [message("Confirmed")])
    nested_fallback = node("trigger", "fallback", children=[message("Please choose yes")])
    prompt = message("Do you agree?", children=[nested, nested_fallback])
    document["nodes"].append(intent("start", [prompt, message("Finished")]))
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    first = event(client, path, csrf, "message_received", conversation, content="start")
    assert contents(first) == ["Do you agree?"] and first.json()["waiting"]
    second = event(client, path, csrf, "message_received", conversation, content="yes")
    assert contents(second) == ["Confirmed", "Finished"] and not second.json()["waiting"]
    event(client, path, csrf, "message_received", conversation, content="start")
    fallback = event(client, path, csrf, "message_received", conversation, content="something else")
    assert contents(fallback) == ["Please choose yes", "Finished"]


def test_global_trigger_can_interrupt_a_nested_wait_without_local_fallback(client, workflow):
    path, document, csrf = workflow
    document["nodes"].extend([intent("start", [message("Choose", children=[intent("yes", [message("Yes")])]), message("Complete")]), intent("menu", [message("Main menu")])])
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    event(client, path, csrf, "message_received", conversation, content="start")
    assert contents(event(client, path, csrf, "message_received", conversation, content="menu")) == ["Main menu"]
    assert contents(event(client, path, csrf, "message_received", conversation, content="yes")) == []


def test_quick_replies_variants_and_language_fallback(client, workflow):
    path, document, csrf = workflow
    assert client.post(path + "/languages", json={"expected_revision": 1, "languages": ["en", "sw"], "default_language": "en"}, headers=csrf).status_code == 200
    config = document["nodes"][0]["children"][0]["config"]
    config["translations"]["en"] = {"messages": ["Hello", "Welcome"], "quick_replies": ["Balance", "Help"]}
    config["translations"]["sw"] = {"messages": ["Karibu {_chatbot.name}"], "quick_replies": ["Salio"]}
    document["nodes"].append(intent("Balance", [message("Balance workflow")]))
    assert save(client, path, document, csrf).status_code == 200
    result = event(client, path, csrf)
    assert contents(result)[0] in {"Hello", "Welcome"}
    assert result.json()["messages"][0]["quick_replies"] == ["Balance", "Help"]
    assert contents(event(client, path, csrf, "message_received", result.json()["conversation_id"], content="Balance")) == ["Balance workflow"]
    assert contents(event(client, path, csrf, language="sw")) == ["Karibu Default AI Agent"]
    # A missing translation falls back to the agent's default language.
    del config["translations"]["sw"]
    document = client.get(path + "/workflows").json()
    del document["nodes"][0]["children"][0]["config"]["translations"]["sw"]
    assert save(client, path, document, csrf).status_code == 200
    assert contents(event(client, path, csrf, language="sw"))[0] in {"Hello", "Welcome"}
    assert event(client, path, csrf, language="fr").status_code == 422


def test_approximate_matching_uses_best_candidate_and_root_fallback(client, workflow):
    path, document, csrf = workflow
    balance = intent("check my balance", [message("Balance")])
    balance["config"]["match_mode"] = "approximate"
    document["nodes"].append(balance)
    document["nodes"][-2]["enabled"] = True
    document["nodes"][-2]["children"] = [message("Fallback")]
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert contents(event(client, path, csrf, "message_received", conversation, content="check my balanc")) == ["Balance"]
    assert contents(event(client, path, csrf, "message_received", conversation, content="completely unrelated")) == ["Fallback"]


def test_attachment_events_filter_types_and_persist_metadata(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"].append(node("trigger", "attachment_received", config={"attachment_types": ["image"]}, children=[message("Image received")]))
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert contents(event(client, path, csrf, "attachment_received", conversation, attachment={"name": "id.png", "media_type": "image"})) == ["Image received"]
    assert contents(event(client, path, csrf, "attachment_received", conversation, attachment={"name": "sound.wav", "media_type": "audio"})) == []
    with app.state.session_factory() as db:
        item = db.scalar(select(AgentMessage).where(AgentMessage.content == "Attachment test: id.png"))
        assert item.message_data["attachment"]["media_type"] == "image"


def test_close_and_csat_events_follow_the_conversation_lifecycle(client, workflow, app):
    path, document, csrf = workflow
    document["nodes"][1]["children"] = [node("action", "collect_feedback", config={"rating_enabled": True,
        "translations": {"en": {"webchat_rating_title": "Please rate the conversation"}}})]
    document["nodes"].append(node("trigger", "collect_feedback", children=[message("Thank you for {{feedback.rating}} / 5")]))
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert event(client, path, csrf, "collect_feedback", conversation, rating=5).status_code == 409
    closed = event(client, path, csrf, "chat_closed", conversation)
    assert contents(closed) == [] and closed.json()["closed"]
    token = closed.json()["feedback"]["token"]
    assert event(client, path, csrf, "message_received", conversation, content="Hi").status_code == 409
    feedback = event(client, path, csrf, "collect_feedback", conversation, rating=5, feedback_token=token)
    assert contents(feedback) == ["Thank you for 5 / 5"]
    assert event(client, path, csrf, "collect_feedback", conversation, rating=4).status_code == 409
    with app.state.session_factory() as db:
        assert db.get(AgentConversation, uuid.UUID(conversation)).workflow_state["csat_rating"] == 5


def test_saved_workflow_changes_invalidate_a_waiting_context(client, workflow):
    path, document, csrf = workflow
    document["nodes"].append(intent("start", [message("Wait", children=[intent("yes", [message("OLD")])])]))
    document = save(client, path, document, csrf).json()
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert event(client, path, csrf, "message_received", conversation, content="start").json()["waiting"]
    document["nodes"].pop()
    document["nodes"].append(intent("yes", [message("NEW")]))
    assert save(client, path, document, csrf).status_code == 200
    assert contents(event(client, path, csrf, "message_received", conversation, content="yes")) == ["NEW"]


class FakeLLM:
    available = True
    def __init__(self): self.calls = []; self.fail = False
    def reply(self, agent, history, language=None):
        self.calls.append(history)
        if self.fail: raise HTTPException(503, "Provider unavailable")
        return "Generated answer"


def test_workflows_take_priority_over_llm_and_provider_failure_rolls_back(client, workflow, app):
    path, document, csrf = workflow
    llm = FakeLLM(); app.state.llm_tester = llm
    document["nodes"].append(intent("menu", [message("Saved menu")]))
    assert save(client, path, document, csrf).status_code == 200
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert contents(event(client, path, csrf, "message_received", conversation, content="menu")) == ["Saved menu"]
    assert llm.calls == []
    assert contents(event(client, path, csrf, "message_received", conversation, content="question")) == ["Generated answer"]
    assert llm.calls[0][-1] == {"role": "user", "content": "question"}
    llm.fail = True
    assert event(client, path, csrf, "message_received", conversation, content="fail").status_code == 503
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.content == "fail")) == 0


def test_generative_rate_limit_and_llm_disabled_are_respected(client, workflow, app):
    path, _, csrf = workflow
    llm = FakeLLM(); app.state.llm_tester = llm
    app.state.settings.llm_tester_requests_per_minute = 1
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert contents(event(client, path, csrf, "message_received", conversation, content="one")) == ["Generated answer"]
    assert event(client, path, csrf, "message_received", conversation, content="two").status_code == 429
    assert client.post(path + "/personalization", json={"expected_revision": 1, "persona": "Test", "llm_enabled": False}, headers=csrf).status_code == 200
    assert contents(event(client, path, csrf, "message_received", conversation, content="three")) == []
    assert len(llm.calls) == 1


def test_permissions_csrf_and_conversation_isolation(client, workflow, app, signed_in):
    path, document, csrf = workflow
    assert client.post(path + "/workflows", json={"expected_revision": 1, "nodes": document["nodes"]}).status_code == 403
    assert client.post(path + "/tester/events", json={"event": "chat_started"}).status_code == 403
    conversation = event(client, path, csrf).json()["conversation_id"]
    base = path.rsplit("/", 1)[0]
    second = client.post(base, data={"name": "Second"}, headers=csrf).json()
    assert event(client, base + "/" + second["id"], csrf, "message_received", conversation, content="Hi").status_code == 404
    foreign_path = path.replace(signed_in["workspace"]["id"], str(uuid.uuid4()))
    assert client.get(foreign_path + "/workflows").status_code == 404
    with app.state.session_factory() as db:
        member = db.scalar(select(Membership).where(Membership.user_id == uuid.UUID(signed_in["user"]["id"])))
        member.role = "member"; db.commit()
    assert client.get(path + "/workflows").status_code == 200
    assert save(client, path, document, csrf).status_code == 403
    assert event(client, path, csrf).status_code == 403
    client.cookies.clear()
    assert client.get(path + "/workflows").status_code == 401


def test_agent_deletion_removes_workflows_and_retains_test_history(client, workflow, app):
    path, _, csrf = workflow
    conversation = event(client, path, csrf).json()["conversation_id"]
    assert client.post(path + "/delete", json={"expected_revision": 1, "confirmation_name": "Default AI Agent"}, headers=csrf).status_code == 204
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowDocument)) == 0
        assert db.get(AgentConversation, uuid.UUID(conversation)).agent_id is None
        assert db.scalar(select(func.count()).select_from(AgentMessage)) == 1


def test_0003_backfills_existing_agents_and_keeps_customized_greetings(database_engine):
    schema = "petrix_workflow_upgrade_" + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with database_engine.connect() as connection:
        original = connection.execute(text("SELECT current_schema()")).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes["connection"] = connection
            with connection.begin(): command.upgrade(config, "0002")
            workspace_id, agent_id = uuid.uuid4(), uuid.uuid4()
            with connection.begin():
                connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {"id": workspace_id})
                connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing agent',false,'','[\"en\"]','en',false,'',1,1,now(),now())"), {"id": agent_id, "workspace": workspace_id})
            with connection.begin(): command.upgrade(config, "head")
            nodes = connection.execute(text("SELECT nodes FROM agent_workflows WHERE agent_id=:id"), {"id": agent_id}).scalar()
            assert nodes[0]["children"][0]["type"] == "send_message"
            connection.execute(text("UPDATE agent_workflows SET nodes='[]'::json WHERE agent_id=:id"), {"id": agent_id}); connection.commit()
            with connection.begin(): command.upgrade(config, "head")
            assert connection.execute(text("SELECT nodes FROM agent_workflows WHERE agent_id=:id"), {"id": agent_id}).scalar() == []
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
