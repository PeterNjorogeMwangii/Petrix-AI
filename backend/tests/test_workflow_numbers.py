import copy
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.models import WorkflowDocument
from app.workflows.numbering import walk
from test_workflows import workflow, node, message, intent, save, event, contents


def numbers(document):
    return {item["id"]: item["number"] for item in walk(document["nodes"])}


def test_defaults_keep_root_numbers_and_number_the_greeting(client, workflow, app):
    path, document, csrf = workflow
    assert [item["number"] for item in document["nodes"]] == [1, 2, 3, 4, 5]
    assert document["nodes"][0]["children"][0]["number"] == 6
    response = save(client, path, document, csrf)
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert numbers(response.json()) == numbers(document)
    with app.state.session_factory() as db:
        assert db.get(WorkflowDocument, uuid.UUID(document["agent_id"])).next_node_number == 7


def test_every_nested_node_is_numbered_and_keeps_number_when_moved_or_renamed(client, workflow):
    path, document, csrf = workflow
    flag = node("action", "set_variable", config={"variable_name": "flag", "chat_script": "True"})
    confirmation = intent("yes", [message("Confirmed")])
    question = message("Continue?", children=[confirmation, node("trigger", "fallback")])
    document["nodes"][0]["children"].extend([flag, question])
    document["nodes"].append(intent("help", [message("Help")]))
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    document = response.json()
    assigned = numbers(document)
    assert sorted(assigned.values()) == list(range(1, 14))
    assert assigned[flag["id"]] == 7 and assigned[question["id"]] == 8
    assert assigned[confirmation["id"]] == 9
    assert document["nodes"][-1]["number"] == 12  # Shared sequence, not a root row index.

    # Moving a root, reparenting a branch, renaming, and disabling retain identity.
    start = document["nodes"][0]
    start["children"][1]["children"] = [start["children"][2]["children"].pop(0)]
    start["children"][1]["name"] = "Saved flag"
    start["children"][1]["enabled"] = False
    start["children"][0], start["children"][2] = start["children"][2], start["children"][0]
    document["nodes"].insert(0, document["nodes"].pop())
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    assert numbers(response.json()) == assigned
    assert numbers(client.get(path + "/workflows").json()) == assigned


def test_deleted_numbers_are_not_reused_even_after_deleting_the_highest_step(client, workflow, app):
    path, document, csrf = workflow
    removed = message("Temporary", children=[message("Nested")])
    document["nodes"][0]["children"].append(removed)
    document = save(client, path, document, csrf).json()
    assert sorted(numbers(document).values()) == list(range(1, 9))
    document["nodes"][0]["children"].pop()
    document = save(client, path, document, csrf).json()
    # A reintroduced UUID is a new step after deletion and cannot reclaim its number.
    document["nodes"][0]["children"].append(removed)
    document = save(client, path, document, csrf).json()
    assert numbers(document)[removed["id"]] == 9
    assert sorted(numbers(document).values()) == [1, 2, 3, 4, 5, 6, 9, 10]
    with app.state.session_factory() as db:
        assert db.get(WorkflowDocument, uuid.UUID(document["agent_id"])).next_node_number == 11


def test_numbers_are_server_assigned_and_older_clients_can_omit_them(client, workflow):
    path, document, csrf = workflow
    original = numbers(document)
    for item in walk(document["nodes"]):
        item.pop("number")
    response = save(client, path, document, csrf)
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert numbers(response.json()) == original
    for item in walk(document["nodes"]):
        item["number"] = 999  # A client cannot renumber an existing step.
    response = save(client, path, document, csrf)
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert numbers(response.json()) == original
    first = message("First"); first["number"] = 1
    clone = copy.deepcopy(first); clone["id"] = str(uuid.uuid4())
    document["nodes"][0]["children"].extend([first, clone])
    response = save(client, path, document, csrf)
    assert response.status_code == 200, response.text
    assigned = numbers(response.json())
    assert assigned[first["id"]] == 7 and assigned[clone["id"]] == 8
    assert all(assigned[key] == value for key, value in original.items())


def test_rejected_and_stale_saves_do_not_consume_numbers(client, workflow):
    path, document, csrf = workflow
    stale = copy.deepcopy(document)
    document["nodes"][0]["children"].append(node("action", "send_api"))
    assert save(client, path, document, csrf).status_code == 422
    document["nodes"][0]["children"].pop()
    first = message("First")
    document["nodes"][0]["children"].append(first)
    response = save(client, path, document, csrf)
    assert response.status_code == 200 and numbers(response.json())[first["id"]] == 7
    second = message("Second")
    stale["nodes"][0]["children"].append(second)
    assert save(client, path, stale, csrf).status_code == 409
    current = client.get(path + "/workflows").json()
    current["nodes"][0]["children"].append(second)
    response = save(client, path, current, csrf)
    assert response.status_code == 200 and numbers(response.json())[second["id"]] == 8


