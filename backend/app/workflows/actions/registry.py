"""Explicit allow-list: never import an action path supplied by a client."""
from importlib import import_module

from app.workflows.actions.jump.config import JumpConfig
from app.workflows.actions.jump.handler import execute as jump
from app.workflows.actions.send_message.config import SendMessageConfig
from app.workflows.actions.send_message.handler import execute as send_message
from app.workflows.actions.set_variable.config import SetVariableConfig
from app.workflows.actions.set_variable.handler import execute as set_variable

ACTION_TYPES = (
    "send_message", "send_attachment", "show_carousel", "ask_survey",
    "live_agent_handoff", "external_handoff", "ai_agent_handoff", "create_ticket",
    "jump", "branch", "set_variable", "switch_language", "close_chat",
    "send_api", "llm_processing", "auto_fill_field", "auto_team_match", "sql_query", "send_graph",
)
ACTIONS = {key: import_module("app.workflows.actions." + key).DEFINITION for key in ACTION_TYPES}


def config_for(action_type):
    return {"send_message": SendMessageConfig, "set_variable": SetVariableConfig, "jump": JumpConfig}[action_type]


def handler_for(action_type):
    return {"send_message": send_message, "set_variable": set_variable, "jump": jump}[action_type]
