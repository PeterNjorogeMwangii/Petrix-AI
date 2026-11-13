from copy import deepcopy
import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import uuid

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select, text
from starlette.datastructures import Headers, UploadFile

from app.models import AgentAttachment, AgentConversation, AgentMessage, User
from app.workflows.actions.ask_survey.config import AskSurveyConfig
from app.workflows.actions.ask_survey.handler import typed_answer
from app.workflows.attachments import read_uploads
from app.workflows.runtime import execute
from test_jump import setup_tree, prepare, run, texts, jump
from test_workflows import workflow, node, message, intent, save, event, contents, FakeLLM


def field(name="customer_name", type="text", **kwargs):
    return {"id": str(uuid.uuid4()), "question": "Question for " + name, "answer_type": type,
        "invalid_answer_reply": "Please provide a valid " + type + ".", "variable_name": name,
        "quick_answers": [], "enable_skip": False, **kwargs}


def survey(*fields, **kwargs):
    return node("action", "ask_survey", config={"fields": list(fields) or [field()]}, **kwargs)


@pytest.mark.parametrize("change", ["empty", "too_many", "blank_question", "blank_invalid", "bad_name", "reserved", "system_language", "unknown_type", "duplicate_id", "duplicate_name", "too_many_quick", "blank_quick", "duplicate_quick", "extra"])
def test_survey_configuration_rejects_invalid_fields(change):
    config = {"fields": [field()]}
    value = config["fields"][0]
    if change == "empty": config["fields"] = []
    elif change == "too_many": config["fields"] = [field("answer_"+str(index)) for index in range(51)]
    elif change == "blank_question": value["question"] = " "
    elif change == "blank_invalid": value["invalid_answer_reply"] = " "
    elif change == "bad_name": value["variable_name"] = "123-name"
    elif change == "reserved": value["variable_name"] = "agent_name"
    elif change == "system_language": value["variable_name"] = "language"
    elif change == "unknown_type": value["answer_type"] = "password"
    elif change == "duplicate_id": config["fields"].append({**field("other"), "id": value["id"]})
    elif change == "duplicate_name": config["fields"].append(field())
    elif change == "too_many_quick": value["quick_answers"] = [str(index) for index in range(14)]
    elif change == "blank_quick": value["quick_answers"] = [" "]
    elif change == "duplicate_quick": value["quick_answers"] = ["Yes", " Yes "]
    else: value["unknown_setting"] = True
    with pytest.raises(ValidationError): AskSurveyConfig.model_validate(config)


@pytest.mark.parametrize("type,answer,expected", [
    ("text", "  Chris  ", "Chris"), ("number", "0", 0), ("number", "-12", -12),
    ("number", "12.50", 12.5), ("number", ".25", .25), ("number", "1e3", 1000),
    ("email", "chris@EXAMPLE.COM", "chris@example.com"),
    ("phone", "+254 (712) 345-678", "+254712345678"), ("phone", "0712345678", "0712345678")])
def test_answers_are_normalized_to_the_expected_variable_type(type, answer, expected):
    actual = typed_answer(field(type=type), {"event": "message_received", "content": answer})
    assert actual == expected and actual.__class__ == expected.__class__


@pytest.mark.parametrize("type,answer", [("text", " "), ("number", "NaN"), ("number", "Infinity"),
    ("number", "1,000"), ("number", "1e99999"), ("number", "1e-99999"), ("number", "abc"),
    ("email", "not-an-email"), ("email", "a@localhost"), ("phone", "abc0712345678"),
    ("phone", "++254712345678"), ("phone", "123"), ("phone", "1234567890123456")])
def test_invalid_values_fail_without_coercing_a_different_answer(type, answer):
    with pytest.raises(ValueError): typed_answer(field(type=type), {"event": "message_received", "content": answer})


def test_survey_asks_one_field_at_a_time_then_resumes_children_and_siblings():
    agent, document = setup_tree()
    second = field("age", "number", question="How old are you, {customer_name}?")
    document.nodes[0]["children"] = [survey(field(), second, children=[message("Child: {age}")]), message("Done, {customer_name}")]
    prepare(agent, document)
    first = run(agent, document)
    assert texts(first) == ["Question for customer_name"] and first[0]["survey"]["field_index"] == 0
    next_question = run(agent, document, first[0], "message_received", "Chris")
    assert texts(next_question) == ["How old are you, Chris?"]
    assert next_question[0]["variables"] == {"customer_name": "Chris"}
    assert first[0]["survey"]["answer_token"] != next_question[0]["survey"]["answer_token"]
    finished = run(agent, document, next_question[0], "message_received", "31")
    assert texts(finished) == ["Child: 31", "Done, Chris"]
    assert finished[0]["variables"] == {"customer_name": "Chris", "age": 31}
    assert "survey" not in finished[0] and not finished[0]["stack"]


