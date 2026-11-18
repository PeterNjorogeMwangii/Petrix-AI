import json

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.security import utcnow
from app.models import AgentChange, WorkflowDocument
from app.workflows.actions.registry import ACTIONS, config_for
from app.workflows.defaults import SYSTEM_TRIGGERS, default_nodes, node_id
from app.workflows.numbering import assign_numbers, walk
from app.workflows.paths import PATH_ACTIONS, path_ids
from app.workflows.schemas import AttachmentTriggerConfig, EmptyConfig, MessageTriggerConfig

TRIGGERS = [
    {"type": "message_received", "name": "Message received", "description": "Match a message against intent examples.", "top_level_only": False, "permanent": False},
    {"type": "attachment_received", "name": "Attachment received", "description": "Match an image, audio, video, or file event.", "top_level_only": False, "permanent": False},
    {"type": "fallback", "name": "Fallback", "description": "Run when no other trigger at this level matches.", "top_level_only": False, "permanent": False},
    {"type": "collect_feedback", "name": "Collect feedback", "description": "Run when a closed chat submits requested customer feedback.", "top_level_only": False, "permanent": False},
] + [{"type": value, "name": value.replace("_", " ").capitalize(), "description": "Run post-conversation automation when a chat is closed." if value == "chat_closed" else "A permanent conversation event.",
    "top_level_only": True, "permanent": True} for value in SYSTEM_TRIGGERS]
TRIGGER_TYPES = {item["type"] for item in TRIGGERS}


def initialize(db, agent):
    nodes = default_nodes(agent.id, agent.default_language)
    document = WorkflowDocument(agent_id=agent.id, revision=1, nodes=nodes,
        next_node_number=max(node["number"] for node in walk(nodes)) + 1)
    db.add(document)
    return document