def test_number_sequences_are_independent_for_each_agent(client, workflow):
    path, document, csrf = workflow
    document["nodes"][0]["children"].append(message("First agent"))
    assert max(numbers(save(client, path, document, csrf).json()).values()) == 7
    base = path.rsplit("/", 1)[0]
    created = client.post(base, data={"name": "Second agent", "default_language": "en"}, headers=csrf)
    assert created.status_code == 201, created.text
    second_path = base + "/" + created.json()["id"]
    second = client.get(second_path + "/workflows").json()
    assert sorted(numbers(second).values()) == [1, 2, 3, 4, 5, 6]
    second["nodes"][0]["children"].append(message("Second agent"))
    assert max(numbers(save(client, second_path, second, csrf).json()).values()) == 7


def test_tester_trace_uses_saved_numbers_for_nested_triggers_and_actions(client, workflow):
    path, document, csrf = workflow
    confirmation = intent("yes", [message("Confirmed")])
    document["nodes"][0]["children"].append(message("Continue?", children=[confirmation]))
    document = save(client, path, document, csrf).json()
    assigned = numbers(document)
    started = event(client, path, csrf)
    assert started.status_code == 200
    response = event(client, path, csrf, "message_received", started.json()["conversation_id"], content="yes")
    assert contents(response) == ["Confirmed"]
    for result in [started.json(), response.json()]:
        assert all(step["node_number"] == assigned[step["node_id"]] for step in result["trace"])


def test_0004_numbers_existing_custom_trees_once_without_resetting_them(database_engine):
    schema = "petrix_number_upgrade_" + uuid.uuid4().hex
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with database_engine.connect() as connection:
        original_schema = connection.execute(sa.text("SELECT current_schema()")).scalar()
        connection.execute(sa.text('CREATE SCHEMA "{}"'.format(schema))); connection.commit()
        try:
            connection.execute(sa.text('SET search_path TO "{}"'.format(schema))); connection.commit()
            config.attributes["connection"] = connection
            with connection.begin(): command.upgrade(config, "0002")
            workspace_id, agent_id, empty_agent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            with connection.begin():
                connection.execute(sa.text("INSERT INTO workspaces (id,name,created_at) VALUES (:id,'Existing',now())"), {"id": workspace_id})
                for identifier in [agent_id, empty_agent_id]:
                    connection.execute(sa.text("INSERT INTO ai_agents (id,workspace_id,name,llm_enabled,persona,languages,default_language,voice_enabled,voice_name,voice_speed,revision,created_at,updated_at) VALUES (:id,:workspace,'Existing agent',false,'','[\"en\"]','en',false,'',1,1,now(),now())"), {"id": identifier, "workspace": workspace_id})
            with connection.begin(): command.upgrade(config, "0003")
            workflows = sa.table("agent_workflows", sa.column("agent_id", sa.Uuid()), sa.column("nodes", sa.JSON()), sa.column("revision", sa.Integer()))
            nodes = connection.execute(sa.select(workflows.c.nodes).where(workflows.c.agent_id == agent_id)).scalar()
            nodes[0]["children"].insert(0, node("action", "set_variable", config={"variable_name": "company", "chat_script": '"PetrixBank"'}))
            nodes[0]["children"][1]["config"]["translations"]["en"]["messages"] = ["Welcome to **{company}**"]
            nodes.append(intent("help", [message("Saved help", children=[node("trigger", "fallback")])]))
            connection.execute(workflows.update().where(workflows.c.agent_id == agent_id).values(nodes=nodes, revision=9))
            connection.execute(workflows.update().where(workflows.c.agent_id == empty_agent_id).values(nodes=[]))
            updated_at = connection.execute(sa.text("SELECT updated_at FROM agent_workflows WHERE agent_id=:id"), {"id": agent_id}).scalar()
            connection.commit()
            original = copy.deepcopy(nodes)
            with connection.begin(): command.upgrade(config, "head")
            row = connection.execute(sa.text("SELECT nodes,revision,next_node_number,updated_at FROM agent_workflows WHERE agent_id=:id"), {"id": agent_id}).mappings().one()
            assert row["revision"] == 9 and row["updated_at"] == updated_at
            assert [item["number"] for item in row["nodes"]] == [1, 2, 3, 4, 5, 6]
            assert row["nodes"][0]["children"][0]["number"] == 7
            numbered = copy.deepcopy(row["nodes"])
            assert sorted(item["number"] for item in walk(numbered)) == list(range(1, 11))
            assert row["next_node_number"] == 11
            for item in walk(row["nodes"]): item.pop("number")
            assert row["nodes"] == original
            empty = connection.execute(sa.text("SELECT nodes,next_node_number FROM agent_workflows WHERE agent_id=:id"), {"id": empty_agent_id}).one()
            assert empty == ([], 1)
            connection.commit()
            with connection.begin(): command.upgrade(config, "head")
            assert connection.execute(sa.select(workflows.c.nodes).where(workflows.c.agent_id == agent_id)).scalar() == numbered
            connection.commit()
            with connection.begin(): command.downgrade(config, "0003")
            assert connection.execute(sa.select(workflows.c.nodes).where(workflows.c.agent_id == agent_id)).scalar() == original
            connection.commit()
        finally:
            connection.rollback()
            connection.execute(sa.text('SET search_path TO "{}"'.format(original_schema)))
            connection.execute(sa.text('DROP SCHEMA "{}" CASCADE'.format(schema))); connection.commit()