def test_invalid_answer_keeps_question_and_does_not_call_intent_matching():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field("email", "email", quick_answers=["chris@example.com"]))]
    document.nodes.append(intent("menu", [message("Should not interrupt")]))
    prepare(agent, document)
    first = run(agent, document)
    class ForbiddenMatcher:
        def choose(self, *args): raise AssertionError("Survey answers must not be classified as intents")
    invalid = execute(document, agent, first[0], {"event": "message_received", "content": "menu"}, matching=ForbiddenMatcher())
    assert texts(invalid) == ["Please provide a valid email."]
    assert invalid[0]["survey"] == first[0]["survey"] and not invalid[0].get("variables")
    assert invalid[1][0]["quick_replies"] == ["chris@example.com"]
    assert invalid[2][0]["outcome"] == "invalid_answer" and invalid[3]


def test_skip_is_opt_in_and_clears_an_older_variable_value():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field("email", "email", enable_skip=True), field("note"))]
    prepare(agent, document)
    first = run(agent, document, {"variables": {"email": "old@example.com"}})
    skipped = run(agent, document, first[0], "message_received", "  SKIP  ")
    assert skipped[0]["variables"]["email"] is None and skipped[0]["survey"]["field_index"] == 1
    # Without skip enabled, "skip" is a normal valid text answer.
    finished = run(agent, document, skipped[0], "message_received", "skip")
    assert finished[0]["variables"]["note"] == "skip" and "survey" not in finished[0]


def test_consecutive_survey_actions_do_not_reuse_the_last_answer():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field("first")), survey(field("second")), message("{first}/{second}")]
    prepare(agent, document)
    initial = run(agent, document)
    next_survey = run(agent, document, initial[0], "message_received", "A")
    assert texts(next_survey) == ["Question for second"] and next_survey[0]["variables"] == {"first": "A"}
    finished = run(agent, document, next_survey[0], "message_received", "B")
    assert texts(finished) == ["A/B"]


def test_survey_after_jump_can_return_to_a_menu_and_wait_for_another_event():
    agent, document = setup_tree()
    menu = message("Main menu")
    questionnaire = survey(field("name"))
    document.nodes[0]["children"] = [menu]
    menu["children"] = [intent("register", [questionnaire, message("Thanks {name}"), jump(menu)])]
    prepare(agent, document)
    first = run(agent, document)
    asked = run(agent, document, first[0], "message_received", "register")
    result = run(agent, document, asked[0], "message_received", "Chris")
    assert texts(result) == ["Thanks Chris", "Main menu"] and result[0]["stack"]
    again = run(agent, document, result[0], "message_received", "register")
    assert texts(again) == ["Question for name"]


@pytest.mark.parametrize("type", ["chat_closed", "chat_timeout", "ai_timeout"])
def test_lifecycle_events_interrupt_pending_surveys(type):
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field()), message("Abandoned")]
    target = next(node for node in document.nodes if node["type"] == type)
    target["children"] = [node("action", "set_variable", config={"variable_name": "lifecycle_handled", "chat_script": "True"})] if type == "chat_closed" else [message("Lifecycle handler")]
    prepare(agent, document)
    first = run(agent, document)
    result = run(agent, document, first[0], type)
    assert texts(result) == ([] if type == "chat_closed" else ["Lifecycle handler"]) and "survey" not in result[0]
    if type == "chat_closed": assert result[0]["closed"] and result[0]["variables"]["lifecycle_handled"] is True


def test_survey_final_answer_rolls_back_if_a_later_action_fails():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field()), message("{missing}")]
    prepare(agent, document)
    first = run(agent, document); before = deepcopy(first[0])
    with pytest.raises(HTTPException): run(agent, document, first[0], "message_received", "Chris")
    assert first[0] == before


def test_survey_completion_retains_trigger_entities_for_downstream_actions():
    agent, document = setup_tree()
    document.nodes[0]["children"] = [survey(field()), message("{system.intent.entities.city}")]
    prepare(agent, document)
    first = run(agent, document, {"intent": {"entities": {"city": "Nairobi"}}})
    result = execute(document, agent, first[0], {"event": "message_received", "content": "Chris"}, matching=SimpleNamespace(report={}))
    assert texts(result) == ["Nairobi"]


def start_survey(client, path, document, csrf, fields, tail=None):
    document["nodes"][0]["children"] = [survey(*fields)] + (tail or [])
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    first = event(client, path, csrf)
    assert first.status_code == 200, first.text
    return response.json(), first.json()