def get_document(db, agent_id, lock=False):
    query = select(WorkflowDocument).where(WorkflowDocument.agent_id == agent_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    document = db.scalar(query)
    if not document:
        raise HTTPException(409, "Workflow storage is missing. Run python -m alembic upgrade head from backend, then retry.")
    return document


def payload(document):
    return {"agent_id": str(document.agent_id), "revision": document.revision, "nodes": document.nodes, "updated_at": document.updated_at.isoformat()}


def validate_nodes(nodes, agent_id):
    if len(json.dumps(nodes, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        raise HTTPException(422, "Keep the workflow definition below 1 MB.")
    seen = set()
    node_count = 0
    permanent = set()

    def visit(siblings, depth):
        nonlocal node_count
        if depth > 12:
            raise HTTPException(422, "Workflows support up to 12 nested levels.")
        fallbacks = 0
        encountered_trigger = False
        for node in siblings:
            if node["id"] in seen:
                raise HTTPException(422, "Each workflow node must have a unique ID.")
            seen.add(node["id"])
            node_count += 1
            if node_count > 500:
                raise HTTPException(422, "An agent can have up to 500 workflow nodes.")
            if depth == 0 and node["kind"] != "trigger":
                raise HTTPException(422, "Top-level workflows must begin with a trigger.")
            try:
                if node["kind"] == "trigger":
                    encountered_trigger = True
                    if node["type"] not in TRIGGER_TYPES:
                        raise HTTPException(422, "Choose a supported trigger.")
                    if node["type"] in SYSTEM_TRIGGERS:
                        if depth != 0 or not node["enabled"] or node["id"] != node_id(agent_id, node["type"]) or node["type"] in permanent:
                            raise HTTPException(422, "System triggers are permanent, always enabled, and top-level only.")
                        permanent.add(node["type"])
                    if node["type"] == "fallback":
                        fallbacks += 1
                    model = MessageTriggerConfig if node["type"] == "message_received" else AttachmentTriggerConfig if node["type"] == "attachment_received" else EmptyConfig
                    node["config"] = model.model_validate(node["config"]).model_dump()
                    if node["type"] == "message_received" and node["enabled"] and not any(node["config"]["examples"].values()):
                        raise HTTPException(422, "Add at least one intent example before enabling a Message received trigger.")
                else:
                    if encountered_trigger:
                        raise HTTPException(422, "Place actions before the alternative nested triggers at the same level.")
                    if node["type"] not in ACTIONS or not ACTIONS[node["type"]]["available"]:
                        raise HTTPException(422, "This action will be configured in a later milestone.")
                    node["config"] = config_for(node["type"]).model_validate(node["config"]).model_dump(mode="json")
            except ValidationError as error:
                if node["type"] in {"set_variable", "jump", "ask_survey", "branch", "send_api", "sql_query", "switch_language", "send_attachment", "show_carousel", "collect_feedback", "close_chat", "llm_processing", "auto_fill_field", "auto_team_match", "send_graph", "live_agent_handoff", "external_handoff", "ai_agent_handoff", "create_ticket"}:
                    issue = error.errors(include_url=False, include_context=False, include_input=False)[0]
                    field = ".".join(map(str, issue["loc"]))
                    location = ", " + field if field else ""
                    raise HTTPException(422, "Step '{}'{}: {}".format(node["name"], location, issue["msg"].removeprefix("Value error, "))) from None
                raise HTTPException(422, "Check the settings for '{}'.".format(node["name"])) from None
            if node["kind"] == "action" and node["type"] in PATH_ACTIONS:
                if node["children"]:
                    raise HTTPException(422, "Step '{}': Put nested steps inside one of this action's paths.".format(node["name"]))
                expected_paths = path_ids(node)
                paths = node.get("paths", [])
                if len(paths) != len(expected_paths) or {path["id"] for path in paths} != set(expected_paths):
                    raise HTTPException(422, "Step '{}': Provide exactly one action path for every configured outcome.".format(node["name"]))
                by_id = {path["id"]: path for path in paths}
                node["paths"] = [by_id[path_id] for path_id in expected_paths]
                for path in node["paths"]:
                    if path["id"] in seen:
                        raise HTTPException(422, "Each workflow node and action path must have a unique ID.")
                    seen.add(path["id"])
                    visit(path["children"], depth + 1)
            else:
                if node.get("paths"):
                    raise HTTPException(422, "Only Branch, Send API, SQL Query, LLM Processing, Auto Fill Field, Auto Team Match, and Live Agent Handoff actions can contain outcome paths.")
                if node['type'] in {'external_handoff','ai_agent_handoff'} and node['children']:
                    raise HTTPException(422, "Handoff actions cannot contain follow-up actions on the originating agent.")
                # Keep older workflow documents byte-for-byte stable on no-op saves.
                node.pop("paths", None)
                visit(node["children"], depth + 1)
        if fallbacks > 1:
            raise HTTPException(422, "Keep one fallback trigger per level.")

    visit(nodes, 0)
    if permanent != set(SYSTEM_TRIGGERS):
        raise HTTPException(422, "The four permanent conversation triggers must remain at the top level.")
    # Resolve after visiting the entire tree so forward and cross-root targets work.
    index = {node["id"]: node for node in walk(nodes)}
    enabled = {}

    def collect_enabled(values, ancestors_enabled=True):
        for node in values:
            enabled[node["id"]] = ancestors_enabled and node["enabled"]
            collect_enabled(node["children"], enabled[node["id"]])
            for path in node.get("paths", []):
                collect_enabled(path["children"], enabled[node["id"]])

    collect_enabled(nodes)
    for node in index.values():
        if node["kind"] != "action" or node["type"] != "jump":
            continue
        target_id = node["config"]["target_node_id"]
        if target_id == node["id"]:
            raise HTTPException(422, "Step '{}': A Jump cannot target itself.".format(node["name"]))
        if target_id not in index:
            raise HTTPException(422, "Step '{}': The Jump target must exist in this agent's workflow. Update or remove the Jump before deleting its target.".format(node["name"]))
        if enabled[node["id"]] and not enabled[target_id]:
            raise HTTPException(422, "Step '{}': Enable the Jump target and its parent steps, or disable this Jump first.".format(node["name"]))
    from app.workflows.post_close import validate_post_close
    validate_post_close(nodes)
    return nodes


def save(db, agent, actor_id, body):
    document = get_document(db, agent.id, lock=True)
    if document.revision != body.expected_revision:
        raise HTTPException(409, "Workflows changed in another session. Reload workflows before saving.")
    nodes = validate_nodes([node.model_dump(mode="json") for node in body.nodes], agent.id)
    from app.custom_fields.service import validate_references as validate_field_references
    validate_field_references(db, agent.workspace_id, nodes)
    from app.inbox.service import validate_references as validate_handoff_references
    validate_handoff_references(db, agent.workspace_id, nodes)
    from app.external_handoff.service import validate_references as validate_external_references
    validate_external_references(db, agent.workspace_id, nodes)
    from app.ai_network.service import validate_references as validate_ai_references
    validate_ai_references(db, agent, nodes)
    from app.tickets.service import validate_references as validate_ticket_references
    validate_ticket_references(db, agent.workspace_id, nodes)
    from app.database_connections.service import validate_references
    validate_references(db, agent.workspace_id, nodes)
    from app.workflows.actions.switch_language.validation import validate_targets
    validate_targets(nodes, agent.languages)
    from app.workflows.assets import validate_references as validate_assets
    validate_assets(db, agent, nodes)
    next_number = assign_numbers(nodes, document.nodes, document.next_node_number)
    if nodes != document.nodes:
        document.nodes = nodes
        document.next_node_number = next_number
        document.revision += 1
        document.updated_at = utcnow()
        db.add(AgentChange(workspace_id=agent.workspace_id, agent_id=agent.id, agent_name=agent.name, actor_id=actor_id,
            action="workflows_saved", summary="Workflow revision {} saved.".format(document.revision)))
    db.commit()
    return payload(document)