def answer_api(client, path, csrf, current, content, **kwargs):
    return event(client, path, csrf, "message_received", current["conversation_id"], content=content,
        survey_answer_token=current["survey"]["answer_token"], **kwargs)


def upload(client, path, csrf, current, files):
    return client.post(path + "/tester/attachments", headers=csrf,
        data={"conversation_id": current["conversation_id"], "survey_answer_token": current["survey"]["answer_token"]},
        files=[("files", item) for item in files])


def test_api_stores_regular_questions_answers_variables_and_blocks_llm(client, workflow, app):
    path, document, csrf = workflow
    llm = FakeLLM(); app.state.llm_tester = llm
    _, current = start_survey(client, path, document, csrf, [field("email", "email"), field("score", "number", enable_skip=True)], [message("Thank you {email}")])
    invalid = answer_api(client, path, csrf, current, "bad")
    assert contents(invalid) == ["Please provide a valid email."] and invalid.json()["survey"]["question_number"] == 1
    response = answer_api(client, path, csrf, invalid.json(), "chris@example.com")
    assert response.status_code == 200
    last = answer_api(client, path, csrf, response.json(), "skip")
    assert contents(last) == ["Thank you chris@example.com"] and last.json()["survey"] is None
    assert last.json()["variables"] == {"email": "chris@example.com", "score": None} and not llm.calls
    with app.state.session_factory() as db:
        rows = db.scalars(select(AgentMessage).where(AgentMessage.conversation_id == uuid.UUID(current["conversation_id"]))).all()
        assert sum(row.role == "user" for row in rows) == 3
        assert sum(row.role == "assistant" for row in rows) == 4


def test_api_tokens_reject_replayed_or_missing_answers_without_advancing(client, workflow):
    path, document, csrf = workflow
    _, first = start_survey(client, path, document, csrf, [field("first"), field("second")])
    response = answer_api(client, path, csrf, first, "A")
    assert response.status_code == 200
    assert answer_api(client, path, csrf, first, "Duplicate A").status_code == 409
    assert event(client, path, csrf, "message_received", first["conversation_id"], content="missing token").status_code == 409
    done = answer_api(client, path, csrf, response.json(), "B")
    assert done.status_code == 200 and done.json()["variables"] == {"first": "A", "second": "B"}


def test_revision_change_rejects_stale_survey_answers_and_restart_uses_new_questions(client, workflow):
    path, document, csrf = workflow
    document, first = start_survey(client, path, document, csrf, [field()])
    document["nodes"][0]["children"][0]["config"]["fields"][0]["question"] = "Updated question"
    assert save(client, path, document, csrf).status_code == 200
    assert answer_api(client, path, csrf, first, "Chris").status_code == 409
    assert contents(event(client, path, csrf)) == ["Updated question"]


def test_consecutive_actual_uploads_single_single_multiple_are_stored_and_downloadable(client, workflow, app):
    path, document, csrf = workflow
    _, first = start_survey(client, path, document, csrf, [field("front", "attachment"), field("back", "attachment"), field("documents", "attachments")], [message("Files received")])
    front = upload(client, path, csrf, first, [("../front.txt", b"FRONT", "text/plain")])
    assert front.status_code == 200, front.text
    assert front.json()["variables"]["front"]["name"] == "front.txt"
    assert front.json()["survey"]["question_number"] == 2
    assert upload(client, path, csrf, first, [("duplicate.txt", b"DUPLICATE", "text/plain")]).status_code == 409
    back = upload(client, path, csrf, front.json(), [("back.txt", b"BACK", "text/plain")])
    assert back.status_code == 200 and back.json()["survey"]["question_number"] == 3
    done = upload(client, path, csrf, back.json(), [("one.txt", b"ONE", "text/plain"), ("two.txt", b"TWO", "text/plain")])
    assert contents(done) == ["Files received"] and done.json()["survey"] is None
    values = done.json()["variables"]
    assert len(values["documents"]) == 2
    for file, expected in [(values["front"], b"FRONT"), (values["back"], b"BACK"), (values["documents"][0], b"ONE")]:
        download = client.get(file["url"])
        assert download.status_code == 200 and download.content == expected
        assert download.headers["content-disposition"].startswith("attachment;")
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 4
        row = db.scalar(select(AgentMessage).where(AgentMessage.content == "Uploaded: front.txt"))
        assert row.role == "user" and row.message_data["attachments"][0]["id"] == values["front"]["id"]
    client.cookies.clear()
    assert client.get(values["front"]["url"]).status_code == 401


def test_simulated_attachment_metadata_does_not_pass_a_real_upload_question(client, workflow):
    path, document, csrf = workflow
    _, current = start_survey(client, path, document, csrf, [field("file", "attachment")])
    response = event(client, path, csrf, "attachment_received", current["conversation_id"],
        attachment={"name": "fake.png", "media_type": "image"}, survey_answer_token=current["survey"]["answer_token"])
    assert contents(response) == ["Please provide a valid attachment."]
    assert response.json()["survey"]["answer_token"] == current["survey"]["answer_token"] and not response.json()["variables"]


def test_upload_validation_and_csrf_do_not_advance_the_survey(client, workflow, app):
    path, document, csrf = workflow
    _, current = start_survey(client, path, document, csrf, [field("file", "attachment")])
    assert upload(client, path, {}, current, [("a.txt", b"A", "text/plain")]).status_code == 403
    assert upload(client, path, csrf, current, [("empty.txt", b"", "text/plain")]).status_code == 422
    assert upload(client, path, csrf, current, [("a.txt", b"A", "text/plain"), ("b.txt", b"B", "text/plain")]).status_code == 422
    other_path = path.replace(path.split("/")[-1], str(uuid.uuid4()))
    assert upload(client, other_path, csrf, current, [("a.txt", b"A", "text/plain")]).status_code == 404
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 0
        assert db.get(AgentConversation, uuid.UUID(current["conversation_id"])).workflow_state["survey"]["answer_token"] == current["survey"]["answer_token"]


def test_failed_upload_turn_rolls_back_file_bytes_messages_and_survey_variable(client, workflow, app):
    path, document, csrf = workflow
    _, current = start_survey(client, path, document, csrf, [field("file", "attachment")], [message("Never saved"), message("{missing}")])
    response = upload(client, path, csrf, current, [("a.txt", b"A", "text/plain")])
    assert response.status_code == 422
    with app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AgentAttachment)) == 0
        state = db.get(AgentConversation, uuid.UUID(current["conversation_id"])).workflow_state
        assert state["survey"]["answer_token"] == current["survey"]["answer_token"] and not state.get("variables")
        assert db.scalar(select(func.count()).select_from(AgentMessage).where(AgentMessage.role == "user")) == 0


def test_file_download_is_isolated_to_the_conversation_user(client, workflow, app, signed_in):
    path, document, csrf = workflow
    _, current = start_survey(client, path, document, csrf, [field("file", "attachment")])
    response = upload(client, path, csrf, current, [("a.txt", b"A", "text/plain")]).json()
    with app.state.session_factory() as db:
        other = User(email="other-file-owner@example.com"); db.add(other); db.flush()
        conversation = db.get(AgentConversation, uuid.UUID(current["conversation_id"]))
        conversation.user_id = other.id; db.commit()
    assert client.get(response["variables"]["file"]["url"]).status_code == 404


@pytest.mark.parametrize("sizes,code", [([0],422), ([10485761],413), ([7340032,7340032,7340032],413), ([1]*11,422)])
def test_upload_byte_and_batch_limits(sizes, code):
    uploads = [UploadFile(BytesIO(b"x"*size), filename="file.txt", headers=Headers({"content-type":"text/plain"})) for size in sizes]
    with pytest.raises(HTTPException) as error: asyncio.run(read_uploads(uploads))
    assert error.value.status_code == code


def test_0005_preserves_existing_workflows_and_adds_only_file_storage(database_engine):
    schema = "petrix_survey_upgrade_" + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1]/"alembic.ini"))
    with database_engine.connect() as connection:
        original = connection.execute(text("SELECT current_schema()")).scalar()
        connection.execute(text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes["connection"] = connection
            with connection.begin(): command.upgrade(config,"0004")
            workspace_id, agent_id = uuid.uuid4(), uuid.uuid4()
            connection.execute(text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {"id":workspace_id})
            connection.execute(text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing',false,'','[\"en\"]','en',false,'',1,1,now(),now())"), {"id":agent_id,"workspace":workspace_id})
            connection.execute(text("INSERT INTO agent_workflows (agent_id,revision,next_node_number,nodes,updated_at) VALUES (:id,7,42,'[]',now())"), {"id":agent_id}); connection.commit()
            with connection.begin(): command.upgrade(config,"head")
            assert connection.execute(text("SELECT revision,next_node_number,nodes FROM agent_workflows WHERE agent_id=:id"), {"id":agent_id}).one() == (7,42,[])
            assert connection.execute(text("SELECT count(*) FROM agent_attachments")).scalar() == 0
            connection.commit()
            with connection.begin(): command.downgrade(config,"0004")
            assert connection.execute(text("SELECT revision FROM agent_workflows WHERE agent_id=:id"), {"id":agent_id}).scalar() == 7
            connection.commit()
        finally:
            connection.rollback(); connection.execute(text('SET search_path TO "{}"'.format(original)))
            connection.execute(text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
